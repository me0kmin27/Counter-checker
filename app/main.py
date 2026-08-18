import asyncio
import os
from contextlib import asynccontextmanager, suppress
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .database import Base, SessionLocal, engine, ensure_compatibility_schema, get_db
from .models import (
    Attachment, CounterReading, Device, EmailMessage, ExtractionRun, Organization,
    PopAccount, ProcessingEvent, Site,
)
from .pop_service import fetch_account
from .security import encrypt_password


def _validate_account(name: str, host: str, port: int, username: str) -> tuple[str, str, str]:
    name, host, username = name.strip(), host.strip(), username.strip()
    if not name or not host or not username or not 1 <= port <= 65535:
        raise HTTPException(422, "POP 계정 정보를 올바르게 입력하세요.")
    if any(character.isspace() for character in host):
        raise HTTPException(422, "POP 서버 주소에는 공백을 사용할 수 없습니다.")
    return name, host, username


def _validate_mail_options(security_mode: str, smtp_security_mode: str, smtp_auth_method: str,
                           smtp_port: int, smtp_timeout: int):
    security_modes = {"auto", "ssl", "starttls", "none"}
    if security_mode not in security_modes:
        raise HTTPException(422, "POP 보안 연결 방식을 올바르게 선택하세요.")
    if smtp_security_mode not in security_modes:
        raise HTTPException(422, "SMTP 암호화 방법을 올바르게 선택하세요.")
    if smtp_auth_method not in {"same_as_pop", "credentials", "pop_before_smtp"}:
        raise HTTPException(422, "SMTP 인증 방법을 올바르게 선택하세요.")
    if not 1 <= smtp_port <= 65535 or not 1 <= smtp_timeout <= 300:
        raise HTTPException(422, "SMTP 포트와 타임아웃을 올바르게 입력하세요.")


async def poll_loop():
    interval = max(60, int(os.getenv("POLL_INTERVAL_SECONDS", "300")))
    while True:
        await asyncio.sleep(interval)
        with SessionLocal() as db:
            accounts = db.scalars(select(PopAccount).where(PopAccount.enabled.is_(True))).all()
            for account in accounts:
                try:
                    await asyncio.to_thread(fetch_account, db, account)
                except Exception:
                    pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    ensure_compatibility_schema()
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Counter Checker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    accounts = db.scalar(select(func.count()).select_from(PopAccount)) or 0
    active_accounts = db.scalar(
        select(func.count()).select_from(PopAccount).where(PopAccount.enabled.is_(True))
    ) or 0
    messages = db.scalar(select(func.count()).select_from(EmailMessage)) or 0
    attachments = db.scalar(select(func.count()).select_from(Attachment)) or 0
    latest = db.scalars(select(EmailMessage).order_by(EmailMessage.received_at.desc()).limit(5)).all()
    errors = db.scalar(select(func.count()).select_from(PopAccount).where(PopAccount.last_error.is_not(None))) or 0
    return templates.TemplateResponse(request, "dashboard.html", {
        "accounts": accounts, "active_accounts": active_accounts, "messages": messages,
        "attachments": attachments, "latest": latest, "errors": errors,
    })


@app.get("/counters", response_class=HTMLResponse)
def counter_workspace(request: Request, db: Session = Depends(get_db)):
    """Expose the counter-analysis domain that is already persisted by the application."""
    counts = {
        "organizations": db.scalar(select(func.count()).select_from(Organization)) or 0,
        "sites": db.scalar(select(func.count()).select_from(Site)) or 0,
        "devices": db.scalar(select(func.count()).select_from(Device)) or 0,
        "readings": db.scalar(select(func.count()).select_from(CounterReading)) or 0,
    }
    pending = db.scalar(
        select(func.count()).select_from(CounterReading)
        .where(CounterReading.status == "needs_review")
    ) or 0
    readings = db.scalars(
        select(CounterReading).options(
            selectinload(CounterReading.device).selectinload(Device.site)
            .selectinload(Site.organization),
            selectinload(CounterReading.run),
        ).order_by(CounterReading.captured_at.desc()).limit(30)
    ).all()
    devices = db.scalars(
        select(Device).options(
            selectinload(Device.site).selectinload(Site.organization),
            selectinload(Device.readings),
        ).order_by(Device.id.desc()).limit(20)
    ).all()
    runs = db.scalars(
        select(ExtractionRun).order_by(ExtractionRun.created_at.desc()).limit(10)
    ).all()
    events = db.scalars(
        select(ProcessingEvent).order_by(ProcessingEvent.created_at.desc()).limit(10)
    ).all()
    return templates.TemplateResponse(request, "counters.html", {
        "counts": counts, "pending": pending, "readings": readings,
        "devices": devices, "runs": runs, "events": events,
    })


