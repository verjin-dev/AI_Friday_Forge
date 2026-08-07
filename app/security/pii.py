from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.models import SecurityFinding, SecuritySeverity


@dataclass(frozen=True, slots=True)
class Detector:
    name: str
    pattern: re.Pattern[str]
    severity: SecuritySeverity
    #: ``pi`` = personal identity attributes, ``pii`` = uniquely identifying,
    #: ``secret`` = credential material.
    category: str
    validator: str | None = None


def _luhn(value: str) -> bool:
    digits = [int(ch) for ch in value if ch.isdigit()]
    if len(digits) < 12:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _verhoeff(value: str) -> bool:
    """Aadhaar checksum — avoids flagging every 12-digit consignment number."""

    d_table = [
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
        [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
        [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
        [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
        [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
        [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
        [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
        [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
        [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
    ]
    p_table = [
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
        [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
        [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
        [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
        [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
        [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
        [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
    ]
    digits = [int(ch) for ch in value if ch.isdigit()]
    if len(digits) != 12:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        checksum = d_table[checksum][p_table[index % 8][digit]]
    return checksum == 0


_VALIDATORS = {"luhn": _luhn, "verhoeff": _verhoeff}


DETECTORS: tuple[Detector, ...] = (
    Detector(
        "email",
        re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"),
        SecuritySeverity.MEDIUM,
        "pii",
    ),
    Detector(
        "phone",
        re.compile(r"(?<!\d)(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){9,13}\d(?!\d)"),
        SecuritySeverity.MEDIUM,
        "pii",
    ),
    Detector(
        "payment_card",
        re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
        SecuritySeverity.CRITICAL,
        "pii",
        validator="luhn",
    ),
    Detector(
        "aadhaar",
        re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
        SecuritySeverity.CRITICAL,
        "pii",
        validator="verhoeff",
    ),
    Detector(
        "pan",
        re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
        SecuritySeverity.HIGH,
        "pii",
    ),
    Detector(
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        SecuritySeverity.CRITICAL,
        "pii",
    ),
    Detector(
        "iban",
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        SecuritySeverity.HIGH,
        "pii",
    ),
    Detector(
        "passport",
        re.compile(r"\b[A-PR-WY][1-9]\d{6}\b"),
        SecuritySeverity.HIGH,
        "pii",
    ),
    Detector(
        "driving_licence",
        re.compile(r"\b[A-Z]{2}[-\s]?\d{2}[-\s]?\d{4}[-\s]?\d{7}\b"),
        SecuritySeverity.HIGH,
        "pii",
    ),
    Detector(
        "vehicle_registration",
        re.compile(r"\b[A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{1,3}[\s-]?\d{4}\b"),
        SecuritySeverity.LOW,
        "pi",
    ),
    Detector(
        "postal_address",
        re.compile(
            r"\b\d{1,5}\s+[A-Za-z0-9.\s]{3,40}\b"
            r"(?:street|st\.|road|rd\.|avenue|ave\.|lane|ln\.|nagar|colony|sector)\b",
            re.IGNORECASE,
        ),
        SecuritySeverity.MEDIUM,
        "pi",
    ),
    Detector(
        "date_of_birth",
        re.compile(
            r"\b(?:dob|date of birth)\b[\s:]*\d{1,4}[-/]\d{1,2}[-/]\d{1,4}",
            re.IGNORECASE,
        ),
        SecuritySeverity.HIGH,
        "pi",
    ),
    Detector(
        "ip_address",
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        SecuritySeverity.LOW,
        "pi",
    ),
    Detector(
        "api_key",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}"
            r"|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
        SecuritySeverity.CRITICAL,
        "secret",
    ),
    Detector(
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api[_-]?key|token|bearer)\b\s*[:=]\s*\S{6,}",
            re.IGNORECASE,
        ),
        SecuritySeverity.CRITICAL,
        "secret",
    ),
)


def _mask(value: str) -> str:
    stripped = value.strip()
    if len(stripped) <= 4:
        return "*" * len(stripped)
    return f"{stripped[:2]}{'*' * max(4, len(stripped) - 4)}{stripped[-2:]}"


def detect_personal_data(text: str) -> list[SecurityFinding]:
    """Detect PI, PII and credential material in free text."""

    findings: list[SecurityFinding] = []
    if not text:
        return findings

    seen: set[tuple[str, str]] = set()
    for detector in DETECTORS:
        for match in detector.pattern.finditer(text):
            value = match.group(0)
            if detector.validator:
                check = _VALIDATORS.get(detector.validator)
                if check and not check(value):
                    continue
            key = (detector.name, value)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                SecurityFinding(
                    check=f"{detector.category}:{detector.name}",
                    severity=detector.severity,
                    detail=(
                        f"Possible {detector.name.replace('_', ' ')} detected "
                        f"({detector.category.upper()})."
                    ),
                    span=_mask(value),
                )
            )
    return findings


def redact(text: str) -> str:
    """Replace detected personal data with typed placeholders."""

    if not text:
        return text

    redacted = text
    for detector in DETECTORS:

        def _replace(match: re.Match[str]) -> str:
            value = match.group(0)
            if detector.validator:
                check = _VALIDATORS.get(detector.validator)
                if check and not check(value):
                    return value
            return f"[REDACTED:{detector.name.upper()}]"

        redacted = detector.pattern.sub(_replace, redacted)
    return redacted
