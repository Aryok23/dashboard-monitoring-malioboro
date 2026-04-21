import logging
import os
from datetime import datetime, timedelta

import bcrypt as _bcrypt
from sqlalchemy import and_, cast, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Alert, Base, DetectionLog, User

logger = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker | None = None


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

    await _seed_admin()
    logger.info("Database initialised.")


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


async def save_detection_log(camera_id: int, counts_dict: dict) -> None:
    total = sum(counts_dict.values())
    async with _session_factory() as session:
        log = DetectionLog(
            camera_id=camera_id,
            timestamp=datetime.utcnow(),
            people_count=counts_dict.get("people", 0),
            bicycle_count=counts_dict.get("bicycle", 0),
            motorcycle_count=counts_dict.get("motorcycle", 0),
            bajaj_count=counts_dict.get("bajaj", 0),
            becak_count=counts_dict.get("becak", 0),
            andong_count=counts_dict.get("andong", 0),
            car_count=counts_dict.get("car", 0),
            bus_count=counts_dict.get("bus", 0),
            truck_count=counts_dict.get("truck", 0),
            total_count=total,
        )
        session.add(log)
        await session.commit()


async def save_alert(camera_id: int, alert_type: str, description: str = "") -> None:
    async with _session_factory() as session:
        alert = Alert(
            camera_id=camera_id,
            timestamp=datetime.utcnow(),
            alert_type=alert_type,
            description=description,
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


async def get_summary(camera_id: int, days: int) -> list[dict]:
    """Daily aggregated total_count for a camera over the last N days."""
    since = datetime.utcnow() - timedelta(days=days)
    async with _session_factory() as session:
        date_col = func.date(DetectionLog.timestamp).label("date")
        result = await session.execute(
            select(date_col, func.sum(DetectionLog.total_count).label("total"))
            .where(
                and_(
                    DetectionLog.camera_id == camera_id,
                    DetectionLog.timestamp >= since,
                )
            )
            .group_by(date_col)
            .order_by(date_col)
        )
        return [{"date": str(row.date), "total": row.total or 0} for row in result]


async def get_heatmap(date_str: str) -> list[dict]:
    """Average total_count per camera per hour for a given date."""
    async with _session_factory() as session:
        date_col = func.date(DetectionLog.timestamp).label("date")
        hour_col = func.strftime("%H", DetectionLog.timestamp).label("hour")
        result = await session.execute(
            select(
                DetectionLog.camera_id,
                hour_col,
                func.avg(DetectionLog.total_count).label("avg_count"),
            )
            .where(date_col == date_str)
            .group_by(DetectionLog.camera_id, hour_col)
            .order_by(DetectionLog.camera_id, hour_col)
        )
        return [
            {
                "camera_id": row.camera_id,
                "hour": int(row.hour),
                "avg_count": round(row.avg_count or 0, 1),
            }
            for row in result
        ]


async def get_unread_alerts(limit: int = 50) -> list[dict]:
    async with _session_factory() as session:
        result = await session.execute(
            select(Alert)
            .where(Alert.is_read == False)  # noqa: E712
            .order_by(Alert.timestamp.desc())
            .limit(limit)
        )
        return [_alert_to_dict(row) for row in result.scalars()]


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
    }
