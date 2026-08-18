import errno
from email.message import EmailMessage as MimeMessage
import socket
from unittest.mock import MagicMock, call, patch

from app.database import SessionLocal
from app.models import CounterReading, Device, EmailMessage, ExtractionRun, Organization, PopAccount, Site
from app.pop_service import _create_pop_socket, describe_connection_error, fetch_account, store_message
from app.security import decrypt_password, encrypt_password


def test_health_and_empty_pages(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "아직 받은 메일이 없습니다" in client.get("/").text
    assert "POP 계정 설정" in client.get("/settings").text
    assert "SMTP 아이디" in client.get("/settings").text
    assert "POP3의 USER/PASS 인증에 사용됩니다" in client.get("/settings").text


def test_add_pop_account_encrypts_password(client):
    response = client.post("/settings", data={
        "name": "업무 메일", "host": "pop.example.com", "port": "995",
        "username": "counter@example.com", "password": "secret", "security_mode": "ssl",
        "enabled": "on",
    }, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        account = db.query(PopAccount).one()
        assert account.encrypted_password != b"secret"
        assert decrypt_password(account.encrypted_password) == "secret"


def test_store_and_view_mime_message(client):
    with SessionLocal() as db:
        account = PopAccount(name="test", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add(account)
        db.commit()
        mime = MimeMessage()
        mime["Subject"] = "8월 카운터"
        mime["From"] = "device@example.com"
        mime["To"] = "counter@example.com"
        mime.set_content("누적 카운터: 12,345")
        mime.add_attachment(b"image", maintype="image", subtype="png", filename="counter.png")
        raw = mime.as_bytes()
        assert store_message(db, account, raw) is True
        assert store_message(db, account, raw) is False
        message_id = db.query(EmailMessage).one().id
    detail = client.get(f"/mail/{message_id}")
    assert detail.status_code == 200
    assert "8월 카운터" in detail.text
    assert "누적 카운터" in detail.text
    assert "counter.png" in detail.text


def test_prototype_domain_relations(client):
    with SessionLocal() as db:
        organization = Organization(name="테스트 고객사", external_code="TEST-1")
        site = Site(name="서울 사무소", organization=organization)
        device = Device(site=site, brand="Acme", model="C100", serial_number="A-12 34",
                        normalized_serial="A1234")
        account = PopAccount(name="test", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add_all([organization, account])
        db.flush()
        mime = MimeMessage()
        mime.set_content("counter")
        assert store_message(db, account, mime.as_bytes()) is True
        message = db.query(EmailMessage).one()
        run = ExtractionRun(email_id=message.id, adapter="plain", adapter_version="1", status="done")
        reading = CounterReading(run=run, device=device, counter_type="total", value=1234,
                                 captured_at=message.received_at, confidence=0.95)
        db.add(reading)
        db.commit()
        assert db.query(CounterReading).one().device.site.organization.name == "테스트 고객사"


def test_update_pop_account_keeps_password_when_blank(client):
    client.post("/settings", data={
        "name": "before", "host": "pop.example.com", "port": "995",
        "username": "user", "password": "secret", "security_mode": "ssl", "enabled": "on",
    })
    with SessionLocal() as db:
        account_id = db.query(PopAccount).one().id
    response = client.post(f"/settings/{account_id}", data={
        "name": "after", "host": "pop2.example.com", "port": "110",
        "username": "new-user", "password": "", "security_mode": "starttls", "enabled": "on",
    }, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        account = db.get(PopAccount, account_id)
        assert (account.name, account.host, account.security_mode) == (
            "after", "pop2.example.com", "starttls"
        )
        assert decrypt_password(account.encrypted_password) == "secret"


def test_fetch_failure_displays_actionable_pop_error(client):
    client.post("/settings", data={
        "name": "unreachable", "host": "bad.invalid", "port": "995",
        "username": "user", "password": "secret", "security_mode": "ssl",
    })
    with SessionLocal() as db:
        account_id = db.query(PopAccount).one().id

    with patch("app.pop_service._IPv4PreferredPOP3SSL", side_effect=socket.gaierror("not found")):
        response = client.post(f"/settings/{account_id}/fetch", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    error_page = client.get(response.headers["location"])
    assert "POP 서버 주소를 찾을 수 없습니다" in error_page.text


def test_fetch_uses_saved_smtp_credentials_for_pop_user_pass(client):
    with SessionLocal() as db:
        account = PopAccount(
            name="SMTP credentials", host="pop.example.com", port=995,
            username="smtp-user@example.com", encrypted_password=encrypt_password("smtp-secret"),
            use_ssl=True, security_mode="ssl",
        )
        db.add(account)
        db.commit()
        pop_client = MagicMock()
        pop_client.stat.return_value = (0, 0)

        with patch("app.pop_service._IPv4PreferredPOP3SSL", return_value=pop_client):
            assert fetch_account(db, account) == 0

    pop_client.user.assert_called_once_with("smtp-user@example.com")
    pop_client.pass_.assert_called_once_with("smtp-secret")


def test_starttls_is_negotiated_before_pop_authentication(client):
    with SessionLocal() as db:
        account = PopAccount(
            name="STARTTLS", host="pop.example.com", port=110,
            username="smtp-user", encrypted_password=encrypt_password("smtp-secret"),
            use_ssl=False, security_mode="starttls",
        )
        db.add(account)
        db.commit()
        pop_client = MagicMock()
        pop_client.stat.return_value = (0, 0)
        pop_client.attach_mock(pop_client.stls, "ordered_stls")
        pop_client.attach_mock(pop_client.user, "ordered_user")

        with patch("app.pop_service._IPv4PreferredPOP3", return_value=pop_client), \
             patch("app.pop_service.ssl.create_default_context") as create_context:
            assert fetch_account(db, account) == 0

    create_context.assert_called_once_with()
    assert pop_client.method_calls.index(call.ordered_stls(context=create_context.return_value)) \
        < pop_client.method_calls.index(call.ordered_user("smtp-user"))


def test_pop_protocol_error_bytes_are_readable():
    error = describe_connection_error(Exception("unknown"))
    assert error == "POP 수신 중 오류가 발생했습니다: unknown"

    import poplib
    error = describe_connection_error(poplib.error_proto(b"-ERR invalid login"))
    assert error == "POP 서버 응답: -ERR invalid login"


def test_network_unreachable_error_explains_container_and_ipv4_checks():
    error = describe_connection_error(OSError(errno.ENETUNREACH, "Network is unreachable"))

    assert "모두에 연결할 수 없습니다" in error
    assert "IPv4" in error
    assert "IPv6" in error
    assert "Errno" not in error


def test_timeout_error_does_not_assume_a_firewall_problem():
    error = describe_connection_error(socket.timeout("timed out"))

    assert "연결 또는 응답" in error
    assert "POP 사용 허용 여부" in error
    assert "방화벽" not in error


def test_pop_socket_tries_ipv4_before_ipv6_and_falls_back():
    ipv4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 995))
    ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 995, 0, 0))

    with patch("app.pop_service.socket.getaddrinfo", side_effect=[[ipv4], [ipv6]]), \
         patch("app.pop_service.socket.socket") as socket_factory:
        ipv4_socket, ipv6_socket = socket_factory.side_effect = [
            MagicMock(), MagicMock(),
        ]
        ipv4_socket.connect.side_effect = OSError(errno.ENETUNREACH, "unreachable")

        result = _create_pop_socket("pop.example.com", 995, 30)

    assert result is ipv6_socket
    ipv4_socket.connect.assert_called_once_with(ipv4[-1])
    ipv6_socket.connect.assert_called_once_with(ipv6[-1])
    assert ipv4_socket.settimeout.call_args_list == [call(5)]
    assert ipv6_socket.settimeout.call_args_list == [call(5), call(30)]


def test_pop_socket_does_not_wait_full_timeout_before_trying_next_address():
    first = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 995))
    second = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.2", 995))

    with patch("app.pop_service.socket.getaddrinfo", side_effect=[[first, second], []]), \
         patch("app.pop_service.socket.socket") as socket_factory:
        first_socket, second_socket = socket_factory.side_effect = [MagicMock(), MagicMock()]
        first_socket.connect.side_effect = socket.timeout("timed out")

        result = _create_pop_socket("pop.example.com", 995, 30)

    assert result is second_socket
    first_socket.settimeout.assert_called_once_with(5)
    assert second_socket.settimeout.call_args_list[-1].args == (30,)


def test_mailbox_search_raw_download_and_delete(client):
    with SessionLocal() as db:
        account = PopAccount(name="test", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add(account)
        db.commit()
        mime = MimeMessage()
        mime["Subject"] = "찾을 제목"
        mime["From"] = "sender@example.com"
        mime.set_content("특별한 본문")
        mime.add_attachment(b"image", maintype="image", subtype="png", filename="카운터.png")
        raw = mime.as_bytes()
        assert store_message(db, account, raw)
        message = db.query(EmailMessage).one()
        message_id = message.id
        attachment_id = message.attachments[0].id

    assert "찾을 제목" in client.get("/mail?q=특별한").text
    assert "조건에 맞는 메일이 없습니다" in client.get("/mail?q=없음").text
    raw_response = client.get(f"/mail/{message_id}/raw")
    assert raw_response.content == raw
    assert raw_response.headers["content-type"].startswith("message/rfc822")
    attachment_response = client.get(f"/attachments/{attachment_id}")
    assert "filename*=UTF-8''" in attachment_response.headers["content-disposition"]
    assert client.post(f"/mail/{message_id}/delete", follow_redirects=False).status_code == 303
    assert client.get(f"/mail/{message_id}").status_code == 404
