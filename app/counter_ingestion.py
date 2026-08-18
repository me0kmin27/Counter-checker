"""Extract copier counter readings from the vendor notification formats."""

import html
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import BotRule, CounterReading, Device, EmailMessage, ExtractionRun, ProcessingEvent


ADAPTER_VERSION = "2"
COUNTER_LABELS = {
    "black": ("흑백", r"black(?:\s*&\s*white)?", "b/?w", "mono(?:chrome)?"),
    "color": ("컬러", "칼라", "colou?r"),
    "total": (r"총\s*카운터", r"총\s*매수", r"누적\s*카운터", r"total(?:\s*counter)?"),
}
SERIAL_LABELS = (r"시리얼(?:\s*번호)?", r"제조\s*번호", r"serial(?:\s*(?:number|no\.?))?", "s/?n")
# A serial is an identifier, not a number. Both alphabet-leading values such as
# W2P123456 and digit-leading values are valid and must be treated identically.
SERIAL_VALUE_PATTERN = r"[A-Z0-9](?:[A-Z0-9._/-]*[A-Z0-9])?"


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


def _html_to_text(content: bytes) -> str:
    """Decode an attached HTM report and expose its rendered text to parsers."""
    charset_match = re.search(
        br"charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)", content[:4096], re.IGNORECASE,
    )
    candidates = [charset_match.group(1).decode("ascii", "ignore")] if charset_match else []
    candidates.extend(["utf-8", "cp949", "euc-kr", "latin-1"])
    source = ""
    for encoding in dict.fromkeys(candidates):
        try:
            source = content.decode(encoding)
            break
        except (LookupError, UnicodeDecodeError):
            continue
    source = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", source,
                    flags=re.IGNORECASE | re.DOTALL)
    source = re.sub(r"<(?:br|/p|/div|/tr|/li|/h[1-6])\b[^>]*>", "\n", source,
                    flags=re.IGNORECASE)
    return html.unescape(re.sub(r"<[^>]+>", " ", source))


def attachment_to_text(filename: str, mime_type: str, content: bytes) -> str:
    """Return safe, selectable text from a supported counter-report attachment."""
    name = filename.casefold()
    mime = mime_type.casefold().split(";", 1)[0].strip()
    if name.endswith(".rtf") or mime in {"application/rtf", "text/rtf"}:
        return _rtf_to_text(content)
    if name.endswith((".htm", ".html")) or mime in {"text/html", "application/xhtml+xml"}:
        return _html_to_text(content)
    raise ValueError("HTM, HTML, RTF 파일만 읽을 수 있습니다.")


def _html_attachments(message: EmailMessage) -> str:
    return "\n".join(
        _html_to_text(item.content) for item in message.attachments
        if item.mime_type.casefold() in {"text/html", "application/xhtml+xml"}
        or item.filename.casefold().endswith((".htm", ".html"))
    )


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
        rf"Serial\s+Number\s*:\s*({SERIAL_VALUE_PATTERN})", source, re.IGNORECASE,
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
        rf"(?:{serial_pattern})\s*[:：#]?\s*({SERIAL_VALUE_PATTERN})",
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


