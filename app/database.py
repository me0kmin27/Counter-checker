import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./counter_checker.db")
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as session:
        yield session


def ensure_compatibility_schema():
    """Add columns introduced after the initial create_all-only deployment."""
    if "pop_accounts" not in inspect(engine).get_table_names():
        return
    columns = {column["name"] for column in inspect(engine).get_columns("pop_accounts")}
    if "security_mode" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE pop_accounts ADD COLUMN security_mode VARCHAR(20)"
            ))
            connection.execute(text(
                "UPDATE pop_accounts SET security_mode = "
                "CASE WHEN use_ssl THEN 'ssl' ELSE 'starttls' END"
            ))
