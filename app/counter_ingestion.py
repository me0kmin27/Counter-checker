"""Extract copier counter readings from the vendor notification formats."""

import html
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import CounterReading, Device, EmailMessage, ExtractionRun, ProcessingEvent


ADAPTER_VERSION = "1"
COUNTER_LABELS = {
    "black": ("흑백", r"black(?:\s*&\s*white)?", "b/?w", "mono(?:chrome)?"),
    "color": ("컬러", "칼라", "colou?r"),
    "total": (r"총\s*카운터", r"총\s*매수", r"누적\s*카운터", r"total(?:\s*counter)?"),
}
SERIAL_LABELS = (r"시리얼(?:\s*번호)?", r"제조\s*번호", r"serial(?:\s*(?:number|no\.?))?", "s/?n")


@dataclass(frozen=True)
class ParsedCounters:
    adapter: str
    serial_number: str | None
    counters: dict[str, int]
    evidence: str
    confidence: float


def normalize_serial(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def _rtf_to_text(content: bytes) -> str:
    """Convert the text-bearing subset of an RTF counter page without executing it."""
    source = content.decode("latin-1", errors="replace")
    source = re.sub(
        r"\\u(-?\d+)\??",
        lambda match: chr(int(match.group(1)) % 65536),
        source,
    )
    source = re.sub(
        r"(?:\\'[0-9a-fA-F]{2})+",
        lambda match: bytes.fromhex("".join(re.findall(r"[0-9a-fA-F]{2}", match.group())))
        .decode("cp949", errors="replace"),
        source,
    )
    source = re.sub(r"\\(?:par|line|tab)\b\s?", "\n", source)
    source = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", source)
    return re.sub(r"[{}]", "", source)


def _ocr_images(message: EmailMessage) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        return ""
    output = []
    for item in message.attachments:
        if not item.mime_type.startswith("image/"):
            continue
        suffix = Path(item.filename).suffix or ".img"
        with tempfile.TemporaryDirectory(prefix="counter-ocr-") as directory:
            source = Path(directory) / f"input{suffix}"
            source.write_bytes(item.content)
            result = subprocess.run(
                [executable, str(source), "stdout", "-l", "kor+eng"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if result.returncode == 0:
                output.append(result.stdout)
    return "\n".join(output)


def _extract(source: str, adapter: str, confidence: float) -> ParsedCounters:
    serial = None
    serial_pattern = "|".join(SERIAL_LABELS)
    match = re.search(
        rf"(?:{serial_pattern})\s*[:：#]?\s*([A-Z0-9][A-Z0-9._ /-]{{2,}})",
        source, re.IGNORECASE,
    )
    if match:
        serial = match.group(1).strip().splitlines()[0].strip(" .")

    counters = {}
    for counter_type, labels in COUNTER_LABELS.items():
        label_pattern = "|".join(labels)
        matches = re.findall(
            rf"(?:{label_pattern})\s*[:：=]?\s*([0-9][0-9,. ]*)",
            source, re.IGNORECASE,
        )
        if matches:
            digits = re.sub(r"\D", "", matches[-1])
            if digits:
                counters[counter_type] = int(digits)
    if "total" not in counters and {"black", "color"} <= counters.keys():
        counters["total"] = counters["black"] + counters["color"]
        confidence = min(confidence, 0.90)
    evidence = "\n".join(line.strip() for line in source.splitlines() if line.strip())[:4000]
    return ParsedCounters(adapter, serial, counters, evidence, confidence)


def parse_counter_message(message: EmailMessage) -> ParsedCounters:
    attachment_text = []
    has_rtf = False
    for item in message.attachments:
        if item.mime_type in {"application/rtf", "text/rtf"} or item.filename.lower().endswith(".rtf"):
            has_rtf = True
            attachment_text.append(_rtf_to_text(item.content))

    subject_and_body = f"{message.subject}\n{message.text_body}\n{html.unescape(re.sub('<[^>]+>', ' ', message.html_body))}"
    probe = subject_and_body.casefold()
    if has_rtf or "samsung" in probe or "삼성" in probe:
        return _extract("\n".join([subject_and_body, *attachment_text]), "samsung-rtf", 0.98)
    if "kyocera" in probe or "교세라" in probe:
        parsed = _extract(subject_and_body, "kyocera", 0.98)
        if parsed.serial_number and len(parsed.counters) >= 2:
            return parsed
        return _extract(f"{subject_and_body}\n{_ocr_images(message)}", "kyocera-ocr", 0.80)
    return _extract(subject_and_body, "sindoh-plain", 0.99)


def process_counter_message(db: Session, message: EmailMessage) -> ExtractionRun | None:
    """Parse one stored email and persist readings, preserving failures for review."""
    message = db.scalar(
        select(EmailMessage).options(selectinload(EmailMessage.attachments))
        .where(EmailMessage.id == message.id)
    )
    parsed = parse_counter_message(message)
    # Ordinary inbox traffic must not pollute the extraction queue. A vendor
    # notification is actionable only when it contains at least one key field.
    if not parsed.serial_number and not parsed.counters:
        return None
    run = ExtractionRun(
        email_id=message.id, adapter=parsed.adapter, adapter_version=ADAPTER_VERSION,
        ocr_engine="tesseract" if parsed.adapter.endswith("-ocr") else None,
        status="processing",
    )
    db.add(run)
    db.flush()
    serial_key = normalize_serial(parsed.serial_number or "")
    devices = db.scalars(select(Device)).all() if serial_key else []
    device = next((item for item in devices if normalize_serial(item.serial_number) == serial_key), None)
    if not parsed.serial_number:
        run.status, run.error_code = "needs_review", "serial_missing"
    elif not device:
        run.status, run.error_code = "needs_review", "unknown_serial"
    elif not parsed.counters:
        run.status, run.error_code = "needs_review", "counters_missing"
    else:
        captured_at = message.sent_at or message.received_at
        captured_at = captured_at if captured_at.tzinfo else captured_at.replace(tzinfo=timezone.utc)
        needs_review = not {"black", "color", "total"} <= parsed.counters.keys()
        for counter_type, value in parsed.counters.items():
            previous = db.scalar(
                select(CounterReading).where(
                    CounterReading.device_id == device.id,
                    CounterReading.counter_type == counter_type,
                    CounterReading.status == "confirmed",
                ).order_by(CounterReading.captured_at.desc()).limit(1)
            )
            anomalous = previous is not None and value < previous.value
            needs_review = needs_review or anomalous
            db.add(CounterReading(
                run=run, device=device, counter_type=counter_type, value=value,
                captured_at=captured_at, confidence=parsed.confidence,
                status="needs_review" if anomalous or parsed.confidence < .9 else "confirmed",
                raw_text=parsed.evidence,
            ))
        run.status = "needs_review" if needs_review else "done"
        run.error_code = "counter_decreased" if needs_review and any(
            reading.status == "needs_review" for reading in run.readings
        ) else ("counter_type_missing" if needs_review else None)
    db.add(ProcessingEvent(
        email_id=message.id, from_status=None, to_status=run.status,
        event_metadata={"adapter": parsed.adapter, "error_code": run.error_code},
    ))
    db.commit()
    return run