@app.get("/counters/{reading_id}", response_class=HTMLResponse)
def counter_detail(reading_id: int, request: Request, db: Session = Depends(get_db)):
    reading = db.scalar(
        select(CounterReading).options(
            selectinload(CounterReading.device).selectinload(Device.site)
            .selectinload(Site.organization),
            selectinload(CounterReading.run), selectinload(CounterReading.reviews),
        ).where(CounterReading.id == reading_id)
    )
    if not reading:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "counter_detail.html", {"reading": reading})


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "settings.html", {
        "accounts": db.scalars(select(PopAccount).order_by(PopAccount.id)).all()
    })


@app.post("/settings")
def add_account(
    name: str = Form(...), host: str = Form(...), port: int = Form(995),
    username: str = Form(...), password: str = Form(...),
    use_ssl: bool = Form(False), security_mode: str | None = Form(None),
    pop_require_spa: bool = Form(False),
    smtp_host: str = Form(""), smtp_port: int = Form(587),
    smtp_security_mode: str = Form("auto"), smtp_timeout: int = Form(30),
    smtp_require_spa: bool = Form(False), smtp_auth_required: bool = Form(False),
    smtp_auth_method: str = Form("same_as_pop"), smtp_username: str = Form(""),
    smtp_password: str = Form(""), enabled: bool = Form(False),
    delete_after_receive: bool = Form(False), db: Session = Depends(get_db),
):
    name, host, username = _validate_account(name, host, port, username)
    # Continue to accept submissions from the former SSL checkbox-only form.
    security_mode = security_mode or ("ssl" if use_ssl else "starttls")
    _validate_mail_options(
        security_mode, smtp_security_mode, smtp_auth_method, smtp_port, smtp_timeout
    )
    account = PopAccount(
        name=name, host=host, port=port, username=username,
        encrypted_password=encrypt_password(password), use_ssl=security_mode == "ssl",
        security_mode=security_mode, pop_require_spa=pop_require_spa,
        smtp_host=smtp_host.strip() or None, smtp_port=smtp_port,
        smtp_security_mode=smtp_security_mode, smtp_timeout=smtp_timeout,
        smtp_require_spa=smtp_require_spa, smtp_auth_required=smtp_auth_required,
        smtp_auth_method=smtp_auth_method, smtp_username=smtp_username.strip() or None,
        encrypted_smtp_password=encrypt_password(smtp_password) if smtp_password else None,
        enabled=enabled,
        delete_after_receive=delete_after_receive,
    )
    db.add(account)
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/{account_id}")
def update_account(
    account_id: int, name: str = Form(...), host: str = Form(...), port: int = Form(...),
    username: str = Form(...), password: str = Form(""), use_ssl: bool = Form(False),
    security_mode: str | None = Form(None),
    pop_require_spa: bool = Form(False), smtp_host: str = Form(""), smtp_port: int = Form(587),
    smtp_security_mode: str = Form("auto"), smtp_timeout: int = Form(30),
    smtp_require_spa: bool = Form(False), smtp_auth_required: bool = Form(False),
    smtp_auth_method: str = Form("same_as_pop"), smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    enabled: bool = Form(False), delete_after_receive: bool = Form(False),
    db: Session = Depends(get_db),
):
    account = db.get(PopAccount, account_id)
    if not account:
        raise HTTPException(404)
    name, host, username = _validate_account(name, host, port, username)
    security_mode = security_mode or ("ssl" if use_ssl else "starttls")
    _validate_mail_options(
        security_mode, smtp_security_mode, smtp_auth_method, smtp_port, smtp_timeout
    )
    account.name, account.host, account.port, account.username = name, host, port, username
    account.use_ssl, account.security_mode = security_mode == "ssl", security_mode
    account.pop_require_spa = pop_require_spa
    account.smtp_host, account.smtp_port = smtp_host.strip() or None, smtp_port
    account.smtp_security_mode, account.smtp_timeout = smtp_security_mode, smtp_timeout
    account.smtp_require_spa, account.smtp_auth_required = smtp_require_spa, smtp_auth_required
    account.smtp_auth_method, account.smtp_username = smtp_auth_method, smtp_username.strip() or None
    account.enabled = enabled
    account.delete_after_receive = delete_after_receive
    if password:
        account.encrypted_password = encrypt_password(password)
    if smtp_password:
        account.encrypted_smtp_password = encrypt_password(smtp_password)
    account.last_error = None
    db.commit()
    return RedirectResponse("/settings?notice=POP%20%EC%84%A4%EC%A0%95%EC%9D%B4%20%EC%A0%80%EC%9E%A5%EB%90%98%EC%97%88%EC%8A%B5%EB%8B%88%EB%8B%A4", status_code=303)


