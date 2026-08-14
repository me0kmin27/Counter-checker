import hashlib
import poplib
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Attachment, EmailMessage, PopAccount
from .security import decrypt_password


MAX_MESSAGE_BYTES = 25 * 1024 * 1024


def _header(value: str | None) -> str:
    return str(make_header(decode_header(value or "")))


def _date(value: str | None):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def store_message(db: Session, account: PopAccount, raw: bytes) -> bool:
    digest = hashlib.sha256(raw).hexdigest()
    exists = db.scalar(select(EmailMessage.id).where(
        EmailMessage.account_id == account.id, EmailMessage.content_sha256 == digest
    ))
    if exists:
        return False
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    text_parts, html_parts, attachments = [], [], []
    for part in parsed.walk():
        if part.is_multipart():
            continue
        content = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if filename or disposition == "attachment":
            attachments.append((
                _header(filename) or "attachment",
                part.get_content_type(), content,
            ))
        elif part.get_content_type() == "text/plain":
            text_parts.append(content.decode(part.get_content_charset() or "utf-8", errors="replace"))
        elif part.get_content_type() == "text/html":
            html_parts.append(content.decode(part.get_content_charset() or "utf-8", errors="replace"))
    recipients = ", ".join(addr for _, addr in getaddresses(parsed.get_all("to", []) + parsed.get_all("cc", [])))
    message = EmailMessage(
        account_id=account.id, message_id=_header(parsed.get("Message-ID")) or None,
        content_sha256=digest, sender=_header(parsed.get("From")), recipients=recipients,
        subject=_header(parsed.get("Subject")), sent_at=_date(parsed.get("Date")),
        received_at=datetime.now(timezone.utc), text_body="\n\n".join(text_parts),
        html_body="\n".join(html_parts), raw_message=raw, attachment_count=len(attachments),
    )
    for filename, mime_type, content in attachments:
        message.attachments.append(Attachment(
            filename=filename, mime_type=mime_type, size_bytes=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(), content=content,
        ))
    db.add(message)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def fetch_account(db: Session, account: PopAccount) -> int:
    client_type = poplib.POP3_SSL if account.use_ssl else poplib.POP3
    client = None
    saved = 0
    try:
        client = client_type(account.host, account.port, timeout=30)
        client.user(account.username)
        client.pass_(decrypt_password(account.encrypted_password))
        count, _ = client.stat()
        for number in range(1, count + 1):
            _, lines, size = client.retr(number)
            if size > MAX_MESSAGE_BYTES:
                continue
            raw = b"\r\n".join(lines) + b"\r\n"
            if store_message(db, account, raw):
                saved += 1
            if account.delete_after_receive:
                client.dele(number)
        account.last_error = None
        return saved
    except Exception as exc:
        db.rollback()
        account.last_error = str(exc)[:500]
        raise
    finally:
        account.last_checked_at = datetime.now(timezone.utc)
        db.add(account)
        db.commit()
        if client is not None:
            try:
                client.quit()
            except poplib.error_proto:
                pass
