from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class DetectionLog(Base):
    __tablename__ = "detection_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    people_count = Column(Integer, default=0)
    bicycle_count = Column(Integer, default=0)
    motorcycle_count = Column(Integer, default=0)
    bajaj_count = Column(Integer, default=0)
    becak_count = Column(Integer, default=0)
    andong_count = Column(Integer, default=0)
    car_count = Column(Integer, default=0)
    bus_count = Column(Integer, default=0)
    truck_count = Column(Integer, default=0)
    total_count = Column(Integer, default=0)


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera_id = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    alert_type = Column(String, nullable=False)  # 'HIGH_CROWD' or 'HIGH_TRAFFIC'
    description = Column(Text)
    is_read = Column(Boolean, default=False)
