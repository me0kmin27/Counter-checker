from email.message import EmailMessage as MimeMessage

from app.database import SessionLocal
from app.models import CounterReading, Device, EmailMessage, ExtractionRun, Organization, PopAccount, Site
from app.pop_service import store_message
from app.security import decrypt_password, encrypt_password


def test_health_and_empty_pages(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "아직 받은 메일이 없습니다" in client.get("/").text
    assert "POP 계정 설정" in client.get("/settings").text


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
