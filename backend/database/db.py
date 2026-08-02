import json
import logging
import os
from datetime import datetime, timedelta

import bcrypt as _bcrypt
from sqlalchemy import and_, cast, extract, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Alert, Base, DetectionLog, User

logger = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker | None = None

# All date/hour bucketing for charts uses this offset so grouping happens in
# WIB (UTC+7) wall-clock time even though `timestamp` is stored in UTC.
_WIB_OFFSET = "+7 hours"


def _db_url() -> str:
    path = os.getenv("DB_PATH", "backend/data/malioboro.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return f"sqlite+aiosqlite:///{path}"


async def init_db() -> None:
    global _engine, _session_factory

    _engine = create_async_engine(_db_url(), echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _migrate_schema()
    await _seed_admin()
    logger.info("Database initialised.")


async def _migrate_schema() -> None:
    """Idempotent ALTER TABLEs for columns added after the initial release.

    `Base.metadata.create_all` only creates missing tables, not missing
    columns on tables that already exist, so a column added later (like
    peak_count) needs an explicit, safe-to-rerun ALTER TABLE here.
    """
    async with _engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(detection_logs)"))
        columns = {row[1] for row in result.fetchall()}
        if "peak_count" not in columns:
            await conn.execute(
                text("ALTER TABLE detection_logs ADD COLUMN peak_count INTEGER DEFAULT 0")
            )
            logger.info("Migrated detection_logs: added peak_count column.")
        if "people_peak" not in columns:
            await conn.execute(
                text("ALTER TABLE detection_logs ADD COLUMN people_peak INTEGER DEFAULT 0")
            )
            logger.info("Migrated detection_logs: added people_peak column.")

        alert_columns = {
            row[1] for row in (await conn.execute(text("PRAGMA table_info(alerts)"))).fetchall()
        }
        for col, ddl in (
            ("trigger_value", "INTEGER DEFAULT 0"),
            ("image_path", "TEXT"),
            ("detections", "TEXT"),
        ):
            if col not in alert_columns:
                await conn.execute(text(f"ALTER TABLE alerts ADD COLUMN {col} {ddl}"))
                logger.info(f"Migrated alerts: added {col} column.")


async def _seed_admin() -> None:
    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD", "changeme123")

    async with _session_factory() as session:
        result = await session.execute(select(func.count(User.id)))
        if result.scalar() == 0:
            hashed = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
            user = User(username=username, hashed_password=hashed)
            session.add(user)
            await session.commit()
            logger.info(f"Admin user '{username}' seeded.")


def get_session() -> AsyncSession:
    """Return a new async session. Use as an async context manager."""
    return _session_factory()


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


async def save_detection_log(
    camera_id: int, counts_dict: dict, peak_count: int = 0, people_peak: int = 0
) -> None:
    """`counts_dict` keys are the Indonesian class names produced by
    detector.get_counts() (TARGET_CLASSES), not English ones."""
    total = sum(counts_dict.values())
    async with _session_factory() as session:
        log = DetectionLog(
            camera_id=camera_id,
            timestamp=datetime.utcnow(),
            people_count=counts_dict.get("orang", 0),
            bicycle_count=counts_dict.get("sepeda", 0),
            motorcycle_count=counts_dict.get("motor", 0),
            bajaj_count=counts_dict.get("bajaj", 0),
            becak_count=counts_dict.get("becak", 0),
            andong_count=counts_dict.get("andong", 0),
            car_count=counts_dict.get("mobil", 0),
            bus_count=counts_dict.get("bus", 0),
            truck_count=counts_dict.get("truk", 0),
            total_count=total,
            peak_count=peak_count,
            people_peak=people_peak,
        )
        session.add(log)
        await session.commit()


async def save_alert(
    camera_id: int,
    alert_type: str,
    description: str = "",
    trigger_value: int = 0,
    image_path: str | None = None,
    detections: list | None = None,
) -> None:
    async with _session_factory() as session:
        alert = Alert(
            camera_id=camera_id,
            timestamp=datetime.utcnow(),
            alert_type=alert_type,
            description=description,
            trigger_value=trigger_value,
            image_path=image_path,
            detections=json.dumps(detections) if detections is not None else None,
        )
        session.add(alert)
        await session.commit()


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def get_history(camera_id: int, date_str: str) -> list[dict]:
    """All detection logs for a camera on a given date (YYYY-MM-DD)."""
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    async with _session_factory() as session:
        result = await session.execute(
            select(DetectionLog)
            .where(
                and_(
                    DetectionLog.camera_id == camera_id,
                    cast(DetectionLog.timestamp, _date_type()).label("d") == target,
                )
            )
            .order_by(DetectionLog.timestamp)
        )
        return [_log_to_dict(row) for row in result.scalars()]


async def get_weekly_trend(camera_id: int, days: int = 7) -> list[dict]:
    """Per-WIB-calendar-day avg/peak people_count for a camera over the last
    `days` calendar days (WIB), including today. People only (pedestrians),
    not the all-classes total — this feeds the LOS/crowd-alert dashboard.
    One entry per day, in order; days with no rows have avg/peak set to None
    instead of being omitted, so the chart doesn't connect across gaps."""
    wib_today = (datetime.utcnow() + timedelta(hours=7)).date()
    start_date = wib_today - timedelta(days=days - 1)

    async with _session_factory() as session:
        date_col = func.date(DetectionLog.timestamp, _WIB_OFFSET).label("date")
        result = await session.execute(
            select(
                date_col,
                func.avg(DetectionLog.people_count).label("avg_count"),
                func.max(DetectionLog.people_peak).label("peak_count"),
            )
            .where(
                and_(
                    DetectionLog.camera_id == camera_id,
                    date_col >= str(start_date),
                    date_col <= str(wib_today),
                )
            )
            .group_by(date_col)
        )
        by_date = {row.date: row for row in result}

        out = []
        for i in range(days):
            d = str(start_date + timedelta(days=i))
            row = by_date.get(d)
            out.append(
                {
                    "date": d,
                    "avg": round(row.avg_count, 1) if row else None,
                    "peak": int(row.peak_count) if row and row.peak_count is not None else None,
                }
            )
        return out


async def get_hourly_trend(camera_id: int, date_str: str) -> list[dict]:
    """Per-WIB-hour avg/peak people_count for a camera on a given WIB
    calendar date (YYYY-MM-DD). People only (pedestrians), not the
    all-classes total — this feeds the LOS/crowd-alert dashboard. Always
    returns 24 entries (hour 0-23); hours with no rows have avg/peak set to
    None instead of being omitted."""
    async with _session_factory() as session:
        date_col = func.date(DetectionLog.timestamp, _WIB_OFFSET)
        hour_col = func.strftime("%H", DetectionLog.timestamp, _WIB_OFFSET).label("hour")
        result = await session.execute(
            select(
                hour_col,
                func.avg(DetectionLog.people_count).label("avg_count"),
                func.max(DetectionLog.people_peak).label("peak_count"),
            )
            .where(
                and_(
                    DetectionLog.camera_id == camera_id,
                    date_col == date_str,
                )
            )
            .group_by(hour_col)
        )
        by_hour = {int(row.hour): row for row in result}

        return [
            {
                "hour": h,
                "avg": round(by_hour[h].avg_count, 1) if h in by_hour else None,
                "peak": int(by_hour[h].peak_count)
                if h in by_hour and by_hour[h].peak_count is not None
                else None,
            }
            for h in range(24)
        ]


async def get_alerts(
    camera_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    unread_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List alerts, newest first. `start_date`/`end_date` are WIB calendar
    dates (YYYY-MM-DD, inclusive), matching the WIB bucketing used by the
    crowd-density charts. `unread_only` defaults to True so the existing
    notification-bell caller (GET /alerts with no extra params) keeps its
    original behaviour unchanged."""
    async with _session_factory() as session:
        conditions = []
        if camera_id is not None:
            conditions.append(Alert.camera_id == camera_id)
        if unread_only:
            conditions.append(Alert.is_read == False)  # noqa: E712
        if start_date:
            conditions.append(func.date(Alert.timestamp, _WIB_OFFSET) >= start_date)
        if end_date:
            conditions.append(func.date(Alert.timestamp, _WIB_OFFSET) <= end_date)

        query = select(Alert).order_by(Alert.timestamp.desc()).limit(limit).offset(offset)
        if conditions:
            query = query.where(and_(*conditions))

        result = await session.execute(query)
        return [_alert_to_dict(row) for row in result.scalars()]


async def get_alert_by_id(alert_id: int) -> dict | None:
    async with _session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.id == alert_id))
        alert = result.scalar_one_or_none()
        return _alert_to_dict(alert) if alert else None


async def cleanup_old_alerts(retention_days: int) -> list[str]:
    """Delete alert rows older than `retention_days` (by UTC timestamp).
    Returns the image_path of every deleted row that had one, so the caller
    can remove the matching files from disk."""
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    async with _session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.timestamp < cutoff))
        old_alerts = result.scalars().all()
        image_paths = [a.image_path for a in old_alerts if a.image_path]
        for a in old_alerts:
            await session.delete(a)
        await session.commit()
        return image_paths


async def mark_alert_read(alert_id: int) -> None:
    async with _session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.id == alert_id))
        alert = result.scalar_one_or_none()
        if alert:
            alert.is_read = True
            await session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _date_type():
    """SQLAlchemy Date type for casting — imported lazily to avoid circular."""
    from sqlalchemy import Date
    return Date


def _log_to_dict(log: DetectionLog) -> dict:
    return {
        "id": log.id,
        "camera_id": log.camera_id,
        "timestamp": log.timestamp.isoformat(),
        "people_count": log.people_count,
        "bicycle_count": log.bicycle_count,
        "motorcycle_count": log.motorcycle_count,
        "bajaj_count": log.bajaj_count,
        "becak_count": log.becak_count,
        "andong_count": log.andong_count,
        "car_count": log.car_count,
        "bus_count": log.bus_count,
        "truck_count": log.truck_count,
        "total_count": log.total_count,
    }


def _alert_to_dict(alert: Alert) -> dict:
    return {
        "id": alert.id,
        "camera_id": alert.camera_id,
        "timestamp": alert.timestamp.isoformat(),
        "alert_type": alert.alert_type,
        "description": alert.description,
        "is_read": alert.is_read,
        "trigger_value": alert.trigger_value,
        "image_path": alert.image_path,
        "detections": json.loads(alert.detections) if alert.detections else [],
    }
