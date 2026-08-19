import errno
from datetime import datetime
from email.message import EmailMessage as MimeMessage
from io import BytesIO
import socket
from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import DataError, OperationalError
from sqlalchemy.schema import CreateTable

from app.database import SessionLocal
from app.models import (
    Attachment, CounterReading, CounterResolution, Device, DeviceReplacement, EmailMessage,
    ExtractionRun, Organization, PopAccount, Site, User,
)
from app.pop_service import (
    MAX_POP_LINE_BYTES,
    _IPv4PreferredPOP3,
    _create_pop_socket,
    describe_connection_error,
    fetch_account,
    message_matches_filters,
    store_message,
)
from app.counter_ingestion import parse_counter_message
from app.main import _organization_counter_data
from app.security import decrypt_password, encrypt_password


def test_login_roles_and_account_management(client):
    client.post("/users", data={
        "username": "reader", "display_name": "조회 담당", "password": "reader-password",
        "role": "viewer",
    })
    client.post("/logout")
    assert client.get("/").status_code == 200  # follows the login redirect
    response = client.post("/login", data={
        "username": "reader", "password": "reader-password", "next": "/",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert "POP 설정" not in client.get("/").text
    assert client.get("/settings", follow_redirects=False).status_code == 403


def test_mypage_password_and_totp_enrollment(client):
    assert client.post("/mypage/password", data={
        "current_password": "test-password-123", "new_password": "new-password-123",
    }, follow_redirects=False).status_code == 303
    response = client.post("/mypage/totp/start", follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/mypage")
    assert "otpauth://totp/" in page.text
    with SessionLocal() as db:
        assert db.query(User).filter_by(username="admin").one().totp_secret is None


def test_health_and_empty_pages(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "아직 받은 메일이 없습니다" in client.get("/").text
    assert "POP 계정 설정" in client.get("/settings").text
    assert "사용자 아이디" in client.get("/settings").text
    assert "SMTP 서버에 인증 필요" in client.get("/settings").text


def test_add_pop_account_encrypts_password(client):
    response = client.post("/settings", data={
        "name": "업무 메일", "host": "pop.example.com", "port": "995",
        "username": "counter@example.com", "password": "secret", "use_ssl": "on",
        "enabled": "on",
    }, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        account = db.query(PopAccount).one()
        assert account.encrypted_password != b"secret"
        assert decrypt_password(account.encrypted_password) == "secret"


def test_add_account_saves_outlook_style_pop_and_smtp_options(client):
    response = client.post("/settings", data={
        "name": "Outlook", "host": "pop.example.com", "port": "995",
        "username": "pop-user", "password": "pop-secret", "use_ssl": "on",
        "pop_require_spa": "on", "smtp_host": "smtp.example.com", "smtp_port": "465",
        "smtp_security_mode": "ssl", "smtp_timeout": "45", "smtp_require_spa": "on",
        "smtp_auth_required": "on", "smtp_auth_method": "credentials",
        "smtp_username": "smtp-user", "smtp_password": "smtp-secret",
    }, follow_redirects=False)

    assert response.status_code == 303
    with SessionLocal() as db:
        account = db.query(PopAccount).one()
        assert (account.use_ssl, account.pop_require_spa) == (True, True)
        assert (account.smtp_host, account.smtp_port) == ("smtp.example.com", 465)
        assert (account.smtp_security_mode, account.smtp_timeout) == ("ssl", 45)
        assert account.smtp_require_spa and account.smtp_auth_required
        assert (account.smtp_auth_method, account.smtp_username) == ("credentials", "smtp-user")
        assert decrypt_password(account.encrypted_smtp_password) == "smtp-secret"


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


@pytest.mark.parametrize(("subject", "body", "filename", "attachment", "adapter"), [
    ("신도리코 카운터 통지",
     "[Model Name], [Serial Number], 809160416897 [Send Date],16/07/26 "
     "[Total Counter],00002870 [Total Color Counter],00002153 "
     "[Total Black Counter],00000717 [Total Scan/Fax Counter],00000179",
     None, None, "sindoh-plain"),
    ("KYOCERA Counter",
     "Equipment ID: Model Name: ECOSYS M40021cfx Serial Number: 11Y5300412 "
     "MeterDate: Tue 11 Aug 2026 09:50:56 Counters by Function: Printed Pages: "
     "Copier: 384 Printer: 3419 FAX: 332 Total: 4135",
     "counter.png", b"not-needed", "kyocera"),
    ("Samsung report", "장비의 카운터 페이지를 첨부합니다.", "counter.rtf",
     b"{\\rtf1 Serial No: SAM-99\\par Black: 3,000\\par Color: 500\\par Total: 3,500}",
     "samsung-rtf"),
])
def test_vendor_counter_formats_are_parsed(subject, body, filename, attachment, adapter):
    message = EmailMessage(subject=subject, text_body=body, html_body="", attachments=[])
    if filename:
        message.attachments.append(Attachment(
            filename=filename, mime_type="application/rtf" if filename.endswith(".rtf") else "image/png",
            size_bytes=len(attachment), content_sha256="0" * 64, content=attachment,
        ))

    parsed = parse_counter_message(message)

    assert parsed.adapter == adapter
    assert parsed.serial_number
    expected = {
        "sindoh-plain": {"black": 717, "color": 2153, "total": 2870},
        "kyocera": {"total": 4135},
        "samsung-rtf": {"black": 3000, "color": 500, "total": 3500},
    }
    assert parsed.counters == expected[adapter]
    assert "total" in parsed.counters
    if adapter == "sindoh-plain":
        assert parsed.captured_at.isoformat() == "2026-07-16T00:00:00+00:00"
    elif adapter == "kyocera":
        assert parsed.captured_at.isoformat() == "2026-08-11T09:50:56+00:00"


def test_sindoh_plain_report_takes_priority_over_broad_custom_vendor_rule():
    message = EmailMessage(
        subject="카운터 통지메일",
        text_body=(
            "[Model Name],\n"
            "[Serial Number], 800140349718\n"
            "[Send Date],19/08/26\n"
            "[Total Counter],00104725\n"
            "[Total Color Counter],00040612\n"
            "[Total Black Counter],00064113\n"
            "[Total Scan/Fax Counter],00031967\n"
            "[Operating Accumulation Time],  3.8,  8.0,  8.9\n"
            "[Energizing Accumulation Time],433.9,743.9,720.0\n"
            "[Standing Accumulation Time],393.8,627.6,602.5\n"
            "[Power Saving Accumulation Time], 36.3,108.3,108.6"
        ),
        html_body="",
        attachments=[],
    )
    broad_samsung_rule = BotRule(
        brand="삼성", source_type="email", sample_format="",
        serial_pattern=r"\[Serial Number\],\s*(\d+)",
        black_pattern=r"Never matches: (\d+)", enabled=True,
    )

    parsed = parse_counter_message(message, [broad_samsung_rule])

    assert parsed.adapter == "sindoh-plain"
    assert parsed.serial_number == "800140349718"
    assert parsed.counters == {"total": 104725, "color": 40612, "black": 64113}
    assert parsed.captured_at.isoformat() == "2026-08-19T00:00:00+00:00"


def test_sindoh_plain_preserves_full_zero_padded_counter_values():
    message = EmailMessage(
        subject="신도리코 카운터 통지",
        text_body=(
            "[Model Name],\n[Serial Number], 800101012211\n[Send Date],19/08/26\n"
            "[Total Counter],00221005\n[Total Color Counter],00036352\n"
            "[Total Black Counter],00184653\n[Total Scan/Fax Counter],00002375"
        ),
        html_body="", attachments=[],
    )

    parsed = parse_counter_message(message)

    assert parsed.serial_number == "800101012211"
    assert parsed.counters == {"total": 221005, "color": 36352, "black": 184653}


def test_samsung_body_escaped_fields_supply_machine_serial():
    message = EmailMessage(
        subject="Samsung report",
        text_body=(r"Host Name\:SEC842519653E78 Host Location: Administrator Name:세강오피스 "
                   r"Administrator Email Address: skoa\@skoa.co.kr IP Address:192.168.0.200 "
                   r"Machine Serial Number\:ZJXXBJMK80001HH"),
        html_body="", attachments=[],
    )

    parsed = parse_counter_message(message)

    assert parsed.adapter == "samsung-rtf"
    assert parsed.serial_number == "ZJXXBJMK80001HH"


def test_received_counter_mail_automatically_creates_confirmed_readings(client):
    with SessionLocal() as db:
        organization = Organization(name="자동입력 고객사")
        device = Device(site=Site(name="본점", organization=organization), brand="신도리코",
                        serial_number="N-12345", normalized_serial="N12345")
        account = PopAccount(name="counter", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add_all([organization, account])
        db.commit()
        mime = MimeMessage()
        mime["Subject"] = "신도리코 카운터 통지"
        mime.set_content("시리얼번호: N-12345\n흑백: 12,000\n컬러: 2,000\n총카운터: 14,000")

        assert store_message(db, account, mime.as_bytes())
        readings = db.query(CounterReading).order_by(CounterReading.counter_type).all()

        assert [(item.counter_type, item.value) for item in readings] == [
            ("black", 12000), ("color", 2000), ("total", 14000),
        ]
        assert {item.status for item in readings} == {"confirmed"}
        assert db.query(ExtractionRun).one().status == "done"


def test_mail_filters_support_required_and_sufficient_conditions(client):
    account = PopAccount(filter_mode="all", filter_sender="DEVICE@example.com",
                         filter_recipient="counter@", filter_subject="monthly",
                         filter_keyword="12,345")
    mime = MimeMessage()
    mime["From"] = "Device <device@example.com>"
    mime["To"] = "counter@example.com"
    mime["Subject"] = "Monthly counter"
    mime["Date"] = "Tue, 18 Aug 2026 12:00:00 +0000"
    mime.set_content("누적값 12,345")

    assert message_matches_filters(account, mime.as_bytes()) is True
    account.filter_subject = "weekly"
    assert message_matches_filters(account, mime.as_bytes()) is False
    account.filter_mode = "any"
    assert message_matches_filters(account, mime.as_bytes()) is True


def test_fetch_stores_and_deletes_only_matching_mail(client):
    with SessionLocal() as db:
        account = PopAccount(name="filtered", host="pop.example.com", port=995, username="u",
                             encrypted_password=encrypt_password("p"), security_mode="ssl",
                             use_ssl=True, delete_after_receive=True, filter_subject="wanted")
        db.add(account)
        db.commit()
        wanted, rejected = MimeMessage(), MimeMessage()
        wanted["Subject"], rejected["Subject"] = "wanted report", "newsletter"
        wanted.set_content("one")
        rejected.set_content("two")
        pop_client = MagicMock()
        pop_client.stat.return_value = (2, 0)
        pop_client.retr.side_effect = [
            (b"+OK", wanted.as_bytes().splitlines(), len(wanted.as_bytes())),
            (b"+OK", rejected.as_bytes().splitlines(), len(rejected.as_bytes())),
        ]
        with patch("app.pop_service._IPv4PreferredPOP3SSL", return_value=pop_client):
            assert fetch_account(db, account) == 1
        assert db.query(EmailMessage).count() == 1
        pop_client.dele.assert_called_once_with(1)


def test_account_filter_form_persists_rules(client):
    response = client.post("/settings", data={
        "name": "filtered", "host": "pop.example.com", "port": "995",
        "username": "user", "password": "secret", "security_mode": "ssl",
        "filter_mode": "any", "filter_sender": "device@example.com",
        "filter_recipient": "counter@example.com", "filter_subject": "카운터",
        "filter_keyword": "누적", "filter_date_from": "2026-08-01",
        "filter_date_to": "2026-08-31",
    }, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        account = db.query(PopAccount).one()
        assert account.filter_mode == "any"
        assert account.filter_subject == "카운터"
        assert account.filter_date_from.date().isoformat() == "2026-08-01"
    page = client.get("/settings")
    assert "모든 조건 만족 (AND)" in page.text
    assert "device@example.com" in page.text


def test_store_message_retries_after_mysql_disconnect(client):
    with SessionLocal() as db:
        account = PopAccount(name="test", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add(account)
        db.commit()
        mime = MimeMessage()
        mime.set_content("counter")
        original_commit = db.commit
        attempts = 0

        def flaky_commit():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError(
                    "INSERT INTO email_messages ...", {},
                    Exception(2006, "MySQL server has gone away"),
                    connection_invalidated=True,
                )
            original_commit()

        with patch.object(db, "commit", side_effect=flaky_commit):
            assert store_message(db, account, mime.as_bytes()) is True

        assert attempts == 2
        assert db.query(EmailMessage).count() == 1


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


def test_counter_domain_is_visible_on_website(client):
    with SessionLocal() as db:
        organization = Organization(name="웹 고객사")
        site = Site(name="부산 지점", organization=organization)
        device = Device(site=site, brand="Acme", model="C200", serial_number="SN-9",
                        normalized_serial="SN9")
        account = PopAccount(name="test", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add_all([organization, account])
        db.flush()
        mime = MimeMessage()
        mime.set_content("counter")
        assert store_message(db, account, mime.as_bytes())
        message = db.query(EmailMessage).one()
        run = ExtractionRun(email_id=message.id, adapter="counter-parser", adapter_version="2")
        reading = CounterReading(run=run, device=device, counter_type="total", value=54321,
                                 captured_at=message.received_at, confidence=.91,
                                 raw_text="TOTAL 54321")
        db.add(reading)
        db.commit()
        reading_id = reading.id

    page = client.get("/counters")
    assert page.status_code == 200
    assert all(text in page.text for text in ("웹 고객사", "Acme C200", "54,321", "검토 필요"))
    assert all(text in page.text for text in (
        "계약 한도 흑백", "계약 한도 컬러", "실사용량 흑백", "실사용량 컬러", "꺾은선그래프",
    ))
    detail = client.get(f"/counters/{reading_id}")
    assert detail.status_code == 200
    assert all(text in detail.text for text in ("부산 지점", "TOTAL 54321", "counter-parser"))
    assert client.get("/counters/9999").status_code == 404


def test_customer_registration_creates_company_site_and_device(client):
    response = client.post("/customers", data={
        "company_name": "한빛상사", "model_name": "Bizhub C300i",
        "serial_number": "SN 12-34", "phone": "02-1234-5678",
        "email": "counter@hanbit.example", "started_at": "2026-08-01",
        "monthly_black_allowance": "1000", "monthly_color_allowance": "200",
        "initial_black_counter": "123", "initial_color_counter": "45",
    }, follow_redirects=False)

    assert response.status_code == 303
    with SessionLocal() as db:
        organization = db.query(Organization).one()
        assert (organization.name, organization.phone, organization.email) == (
            "한빛상사", "02-1234-5678", "counter@hanbit.example"
        )
        assert organization.sites[0].devices[0].normalized_serial == "SN12-34"
        assert organization.sites[0].devices[0].installed_at.strftime("%Y-%m-%d") == "2026-08-01"
        assert (organization.monthly_black_allowance, organization.monthly_color_allowance) == (1000, 200)
        assert (organization.sites[0].devices[0].initial_black_counter,
                organization.sites[0].devices[0].initial_color_counter) == (123, 45)
    page = client.get("/customers")
    assert all(value in page.text for value in ("한빛상사", "Bizhub C300i", "SN 12-34"))


def test_customer_search_pagination_edit_and_delete(client):
    with SessionLocal() as db:
        for number in range(31):
            organization = Organization(name=f"업체 {number:02d}", phone=f"02-0000-{number:04d}",
                                        email=f"company{number}@example.com")
            site = Site(name="기본 사업장")
            site.devices.append(Device(brand="미지정", model=f"Model {number}",
                                       serial_number=f"SERIAL-{number}",
                                       normalized_serial=f"SERIAL-{number}"))
            organization.sites.append(site)
            db.add(organization)
        db.commit()
        target_id = db.query(Organization.id).filter(Organization.name == "업체 07").scalar()

    first_page = client.get("/customers")
    assert first_page.status_code == 200
    assert first_page.text.count('data-open-dialog="edit-') == 30
    assert "다음" in first_page.text
    second_page = client.get("/customers?page=2")
    assert second_page.text.count('data-open-dialog="edit-') == 1
    search_page = client.get("/customers?q=SERIAL-7")
    assert "업체 07" in search_page.text and "업체 08" not in search_page.text

    response = client.post(f"/customers/{target_id}/edit", data={
        "company_name": "수정 업체", "model_name": "New Model", "serial_number": "NEW-7",
        "phone": "02-7777-7777", "email": "new@example.com", "started_at": "2026-07-07",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert "수정 업체" in client.get("/customers?q=NEW-7").text
    assert client.post(f"/customers/{target_id}/delete", follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.get(Organization, target_id) is None


def test_customer_device_replacement_preserves_counter_handover_history(client):
    client.post("/customers", data={
        "company_name": "교체 업체", "model_name": "Old MFP", "serial_number": "OLD-1",
        "phone": "02-1111-1111", "email": "old@example.com", "started_at": "2026-01-01",
        "monthly_black_allowance": "1500", "monthly_color_allowance": "300",
        "initial_black_counter": "10", "initial_color_counter": "20",
    })
    with SessionLocal() as db:
        organization_id = db.query(Organization.id).scalar()

    response = client.post(f"/customers/{organization_id}/edit", data={
        "company_name": "교체 업체", "model_name": "New MFP", "serial_number": "NEW-1",
        "phone": "02-1111-1111", "email": "new@example.com", "started_at": "2026-08-18",
        "monthly_black_allowance": "2000", "monthly_color_allowance": "400",
        "initial_black_counter": "100", "initial_color_counter": "200",
        "previous_final_black_counter": "9100", "previous_final_color_counter": "2200",
    }, follow_redirects=False)

    assert response.status_code == 303
    with SessionLocal() as db:
        organization = db.get(Organization, organization_id)
        devices = sorted(organization.sites[0].devices, key=lambda item: item.id)
        replacement = organization.sites[0].replacements[0]
        replacement_id = replacement.id
        assert devices[0].retired_at.strftime("%Y-%m-%d") == "2026-08-18"
        assert (devices[1].serial_number, devices[1].initial_black_counter,
                devices[1].initial_color_counter) == ("NEW-1", 100, 200)
        assert (replacement.previous_final_black_counter,
                replacement.previous_final_color_counter) == (9100, 2200)
    page = client.get("/customers")
    assert all(value in page.text for value in ("복합기 교체 이력", "OLD-1", "NEW-1", "9,100", "2,200"))
    assert f"/customers/{organization_id}/history/replacement/{replacement_id}/delete" in page.text

    response = client.post(
        f"/customers/{organization_id}/history/replacement/{replacement_id}/delete",
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.query(DeviceReplacement).count() == 0
        assert db.query(Device).count() == 2


def test_customer_counter_resolution_history_can_be_deleted(client):
    with SessionLocal() as db:
        organization = Organization(name="이력 업체", phone="02-1111-2222", email="history@example.com")
        site = Site(name="기본 사업장")
        site.devices.append(Device(brand="미지정", model="History MFP", serial_number="HISTORY-1",
                                   normalized_serial="HISTORY-1"))
        organization.sites.append(site)
        db.add(organization)
        db.commit()
        organization_id = organization.id

    response = client.post(f"/counters/{organization_id}/resolve", data={
        "period_start": "2026-07", "period_end": "2026-07",
        "period_type": "monthly", "action_note": "초과분 정산 완료",
    }, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        resolution_id = db.query(CounterResolution.id).scalar()

    page = client.get("/customers")
    delete_path = f"/customers/{organization_id}/history/counter/{resolution_id}/delete"
    assert delete_path in page.text and "초과분 정산 완료" in page.text
    assert client.post(delete_path, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.query(CounterResolution).count() == 0

    assert client.post(delete_path, follow_redirects=False).status_code == 404


def test_counter_workspace_filters_by_company_and_period(client):
    with SessionLocal() as db:
        organization = Organization(name="기간조회 업체")
        site = Site(name="기본", organization=organization)
        device = Device(site=site, brand="Acme", model="M1", serial_number="PERIOD-1",
                        normalized_serial="PERIOD-1")
        account = PopAccount(name="test", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add_all([organization, account])
        db.flush()
        mime = MimeMessage()
        mime.set_content("counter")
        assert store_message(db, account, mime.as_bytes())
        run = ExtractionRun(email_id=db.query(EmailMessage).one().id, adapter="test",
                            adapter_version="1")
        db.add(CounterReading(run=run, device=device, counter_type="total", value=9876,
                              captured_at=db.query(EmailMessage).one().received_at,
                              confidence=1, raw_text="9876"))
        db.commit()
        organization_id = organization.id

    page = client.get(f"/counters?organization_id={organization_id}&months=6")
    assert page.status_code == 200
    assert "기간조회 업체" in page.text and "9,876" in page.text
    assert 'value="6" selected' in page.text
    assert client.get("/counters?months=5").status_code == 422


def test_counter_history_starts_at_usage_date_and_fills_months(client):
    with SessionLocal() as db:
        organization = Organization(name="누적 업체", monthly_black_allowance=1000,
                                    monthly_color_allowance=200)
        device = Device(site=Site(name="기본", organization=organization), brand="Acme",
                        serial_number="ACC-1", normalized_serial="ACC-1",
                        installed_at=datetime(2026, 1, 15), initial_black_counter=100,
                        initial_color_counter=10)
        account = PopAccount(name="test", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add_all([organization, account])
        db.flush()
        message = EmailMessage(account=account, content_sha256="a" * 64, sender="", recipients="",
                               subject="", received_at=datetime(2026, 3, 20), raw_message=b"mail")
        db.add(message)
        db.flush()
        run = ExtractionRun(email_id=message.id, adapter="test", adapter_version="1")
        db.add(run)
        db.flush()
        db.add_all([
            CounterReading(run=run, device=device, counter_type="black", value=700,
                           captured_at=datetime(2026, 3, 20), confidence=1, raw_text="700"),
            CounterReading(run=run, device=device, counter_type="color", value=60,
                           captured_at=datetime(2026, 3, 20), confidence=1, raw_text="60"),
        ])
        db.commit()
        db.refresh(organization)

        history = _organization_counter_data(organization)

    assert [row["month"] for row in history] == ["2026-01", "2026-02", "2026-03"]
    assert [row["black"] for row in history] == [0, 0, 600]
    assert history[-1]["meter_black"] == 700
    assert history[-1]["meter_color"] == 60
    assert history[-1]["cumulative_black"] == 600
    assert history[-1]["cumulative_black_limit"] == 3000


def test_update_pop_account_keeps_password_when_blank(client):
    client.post("/settings", data={
        "name": "before", "host": "pop.example.com", "port": "995",
        "username": "user", "password": "secret", "use_ssl": "on", "enabled": "on",
    })
    with SessionLocal() as db:
        account_id = db.query(PopAccount).one().id
    response = client.post(f"/settings/{account_id}", data={
        "name": "after", "host": "pop2.example.com", "port": "110",
        "username": "new-user", "password": "", "enabled": "on",
    }, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        account = db.get(PopAccount, account_id)
        assert (account.name, account.host, account.security_mode) == (
            "after", "pop2.example.com", "starttls"
        )
        assert decrypt_password(account.encrypted_password) == "secret"


def test_pop_security_mode_none_is_displayed_and_persisted(client):
    response = client.post("/settings", data={
        "name": "plain POP", "host": "pop.example.com", "port": "110",
        "username": "user", "password": "secret", "security_mode": "none",
        "smtp_security_mode": "none",
    }, follow_redirects=False)

    assert response.status_code == 303
    with SessionLocal() as db:
        account = db.query(PopAccount).one()
        assert account.security_mode == "none"
        assert account.smtp_security_mode == "none"
        assert account.use_ssl is False

    page = client.get("/settings")
    assert page.text.count('value="none" selected') == 2
    assert "사용 안 함" in page.text


def test_invalid_pop_security_mode_is_rejected(client):
    response = client.post("/settings", data={
        "name": "invalid", "host": "pop.example.com", "port": "110",
        "username": "user", "password": "secret", "security_mode": "plaintext",
    })

    assert response.status_code == 422


def test_fetch_failure_displays_actionable_pop_error(client):
    client.post("/settings", data={
        "name": "unreachable", "host": "bad.invalid", "port": "995",
        "username": "user", "password": "secret", "use_ssl": "on",
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


def test_security_mode_none_authenticates_without_tls(client):
    with SessionLocal() as db:
        account = PopAccount(
            name="Plain POP", host="pop.example.com", port=110,
            username="user", encrypted_password=encrypt_password("secret"),
            use_ssl=False, security_mode="none",
        )
        db.add(account)
        db.commit()
        pop_client = MagicMock()
        pop_client.stat.return_value = (0, 0)

        with patch("app.pop_service._IPv4PreferredPOP3", return_value=pop_client):
            assert fetch_account(db, account) == 0

    pop_client.stls.assert_not_called()
    pop_client.user.assert_called_once_with("user")
    pop_client.pass_.assert_called_once_with("secret")


def test_pop_protocol_error_bytes_are_readable():
    error = describe_connection_error(Exception("unknown"))
    assert error == "POP 수신 중 오류가 발생했습니다: unknown"

    import poplib
    error = describe_connection_error(poplib.error_proto(b"-ERR invalid login"))
    assert error == "POP 서버 응답: -ERR invalid login"


def test_pop_client_accepts_message_lines_longer_than_poplib_default():
    client = _IPv4PreferredPOP3.__new__(_IPv4PreferredPOP3)
    client.file = BytesIO(b"x" * 4096 + b"\r\n")
    client._debugging = 0

    line, octets = client._getline()

    assert line == b"x" * 4096
    assert octets == 4098


def test_pop_client_rejects_a_line_larger_than_message_limit():
    client = _IPv4PreferredPOP3.__new__(_IPv4PreferredPOP3)
    client.file = BytesIO(b"x" * (MAX_POP_LINE_BYTES + 1))
    client._debugging = 0

    import poplib
    with pytest.raises(poplib.error_proto, match="message size limit"):
        client._getline()


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


def test_database_size_error_is_sanitized_and_actionable():
    error = describe_connection_error(DataError(
        "INSERT INTO email_messages ...", {}, Exception("Data too long for raw_message")
    ))

    assert "DB 스키마 업데이트" in error
    assert "INSERT INTO" not in error


def test_database_disconnect_error_is_sanitized_and_actionable():
    error = describe_connection_error(OperationalError(
        "INSERT INTO email_messages ...", {},
        Exception(2006, "MySQL server has gone away"),
        connection_invalidated=True,
    ))

    assert "데이터베이스 연결이 끊어졌습니다" in error
    assert "max_allowed_packet" in error
    assert "INSERT INTO" not in error


def test_mysql_message_and_attachment_payloads_use_longblob():
    message_ddl = str(CreateTable(EmailMessage.__table__).compile(dialect=mysql.dialect()))
    attachment_ddl = str(CreateTable(Attachment.__table__).compile(dialect=mysql.dialect()))

    assert "raw_message LONGBLOB" in message_ddl
    assert "text_body LONGTEXT" in message_ddl
    assert "html_body LONGTEXT" in message_ddl
    assert "content LONGBLOB" in attachment_ddl


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


def test_mailbox_selected_and_all_bulk_delete(client):
    with SessionLocal() as db:
        account = PopAccount(name="test", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add(account)
        db.commit()
        message_ids = []
        for subject in ("첫 번째", "두 번째", "세 번째"):
            mime = MimeMessage()
            mime["Subject"] = subject
            mime.set_content(subject)
            assert store_message(db, account, mime.as_bytes())
            message_ids.append(db.query(EmailMessage).filter_by(subject=subject).one().id)

    page = client.get("/mail")
    assert 'id="select-all"' in page.text
    assert page.text.count('name="message_ids"') == 3
    response = client.post("/mail/bulk-delete", data={
        "scope": "selected", "message_ids": [message_ids[0], message_ids[2]],
    }, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        assert [message.id for message in db.query(EmailMessage).all()] == [message_ids[1]]

    response = client.post("/mail/bulk-delete", data={"scope": "all"},
                           follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.query(EmailMessage).count() == 0


def test_mailbox_selected_bulk_delete_requires_a_selection(client):
    response = client.post("/mail/bulk-delete", data={"scope": "selected"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert "%EC%82%AD%EC%A0%9C%ED%95%A0" in response.headers["location"]


def test_mailbox_selected_and_all_counter_extraction(client):
    with SessionLocal() as db:
        account = PopAccount(name="test", host="localhost", port=110, username="u",
                             encrypted_password=encrypt_password("p"), use_ssl=False)
        db.add(account)
        db.commit()
        message_ids = []
        for subject, body in (
            ("첫 카운터", "Serial Number: FIRST-001 Black: 10 Color: 20 Total: 30"),
            ("일반 메일", "카운터 정보가 없는 본문"),
            ("두 번째 카운터", "Serial Number: SECOND-002 Black: 40 Color: 50 Total: 90"),
        ):
            mime = MimeMessage()
            mime["Subject"] = subject
            mime.set_content(body)
            assert store_message(db, account, mime.as_bytes())
            message_ids.append(db.query(EmailMessage).filter_by(subject=subject).one().id)

    page = client.get("/mail")
    assert 'formaction="/mail/bulk-extract"' in page.text
    assert "선택 카운터 추출" in page.text
    assert "전체 카운터 추출" in page.text

    response = client.post("/mail/bulk-extract", data={
        "scope": "selected", "message_ids": [message_ids[0], message_ids[1]],
    }, follow_redirects=False)
    assert response.status_code == 303
    assert "%EC%B9%B4%EC%9A%B4%ED%84%B0%201%EA%B1%B4" in response.headers["location"]
    with SessionLocal() as db:
        assert [run.email_id for run in db.query(ExtractionRun).all()] == [message_ids[0]]

    response = client.post("/mail/bulk-extract", data={"scope": "all"},
                           follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        assert [run.email_id for run in db.query(ExtractionRun).all()] == [
            message_ids[0], message_ids[0], message_ids[2],
        ]


def test_mailbox_selected_counter_extraction_requires_a_selection(client):
    response = client.post("/mail/bulk-extract", data={"scope": "selected"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert "%EC%B6%94%EC%B6%9C%ED%95%A0" in response.headers["location"]


def test_kyocera_counter_is_parsed_from_htm_attachment():
    message = EmailMessage(subject="KYOCERA Counter", sender="device@example.com",
                           text_body="카운터 파일을 첨부합니다.", html_body="", attachments=[])
    report = (b'<html><head><meta charset="utf-8"></head><body>'
              b'<div>Serial Number: 11Y5300412</div>'
              b'<div>MeterDate: 11 Aug 2026 09:50:56</div>'
              b'<h2>Counters by Function:</h2><table><tr><td>Total:</td><td>4,135</td></tr>'
              b'</table></body></html>')
    message.attachments.append(Attachment(filename="counter.htm", mime_type="text/html",
                                          size_bytes=len(report), content_sha256="1" * 64,
                                          content=report))

    parsed = parse_counter_message(message)

    assert parsed.adapter == "kyocera"
    assert parsed.serial_number == "11Y5300412"
    assert parsed.counters == {"total": 4135}


def test_kyocera_alphabet_leading_serial_is_valid():
    message = EmailMessage(subject="KYOCERA Counter", sender="device@example.com",
                           text_body="첨부 참조", html_body="", attachments=[])
    report = (b"<html><body>Serial Number: W2P1234567 "
              b"Counters by Function: Total: 9,876</body></html>")
    message.attachments.append(Attachment(
        filename="Counter_Function.htm", mime_type="text/html",
        size_bytes=len(report), content_sha256="a" * 64, content=report,
    ))

    parsed = parse_counter_message(message)

    assert parsed.serial_number == "W2P1234567"
    assert parsed.counters == {"total": 9876}


def test_custom_bot_rule_can_target_htm_attachment():
    from app.models import BotRule

    message = EmailMessage(subject="Custom report", sender="bot@example.com",
                           text_body="첨부 참조", html_body="", attachments=[])
    report = "<html><body>장비번호: K-100<br>흑백: 1,200<br>컬러: 30<br>합계: 1,230</body></html>".encode()
    message.attachments.append(Attachment(filename="meter.HTML", mime_type="application/octet-stream",
                                          size_bytes=len(report), content_sha256="2" * 64,
                                          content=report))
    rule = BotRule(brand="테스트", source_type="html_attachment", enabled=True,
                   serial_pattern=r"장비번호:\s*([A-Z0-9-]+)",
                   black_pattern=r"흑백:\s*([0-9,]+)", color_pattern=r"컬러:\s*([0-9,]+)",
                   total_pattern=r"합계:\s*([0-9,]+)")

    parsed = parse_counter_message(message, [rule])

    assert parsed.serial_number == "K-100"
    assert parsed.counters == {"black": 1200, "color": 30, "total": 1230}


def test_custom_rule_can_read_kyocera_serial_from_mail_and_counters_from_attachment():
    from app.models import BotRule

    message = EmailMessage(
        subject="KYOCERA RJF3201840", sender="device@example.com",
        text_body="Serial Number: RJF3201840", html_body="", attachments=[],
    )
    report = b"<html><body>Black &amp; White A4: 1 Full Color A4: 100,000 Total A4: 100,001</body></html>"
    message.attachments.append(Attachment(
        filename="counter.htm", mime_type="text/html", size_bytes=len(report),
        content_sha256="3" * 64, content=report,
    ))
    rule = BotRule(
        brand="교세라", source_type="html_attachment", serial_source_type="email", enabled=True,
        serial_pattern=r"Serial Number:\s*([A-Z0-9-]+)",
        black_pattern=r"Black\s*&\s*White\s+A4:\s*([0-9]+(?:[,\s][0-9]+)*)",
        color_pattern=r"Full\s+Color\s+A4:\s*([0-9]+(?:[,\s][0-9]+)*)",
        total_pattern=r"Total\s+A4:\s*([0-9]+(?:[,\s][0-9]+)*)",
    )

    parsed = parse_counter_message(message, [rule])

    assert parsed.serial_number == "RJF3201840"
    assert parsed.counters == {"black": 1, "color": 100000, "total": 100001}


def test_custom_kyocera_rule_falls_back_from_sample_specific_serial_pattern():
    """A legacy 11Y-based target must not make an otherwise identical WDM unit disappear."""
    from app.models import BotRule

    message = EmailMessage(
        subject="KYOCERA Counter", sender="device@example.com",
        text_body="Equipment ID: Serial Number: WDM3500938", html_body="", attachments=[],
    )
    report = b"<html><body>Total A4: 7,654</body></html>"
    message.attachments.append(Attachment(
        filename="WDM3500938-counter.htm", mime_type="text/html", size_bytes=len(report),
        content_sha256="b" * 64, content=report,
    ))
    rule = BotRule(
        brand="교세라", source_type="html_attachment", serial_source_type="email",
        attachment_filename="11Y5300412-counter.htm", enabled=True,
        # Simulates a rule persisted from the working 11Y5300412 sample.
        serial_pattern=r"Serial Number:\s*(11Y[0-9]+)",
        total_pattern=r"Total A4:\s*([0-9,]+)",
    )

    parsed = parse_counter_message(message, [rule])

    assert parsed.adapter == "custom-교세라"
    assert parsed.serial_number == "WDM3500938"
    assert parsed.counters == {"total": 7654}


def test_custom_rule_does_not_guess_when_multiple_attachments_miss_filename():
    """A stale filename is only bypassed when one supported report is unambiguous."""
    from app.models import BotRule

    message = EmailMessage(
        subject="KYOCERA Counter", sender="device@example.com",
        text_body="Serial Number: WDM3500938", html_body="", attachments=[],
    )
    for name, value in (("first.htm", "111"), ("second.htm", "222")):
        report = f"<html><body>Total A4: {value}</body></html>".encode()
        message.attachments.append(Attachment(
            filename=name, mime_type="text/html", size_bytes=len(report),
            content_sha256=value.zfill(64), content=report,
        ))
    rule = BotRule(
        brand="교세라", source_type="html_attachment", serial_source_type="email",
        attachment_filename="11Y5300412-counter.htm", enabled=True,
        serial_pattern=r"Serial Number:\s*([A-Z0-9]+)",
        total_pattern=r"Total A4:\s*([0-9,]+)",
    )

    parsed = parse_counter_message(message, [rule])

    assert parsed.serial_number == "WDM3500938"
    assert parsed.counters == {}


def test_custom_rule_reads_only_the_named_kyocera_attachment():
    from app.models import BotRule

    message = EmailMessage(subject="KYOCERA", sender="device@example.com",
                           text_body="Serial Number: KYO-77", html_body="", attachments=[])
    for filename, total in (("old-counter.htm", "999"), ("monthly-counter.htm", "1,234")):
        report = f"<html><body>Total: {total}</body></html>".encode()
        message.attachments.append(Attachment(
            filename=filename, mime_type="text/html", size_bytes=len(report),
            content_sha256="4" * 64, content=report,
        ))
    rule = BotRule(
        brand="교세라", source_type="html_attachment", serial_source_type="email",
        attachment_filename="monthly-counter.htm",
        # A stale attachment value must never be applied to an email source.
        serial_attachment_filename="does-not-exist.htm", enabled=True,
        serial_pattern=r"Serial Number:\s*([A-Z0-9-]+)",
        total_pattern=r"Total:\s*([0-9,]+)",
    )

    parsed = parse_counter_message(message, [rule])

    assert parsed.serial_number == "KYO-77"
    assert parsed.counters == {"total": 1234}


def test_custom_samsung_rule_reads_serial_and_counters_from_named_rtf():
    from app.models import BotRule

    message = EmailMessage(subject="Samsung", sender="device@example.com",
                           text_body="첨부 참조", html_body="", attachments=[])
    report = b"{\\rtf1 Serial No: SAM-88\\par Black: 100\\par Color: 20\\par Total: 120}"
    message.attachments.append(Attachment(
        filename="samsung-meter.rtf", mime_type="application/octet-stream",
        size_bytes=len(report), content_sha256="5" * 64, content=report,
    ))
    rule = BotRule(
        brand="삼성", source_type="rtf", serial_source_type="rtf",
        attachment_filename="samsung-meter.rtf", enabled=True,
        serial_pattern=r"Serial No:\s*([A-Z0-9-]+)",
        black_pattern=r"Black:\s*([0-9,]+)", color_pattern=r"Color:\s*([0-9,]+)",
        total_pattern=r"Total:\s*([0-9,]+)",
    )

    parsed = parse_counter_message(message, [rule])

    assert parsed.serial_number == "SAM-88"
    assert parsed.counters == {"black": 100, "color": 20, "total": 120}


@pytest.mark.parametrize(("filename", "mime_type", "payload", "expected"), [
    ("kyocera.htm", "text/html", b"<table><tr><td>Serial Number:</td><td>RJF3201840</td></tr></table>",
     "RJF3201840"),
    ("samsung.rtf", "application/rtf", b"{\\rtf1 Serial No: SAM-77\\par Total: 52,971}",
     "Total: 52,971"),
])
def test_bot_settings_can_preview_supported_attachment(client, filename, mime_type,
                                                       payload, expected):
    response = client.post("/bot-settings/preview", files={"file": (filename, payload, mime_type)})

    assert response.status_code == 200
    assert expected in response.json()["text"]


def test_bot_settings_rejects_unsupported_preview_file(client):
    response = client.post(
        "/bot-settings/preview", files={"file": ("counter.pdf", b"pdf", "application/pdf")},
    )

    assert response.status_code == 415
    assert "HTM, HTML, RTF" in response.json()["detail"]
