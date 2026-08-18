"""Extract copier counter readings from the vendor notification formats."""

import html
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import CounterReading, Device, EmailMessage, ExtractionRun, ProcessingEvent


ADAPTER_VERSION = "2"
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
    captured_at: datetime | None = None


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


def _number(value: str) -> int:
    return int(re.sub(r"\D", "", value))


def _captured_at(value: str, formats: tuple[str, ...]) -> datetime | None:
    value = re.sub(r"\s+", " ", value.strip())
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _sindoh(source: str) -> ParsedCounters:
    """Parse Sindoh's bracket-labelled, comma-delimited plain-text report."""
    values = {
        label.casefold(): value.strip()
        for label, value in re.findall(
            r"\[([^\]]+)]\s*,?\s*(.*?)(?=\s*\[[^\]]+]|$)", source, re.DOTALL,
        )
    }
    counters = {}
    for label, counter_type in (
        ("total counter", "total"),
        ("total color counter", "color"),
        ("total black counter", "black"),
    ):
        if values.get(label) and re.search(r"\d", values[label]):
            counters[counter_type] = _number(values[label])
    captured = _captured_at(values.get("send date", ""), ("%d/%m/%y", "%y/%m/%d"))
    return ParsedCounters(
        "sindoh-plain", values.get("serial number"), counters, source[:4000], 0.99, captured,
    )


def _kyocera(source: str) -> ParsedCounters:
    """Parse Kyocera's aligned header and Counters by Function section."""
    serial = re.search(
        r"Serial\s+Number\s*:\s*([A-Z0-9._/-]+)", source, re.IGNORECASE,
    )
    meter_date = re.search(
        r"MeterDate\s*:\s*(?:[A-Za-z]{3}\s+)?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2})",
        source, re.IGNORECASE,
    )
    function_section = re.split(r"Counters\s+by\s+Function\s*:", source, flags=re.IGNORECASE)
    total = re.findall(r"\bTotal\s*:\s*([\d,]+)", function_section[-1], re.IGNORECASE)
    counters = {"total": _number(total[-1])} if total else {}
    captured = _captured_at(meter_date.group(1), ("%d %b %Y %H:%M:%S",)) if meter_date else None
    return ParsedCounters(
        "kyocera", serial.group(1) if serial else None, counters, source[:4000], 0.99, captured,
    )


def _extract(source: str, adapter: str, confidence: float) -> ParsedCounters:
    source = source.replace(r"\:", ":").replace(r"\@", "@")
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
    if "meterdate" in probe or "counters by function" in probe or "kyocera" in probe or "교세라" in probe:
        parsed = _kyocera(subject_and_body)
        if {"black", "color", "total"} <= parsed.counters.keys():
            return parsed
        ocr_text = _ocr_images(message)
        if not ocr_text:
            return parsed
        ocr = _extract(ocr_text, "kyocera-ocr", 0.80)
        # Header values are higher-confidence than OCR and therefore win on
        # conflicts; OCR only fills fields absent from the mail body.
        counters = {**ocr.counters, **parsed.counters}
        return ParsedCounters(
            "kyocera-ocr", parsed.serial_number or ocr.serial_number, counters,
            f"{parsed.evidence}\n{ocr.evidence}"[:4000], 0.80, parsed.captured_at,
        )
    if "[serial number]" in probe or "[total counter]" in probe:
        return _sindoh(subject_and_body)
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
        captured_at = parsed.captured_at or message.sent_at or message.received_at
        captured_at = captured_at if captured_at.tzinfo else captured_at.replace(tzinfo=timezone.utc)
        needs_review = not {"black", "color", "total"} <= parsed.counters.keys()
        decreased = False
        for counter_type, value in parsed.counters.items():
            previous = db.scalar(
                select(CounterReading).where(
                    CounterReading.device_id == device.id,
                    CounterReading.counter_type == counter_type,
                    CounterReading.status == "confirmed",
                ).order_by(CounterReading.captured_at.desc()).limit(1)
            )
            anomalous = previous is not None and value < previous.value
            decreased = decreased or anomalous
            needs_review = needs_review or anomalous
            db.add(CounterReading(
                run=run, device=device, counter_type=counter_type, value=value,
                captured_at=captured_at, confidence=parsed.confidence,
                status="needs_review" if needs_review or parsed.confidence < .9 else "confirmed",
                raw_text=parsed.evidence,
            ))
        if needs_review:
            for reading in run.readings:
                reading.status = "needs_review"
        run.status = "needs_review" if needs_review else "done"
        run.error_code = "counter_decreased" if decreased else (
            "counter_type_missing" if needs_review else None
        )
    db.add(ProcessingEvent(
        email_id=message.id, from_status=None, to_status=run.status,
        event_metadata={"adapter": parsed.adapter, "error_code": run.error_code},
    ))
    db.commit()
    return run
