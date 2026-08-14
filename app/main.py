import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .database import Base, SessionLocal, engine, get_db
from .models import Attachment, EmailMessage, PopAccount
from .pop_service import fetch_account
from .security import encrypt_password


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
    messages = db.scalar(select(func.count()).select_from(EmailMessage)) or 0
    latest = db.scalars(select(EmailMessage).order_by(EmailMessage.received_at.desc()).limit(5)).all()
    errors = db.scalar(select(func.count()).select_from(PopAccount).where(PopAccount.last_error.is_not(None))) or 0
    return templates.TemplateResponse(request, "dashboard.html", {
        "accounts": accounts, "messages": messages, "latest": latest, "errors": errors,
    })


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "settings.html", {
        "accounts": db.scalars(select(PopAccount).order_by(PopAccount.id)).all()
    })


@app.post("/settings")
def add_account(
    name: str = Form(...), host: str = Form(...), port: int = Form(995),
    username: str = Form(...), password: str = Form(...),
    use_ssl: bool = Form(False), enabled: bool = Form(False),
    delete_after_receive: bool = Form(False), db: Session = Depends(get_db),
):
    account = PopAccount(
        name=name.strip(), host=host.strip(), port=port, username=username.strip(),
        encrypted_password=encrypt_password(password), use_ssl=use_ssl, enabled=enabled,
        delete_after_receive=delete_after_receive,
    )
    db.add(account)
    db.commit()
    return RedirectResponse("/settings", status_code=303)


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
        return RedirectResponse("/settings?error=POP%20%EC%97%B0%EA%B2%B0%20%EC%8B%A4%ED%8C%A8", status_code=303)


@app.get("/mail", response_class=HTMLResponse)
def mail_list(request: Request, page: int = 1, db: Session = Depends(get_db)):
    page = max(1, page)
    total = db.scalar(select(func.count()).select_from(EmailMessage)) or 0
    messages = db.scalars(
        select(EmailMessage).options(selectinload(EmailMessage.account))
        .order_by(EmailMessage.received_at.desc()).offset((page - 1) * 20).limit(20)
    ).all()
    return templates.TemplateResponse(request, "mail_list.html", {
        "messages": messages, "page": page, "pages": max(1, (total + 19) // 20),
        "notice": request.query_params.get("notice"),
    })


@app.get("/mail/{message_id}", response_class=HTMLResponse)
def mail_detail(message_id: int, request: Request, db: Session = Depends(get_db)):
    message = db.scalar(select(EmailMessage).options(
        selectinload(EmailMessage.attachments), selectinload(EmailMessage.account)
    ).where(EmailMessage.id == message_id))
    if not message:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "mail_detail.html", {"message": message})


@app.get("/attachments/{attachment_id}")
def attachment(attachment_id: int, db: Session = Depends(get_db)):
    item = db.get(Attachment, attachment_id)
    if not item:
        raise HTTPException(404)
    safe_name = item.filename.replace('"', "")
    return Response(item.content, media_type=item.mime_type, headers={
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
    })
