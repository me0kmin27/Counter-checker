from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def migrate_legacy_mail_schema(engine: Engine) -> None:
    """Add columns required by the Python app to installations made by the PHP MVP."""
    inspector = inspect(engine)
    if "pop_accounts" not in inspector.get_table_names():
        return

    dialect = engine.dialect.name
    binary_type = "LONGBLOB" if dialect == "mysql" else "BLOB"
    boolean_type = "BOOLEAN" if dialect in {"mysql", "sqlite"} else "BOOLEAN"

    tables = {
        "pop_accounts": {column["name"] for column in inspector.get_columns("pop_accounts")},
        "email_messages": {column["name"] for column in inspector.get_columns("email_messages")},
        "attachments": {column["name"] for column in inspector.get_columns("attachments")},
    }
    statements: list[str] = []
    legacy_password = "password_cipher" in tables["pop_accounts"]
    if "encrypted_password" not in tables["pop_accounts"]:
        statements.append(f"ALTER TABLE pop_accounts ADD COLUMN encrypted_password {binary_type} NULL")
    if "use_ssl" not in tables["pop_accounts"]:
        statements.append(
            f"ALTER TABLE pop_accounts ADD COLUMN use_ssl {boolean_type} NOT NULL DEFAULT 1"
        )
        if "use_tls" in tables["pop_accounts"]:
            statements.append("UPDATE pop_accounts SET use_ssl = use_tls")
    if "raw_message" not in tables["email_messages"]:
        statements.append(f"ALTER TABLE email_messages ADD COLUMN raw_message {binary_type} NULL")
    if "content" not in tables["attachments"]:
        statements.append(f"ALTER TABLE attachments ADD COLUMN content {binary_type} NULL")

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        if legacy_password:
            password_value = "CAST(password_cipher AS BLOB)" if dialect == "sqlite" else "password_cipher"
            connection.execute(text(
                f"UPDATE pop_accounts SET encrypted_password = {password_value} "
                "WHERE encrypted_password IS NULL"
            ))
