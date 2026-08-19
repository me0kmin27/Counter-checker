from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index,
    Integer, JSON, LargeBinary, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.mysql import LONGBLOB, LONGTEXT

from .database import Base


LONG_BINARY = LargeBinary().with_variant(LONGBLOB(), "mysql")
LONG_TEXT = Text().with_variant(LONGTEXT(), "mysql")


class PopAccount(Base):
    __tablename__ = "pop_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=995)
    username: Mapped[str] = mapped_column(String(255))
    encrypted_password: Mapped[bytes] = mapped_column(LargeBinary)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    security_mode: Mapped[str] = mapped_column(String(20), default="auto")
    pop_require_spa: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_host: Mapped[str | None] = mapped_column(String(255))
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_security_mode: Mapped[str] = mapped_column(String(20), default="auto")
    smtp_timeout: Mapped[int] = mapped_column(Integer, default=30)
    smtp_require_spa: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_auth_required: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_auth_method: Mapped[str] = mapped_column(String(30), default="same_as_pop")
    smtp_username: Mapped[str | None] = mapped_column(String(255))
    encrypted_smtp_password: Mapped[bytes | None] = mapped_column(LargeBinary)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    delete_after_receive: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_mode: Mapped[str] = mapped_column(String(10), default="all")
    filter_sender: Mapped[str | None] = mapped_column(String(998))
    filter_recipient: Mapped[str | None] = mapped_column(String(998))
    filter_subject: Mapped[str | None] = mapped_column(String(998))
    filter_keyword: Mapped[str | None] = mapped_column(Text)
    filter_date_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filter_date_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    messages: Mapped[list["EmailMessage"]] = relationship(back_populates="account")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('viewer', 'operator', 'admin')", name="ck_user_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BotRule(Base):
    """Operator-defined extraction targets for a vendor mail format."""
    __tablename__ = "bot_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    brand: Mapped[str] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(String(20), default="email")
    # Some devices (notably Kyocera) put identity in the mail body while the
    # counters live in an attachment. NULL keeps legacy rules on one source.
    serial_source_type: Mapped[str | None] = mapped_column(String(20))
    # Optional filename fragments keep rules from combining unrelated files in
    # a message that contains more than one supported attachment.
    attachment_filename: Mapped[str | None] = mapped_column(String(500))
    serial_attachment_filename: Mapped[str | None] = mapped_column(String(500))
    subject_keyword: Mapped[str | None] = mapped_column(String(300))
    sender_keyword: Mapped[str | None] = mapped_column(String(300))
    sample_format: Mapped[str] = mapped_column(Text, default="")
    serial_pattern: Mapped[str] = mapped_column(String(1000))
    black_pattern: Mapped[str | None] = mapped_column(String(1000))
    color_pattern: Mapped[str | None] = mapped_column(String(1000))
    total_pattern: Mapped[str | None] = mapped_column(String(1000))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class EmailMessage(Base):
    __tablename__ = "email_messages"
    __table_args__ = (
        UniqueConstraint("account_id", "content_sha256", name="uq_account_content_sha256"),
        Index("ix_email_received_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("pop_accounts.id"))
    message_id: Mapped[str | None] = mapped_column(String(998))
    content_sha256: Mapped[str] = mapped_column(String(64))
    sender: Mapped[str] = mapped_column(String(998), default="")
    recipients: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text_body: Mapped[str] = mapped_column(LONG_TEXT, default="")
    html_body: Mapped[str] = mapped_column(LONG_TEXT, default="")
    raw_message: Mapped[bytes] = mapped_column(LONG_BINARY)
    attachment_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="received")
    account: Mapped[PopAccount] = relationship(back_populates="messages")
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("email_messages.id"))
    filename: Mapped[str] = mapped_column(String(500), default="attachment")
    mime_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LONG_BINARY)
    message: Mapped[EmailMessage] = relationship(back_populates="attachments")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(320))
    external_code: Mapped[str | None] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    monthly_black_allowance: Mapped[int] = mapped_column(BigInteger, default=0)
    monthly_color_allowance: Mapped[int] = mapped_column(BigInteger, default=0)
    sites: Mapped[list["Site"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    counter_resolutions: Mapped[list["CounterResolution"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class CounterResolution(Base):
    """Operator record explaining how an organization's overage was handled."""
    __tablename__ = "counter_resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    period_start: Mapped[str] = mapped_column(String(7))
    period_end: Mapped[str] = mapped_column(String(7))
    period_type: Mapped[str] = mapped_column(String(20), default="monthly")
    action_note: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    organization: Mapped[Organization] = relationship(back_populates="counter_resolutions")


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Seoul")
    organization: Mapped[Organization] = relationship(back_populates="sites")
    devices: Mapped[list["Device"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    replacements: Mapped[list["DeviceReplacement"]] = relationship(
        back_populates="site", cascade="all, delete-orphan", order_by="DeviceReplacement.replaced_at"
    )


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("brand", "normalized_serial", name="uq_device_brand_serial"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    brand: Mapped[str] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(150))
    serial_number: Mapped[str] = mapped_column(String(150))
    normalized_serial: Mapped[str] = mapped_column(String(150))
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initial_black_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    initial_color_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    site: Mapped[Site] = relationship(back_populates="devices")
    readings: Mapped[list["CounterReading"]] = relationship(back_populates="device")


class DeviceReplacement(Base):
    """Immutable hand-over record between two devices at a customer site."""
    __tablename__ = "device_replacements"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    previous_device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    new_device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    replaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    previous_final_black_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    previous_final_color_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    new_initial_black_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    new_initial_color_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    site: Mapped[Site] = relationship(back_populates="replacements")
    previous_device: Mapped[Device] = relationship(foreign_keys=[previous_device_id])
    new_device: Mapped[Device] = relationship(foreign_keys=[new_device_id])


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("email_messages.id"), index=True)
    adapter: Mapped[str] = mapped_column(String(100))
    adapter_version: Mapped[str] = mapped_column(String(50))
    ocr_engine: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    readings: Mapped[list["CounterReading"]] = relationship(back_populates="run")


class CounterReading(Base):
    __tablename__ = "counter_readings"
    __table_args__ = (
        CheckConstraint("value >= 0", name="ck_counter_reading_value"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_counter_reading_confidence"),
        Index("ix_reading_device_type_captured", "device_id", "counter_type", "captured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    counter_type: Mapped[str] = mapped_column(String(50))
    value: Mapped[int] = mapped_column(BigInteger)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="needs_review")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    run: Mapped[ExtractionRun] = relationship(back_populates="readings")
    device: Mapped[Device] = relationship(back_populates="readings")
    reviews: Mapped[list["Review"]] = relationship(back_populates="reading", cascade="all, delete-orphan")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    reading_id: Mapped[int] = mapped_column(ForeignKey("counter_readings.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(30))
    before_value: Mapped[int | None] = mapped_column(BigInteger)
    after_value: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    reading: Mapped[CounterReading] = relationship(back_populates="reviews")


class ProcessingEvent(Base):
    __tablename__ = "processing_events"
    __table_args__ = (Index("ix_event_email_created", "email_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("email_messages.id"))
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