@app.post("/settings/{account_id}/delete")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(PopAccount, account_id)
    if not account:
        raise HTTPException(404)
    if account.messages:
        account.enabled = False
    else:
        db.delete(account)
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/{account_id}/fetch")
def fetch(account_id: int, db: Session = Depends(get_db)):
    account = db.get(PopAccount, account_id)
    if not account:
        raise HTTPException(404)
    try:
        count = fetch_account(db, account)
        return RedirectResponse(f"/mail?notice={count}%EA%B1%B4%20%EC%88%98%EC%8B%A0", status_code=303)
    except Exception:
        db.refresh(account)
        error = quote(account.last_error or "POP 연결에 실패했습니다.")
        return RedirectResponse(f"/settings?error={error}", status_code=303)


@app.get("/mail", response_class=HTMLResponse)
def mail_list(request: Request, page: int = Query(1, ge=1), q: str = "", account_id: str = "",
              db: Session = Depends(get_db)):
    page = max(1, page)
    query = select(EmailMessage)
    count_query = select(func.count()).select_from(EmailMessage)
    conditions = []
    q = q.strip()
    if q:
        pattern = f"%{q}%"
        conditions.append(or_(EmailMessage.subject.ilike(pattern), EmailMessage.sender.ilike(pattern),
                              EmailMessage.text_body.ilike(pattern)))
    selected_account_id = int(account_id) if account_id.isdigit() else None
    if selected_account_id is not None:
        conditions.append(EmailMessage.account_id == selected_account_id)
    if conditions:
        query = query.where(*conditions)
        count_query = count_query.where(*conditions)
    total = db.scalar(count_query) or 0
    messages = db.scalars(
        query.options(selectinload(EmailMessage.account))
        .order_by(EmailMessage.received_at.desc()).offset((page - 1) * 20).limit(20)
    ).all()
    return templates.TemplateResponse(request, "mail_list.html", {
        "messages": messages, "page": page, "pages": max(1, (total + 19) // 20),
        "notice": request.query_params.get("notice"), "q": q,
        "account_id": selected_account_id,
        "accounts": db.scalars(select(PopAccount).order_by(PopAccount.name)).all(),
    })


@app.post("/mail/bulk-delete")
def bulk_delete_messages(
    scope: str = Form(...), message_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    if scope not in {"selected", "all"}:
        raise HTTPException(422, "삭제 범위를 올바르게 선택하세요.")
    query = select(EmailMessage)
    if scope == "selected":
        if not message_ids:
            notice = quote("삭제할 메일을 선택해 주세요")
            return RedirectResponse(
                f"/mail?notice={notice}", status_code=303,
            )
        query = query.where(EmailMessage.id.in_(set(message_ids)))
    messages = db.scalars(query).all()
    for message in messages:
        db.delete(message)
    db.commit()
    notice = quote(f"메일 {len(messages)}건을 삭제했습니다.")
    return RedirectResponse(f"/mail?notice={notice}", status_code=303)


@app.get("/mail/{message_id}", response_class=HTMLResponse)
def mail_detail(message_id: int, request: Request, db: Session = Depends(get_db)):
    message = db.scalar(select(EmailMessage).options(
        selectinload(EmailMessage.attachments), selectinload(EmailMessage.account)
    ).where(EmailMessage.id == message_id))
    if not message:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "mail_detail.html", {"message": message})


@app.get("/mail/{message_id}/raw")
def raw_message(message_id: int, db: Session = Depends(get_db)):
    message = db.get(EmailMessage, message_id)
    if not message:
        raise HTTPException(404)
    return Response(message.raw_message, media_type="message/rfc822", headers={
        "Content-Disposition": f'attachment; filename="message-{message.id}.eml"',
        "X-Content-Type-Options": "nosniff",
    })


@app.post("/mail/{message_id}/delete")
def delete_message(message_id: int, db: Session = Depends(get_db)):
    message = db.get(EmailMessage, message_id)
    if not message:
        raise HTTPException(404)
    db.delete(message)
    db.commit()
    return RedirectResponse("/mail?notice=%EB%A9%94%EC%9D%BC%EC%9D%84%20%EC%82%AD%EC%A0%9C%ED%96%88%EC%8A%B5%EB%8B%88%EB%8B%A4", status_code=303)


@app.get("/attachments/{attachment_id}")
def attachment(attachment_id: int, db: Session = Depends(get_db)):
    item = db.get(Attachment, attachment_id)
    if not item:
        raise HTTPException(404)
    safe_name = item.filename.replace('"', "").replace("\r", "").replace("\n", "")
    return Response(item.content, media_type=item.mime_type, headers={
        "Content-Disposition": f'attachment; filename="attachment"; filename*=UTF-8\'\'{quote(safe_name)}',
        "X-Content-Type-Options": "nosniff",
    })