def _custom_rule(message: EmailMessage, rules: list[BotRule]) -> ParsedCounters | None:
    subject, sender = message.subject or "", message.sender or ""

    def source_for(source_type: str, filename_keyword: str | None = None) -> str:
        def matching_attachments(supported) -> list:
            candidates = [item for item in message.attachments if supported(item)]
            if not filename_keyword:
                return candidates
            selected = [
                item for item in candidates
                if filename_keyword.casefold() in item.filename.casefold()
            ]
            # The rule builder historically saved the sample's complete
            # filename.  Kyocera commonly includes the device serial in that
            # filename, so a rule made from 11Y... rejects WDM...'s otherwise
            # identical report.  When the message has exactly one attachment
            # of the requested format there is no ambiguity: use that report.
            return selected or (candidates if len(candidates) == 1 else [])

        if source_type == "rtf":
            return "\n".join(
                _rtf_to_text(item.content) for item in matching_attachments(
                    lambda item: (
                    item.filename.lower().endswith(".rtf")
                    or item.mime_type.casefold().split(";", 1)[0] in {"application/rtf", "text/rtf"}
                    )
                )
            )
        if source_type == "html_attachment":
            return "\n".join(
                _html_to_text(item.content) for item in matching_attachments(
                    lambda item: (
                    item.mime_type.casefold().split(";", 1)[0] in {"text/html", "application/xhtml+xml"}
                    or item.filename.casefold().endswith((".htm", ".html"))
                    )
                )
            )
        if source_type == "ocr":
            return _ocr_images(message)
        return f"{subject}\n{message.text_body}\n{html.unescape(re.sub('<[^>]+>', ' ', message.html_body))}"

    for rule in rules:
        if not rule.enabled or (rule.subject_keyword and rule.subject_keyword.casefold() not in subject.casefold()) or (rule.sender_keyword and rule.sender_keyword.casefold() not in sender.casefold()):
            continue
        counter_filename = rule.attachment_filename if rule.source_type in {
            "html_attachment", "rtf", "ocr",
        } else None
        serial_source_type = rule.serial_source_type or rule.source_type
        serial_filename = None
        if serial_source_type in {"html_attachment", "rtf", "ocr"}:
            serial_filename = rule.serial_attachment_filename or counter_filename
        counter_source = source_for(rule.source_type, counter_filename)
        serial_source = source_for(
            serial_source_type, serial_filename,
        )
        if not counter_source and not serial_source:
            continue
        try:
            serial_match = re.search(
                rule.serial_pattern, serial_source, re.IGNORECASE | re.MULTILINE,
            )
            serial_number = serial_match.group(1).strip() if serial_match else None
            # Rules saved from an older sample can be accidentally tied to that
            # sample's value (for example, a pattern that accepts 11Y... but not
            # WDM...).  A failed custom target must not hide a standard labelled
            # serial that the built-in parser can read from the very same source.
            # Keep an explicit custom match authoritative, and only use this as a
            # compatibility fallback for common Serial Number/S/N labels.
            if not serial_number:
                serial_number = _extract(
                    serial_source, f"custom-{rule.brand}-serial-fallback", 0.95,
                ).serial_number
            counters = {}
            for counter_type, pattern in (("black", rule.black_pattern), ("color", rule.color_pattern), ("total", rule.total_pattern)):
                match = re.search(
                    pattern, counter_source, re.IGNORECASE | re.MULTILINE,
                ) if pattern else None
                if match:
                    counters[counter_type] = _number(match.group(1))
        except (re.error, IndexError, ValueError):
            continue
        if serial_number or counters:
            evidence = f"{serial_source}\n{counter_source}"[:4000]
            return ParsedCounters(
                f"custom-{rule.brand}", serial_number, counters, evidence, 0.95,
            )
    return None


def parse_counter_message(message: EmailMessage, rules: list[BotRule] | None = None) -> ParsedCounters:
    custom = _custom_rule(message, rules or [])
    if custom:
        return custom
    attachment_text = []
    has_rtf = False
    for item in message.attachments:
        if item.mime_type in {"application/rtf", "text/rtf"} or item.filename.lower().endswith(".rtf"):
            has_rtf = True
            attachment_text.append(_rtf_to_text(item.content))

    subject_and_body = f"{message.subject}\n{message.text_body}\n{html.unescape(re.sub('<[^>]+>', ' ', message.html_body))}\n{_html_attachments(message)}"
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
    rules = db.scalars(select(BotRule).where(BotRule.enabled.is_(True)).order_by(BotRule.id)).all()
    parsed = parse_counter_message(message, rules)
    # Last-resort identity recovery is based on the registered fleet, not on a
    # vendor prefix.  This makes digit-leading (11Y...) and letter-leading
    # (WDM...) serials equivalent even when a device changes the surrounding
    # mail label or a legacy custom rule misses it.  Only an unambiguous serial
    # present in the decoded message is accepted.
    devices = db.scalars(select(Device)).all() if not parsed.serial_number else []
    if devices:
        identity_source = "\n".join([
            message.subject or "", message.text_body or "", message.html_body or "",
            _html_attachments(message),
            *(
                _rtf_to_text(item.content) for item in message.attachments
                if item.filename.casefold().endswith(".rtf")
                or item.mime_type.casefold().split(";", 1)[0] in {"application/rtf", "text/rtf"}
            ),
        ])
        normalized_source = normalize_serial(identity_source)
        matched_devices = [
            item for item in devices
            if len(normalize_serial(item.serial_number)) >= 4
            and normalize_serial(item.serial_number) in normalized_source
        ]
        if len(matched_devices) == 1:
            parsed = replace(parsed, serial_number=matched_devices[0].serial_number)
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
    devices = devices or (db.scalars(select(Device)).all() if serial_key else [])
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
