import errno
import hashlib
import poplib
import socket
import ssl
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from .models import Attachment, EmailMessage, PopAccount
from .security import decrypt_password


MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_POP_LINE_BYTES = MAX_MESSAGE_BYTES
CONNECT_ATTEMPT_TIMEOUT_SECONDS = 5


def _create_pop_socket(host: str, port: int, timeout: float | None) -> socket.socket:
    """Connect over IPv4 first and fall back to IPv6 when IPv4 is unavailable."""
    if timeout is not None and timeout <= 0:
        raise ValueError("Non-blocking sockets are not supported")

    addresses = []
    resolution_errors = []
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            addresses.extend(socket.getaddrinfo(host, port, family, socket.SOCK_STREAM))
        except socket.gaierror as exc:
            resolution_errors.append(exc)

    if not addresses:
        if resolution_errors:
            raise resolution_errors[-1]
        raise socket.gaierror(f"No address found for {host}")

    connection_errors = []
    for family, socktype, proto, _, sockaddr in addresses:
        sock = socket.socket(family, socktype, proto)
        try:
            # Do not spend the entire POP timeout on the first black-holed IP.
            # A host often has several A/AAAA records, so leave time to try them.
            attempt_timeout = (
                min(timeout, CONNECT_ATTEMPT_TIMEOUT_SECONDS) if timeout is not None else None
            )
            sock.settimeout(attempt_timeout)
            sock.connect(sockaddr)
            sock.settimeout(timeout)
            return sock
        except OSError as exc:
            connection_errors.append(exc)
            sock.close()

    # Prefer an IPv4 error over a misleading final IPv6 ENETUNREACH error.
    raise connection_errors[0]


class _LongLinePOP3Mixin:
    """Accept non-standard long POP lines without changing poplib globally."""

    def _getline(self):
        # poplib's 2 KiB limit is appropriate for POP command responses, but
        # RETR also uses it for message data. Some servers return MIME payloads
        # without wrapping them, so permit a line up to our message size cap.
        line = self.file.readline(MAX_POP_LINE_BYTES + 1)
        if len(line) > MAX_POP_LINE_BYTES:
            raise poplib.error_proto("POP response line exceeds message size limit")
        if self._debugging > 1:
            print("*get*", repr(line))
        if not line:
            raise poplib.error_proto("-ERR EOF")

        octets = len(line)
        if line[-2:] == b"\r\n":
            return line[:-2], octets
        if line[:1] == b"\r":
            return line[1:-1], octets
        return line[:-1], octets


class _IPv4PreferredPOP3(_LongLinePOP3Mixin, poplib.POP3):
    def _create_socket(self, timeout):
        return _create_pop_socket(self.host, self.port, timeout)


class _IPv4PreferredPOP3SSL(_LongLinePOP3Mixin, poplib.POP3_SSL):
    def _create_socket(self, timeout):
        sock = _create_pop_socket(self.host, self.port, timeout)
        return self.context.wrap_socket(sock, server_hostname=self.host)


def describe_connection_error(exc: Exception) -> str:
    """Return a useful, password-safe error for the POP settings screen."""
    if isinstance(exc, DataError):
        detail = (
            "메일 데이터가 데이터베이스 컬럼의 저장 한도를 초과했습니다. "
            "서비스를 최신 이미지로 다시 빌드하고 시작하여 DB 스키마 업데이트를 적용하세요."
        )
    elif isinstance(exc, socket.gaierror):
        detail = "POP 서버 주소를 찾을 수 없습니다. 서버 주소를 확인하세요."
    elif isinstance(exc, (TimeoutError, socket.timeout)):
        detail = (
            "POP 서버의 연결 또는 응답을 기다리는 중 시간이 초과되었습니다. "
            "POP 서버 주소와 포트, 메일 제공업체의 POP 사용 허용 여부를 확인하세요."
        )
    elif isinstance(exc, ConnectionRefusedError):
        detail = "POP 서버가 연결을 거부했습니다. 주소와 포트를 확인하세요."
    elif isinstance(exc, OSError) and exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        detail = (
            "POP 서버의 IPv4와 IPv6 주소 모두에 연결할 수 없습니다. "
            "서버 주소와 포트가 올바른지, 실행 환경에 해당 IP 대역으로 가는 경로가 있는지 확인하세요."
        )
    elif isinstance(exc, ssl.SSLCertVerificationError):
        detail = "POP 서버 TLS 인증서를 확인할 수 없습니다. 인증서와 서버 시간을 확인하세요."
    elif isinstance(exc, ssl.SSLError):
        detail = "POP 서버와 TLS 연결에 실패했습니다. SSL 사용 여부와 포트를 확인하세요."
    elif isinstance(exc, poplib.error_proto):
        response = str(exc)
        if exc.args and isinstance(exc.args[0], bytes):
            response = exc.args[0].decode("utf-8", errors="replace")
        detail = f"POP 서버 응답: {response}"
    elif isinstance(exc, OSError):
        detail = f"POP 서버에 연결할 수 없습니다: {exc}"
    else:
        detail = f"POP 수신 중 오류가 발생했습니다: {exc}"
    return detail.replace("\r", " ").replace("\n", " ")[:500]


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
    client = None
    saved = 0
    try:
        security_mode = account.security_mode or ("ssl" if account.use_ssl else "starttls")
        if security_mode == "auto":
            security_mode = "ssl" if account.port == 995 else "starttls"
        client_type = _IPv4PreferredPOP3SSL if security_mode == "ssl" else _IPv4PreferredPOP3
        client = client_type(account.host, account.port, timeout=30)
        if security_mode == "starttls":
            client.stls(context=ssl.create_default_context())
        # Providers commonly call these the SMTP ID/password even when the same
        # mailbox credentials are used for POP3 USER/PASS authentication.
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
        account.last_error = describe_connection_error(exc)
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
