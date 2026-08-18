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
    additions = {
        "pop_require_spa": "BOOLEAN NOT NULL DEFAULT 0",
        "smtp_host": "VARCHAR(255)", "smtp_port": "INTEGER NOT NULL DEFAULT 587",
        "smtp_security_mode": "VARCHAR(20) NOT NULL DEFAULT 'auto'",
        "smtp_timeout": "INTEGER NOT NULL DEFAULT 30",
        "smtp_require_spa": "BOOLEAN NOT NULL DEFAULT 0",
        "smtp_auth_required": "BOOLEAN NOT NULL DEFAULT 0",
        "smtp_auth_method": "VARCHAR(30) NOT NULL DEFAULT 'same_as_pop'",
        "smtp_username": "VARCHAR(255)", "encrypted_smtp_password": "BLOB",
        "filter_mode": "VARCHAR(10) NOT NULL DEFAULT 'all'",
        "filter_sender": "VARCHAR(998)", "filter_recipient": "VARCHAR(998)",
        "filter_subject": "VARCHAR(998)", "filter_keyword": "TEXT",
        "filter_date_from": "DATETIME", "filter_date_to": "DATETIME",
    }
    columns = {column["name"] for column in inspect(engine).get_columns("pop_accounts")}
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE pop_accounts ADD COLUMN {name} {definition}"))
        if engine.dialect.name in {"mysql", "mariadb"}:
            # SQLAlchemy LargeBinary maps to BLOB (64 KiB) by default. Messages
            # and attachments can be up to 25 MiB, so existing installations
            # must be widened as well as new schemas using LONGBLOB.
            message_columns = {
                column["name"]: str(column["type"]).upper()
                for column in inspect(engine).get_columns("email_messages")
            }
            if message_columns.get("raw_message") != "LONGBLOB":
                connection.execute(text(
                    "ALTER TABLE email_messages MODIFY raw_message LONGBLOB NOT NULL"
                ))
            body_changes = [
                f"MODIFY {name} LONGTEXT NOT NULL"
                for name in ("text_body", "html_body")
                if message_columns.get(name) != "LONGTEXT"
            ]
            if body_changes:
                connection.execute(text(
                    f"ALTER TABLE email_messages {', '.join(body_changes)}"
                ))
            attachment_columns = {
                column["name"]: str(column["type"]).upper()
                for column in inspect(engine).get_columns("attachments")
            }
            if attachment_columns.get("content") != "LONGBLOB":
                connection.execute(text(
                    "ALTER TABLE attachments MODIFY content LONGBLOB NOT NULL"
                ))
