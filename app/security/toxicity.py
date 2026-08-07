from __future__ import annotations

import re

from app.core.models import SecurityFinding, SecuritySeverity

_TOXICITY_PATTERNS: tuple[tuple[str, str, SecuritySeverity], ...] = (
    (
        "profanity_or_abuse",
        r"\b(?:fuck|shit|bitch|bastard|asshole|dick|pussy|cunt|motherfucker)\b",
        SecuritySeverity.MEDIUM,
    ),
    (
        "hate_speech_or_harassment",
        r"\b(?:nigger|kike|faggot|retard|chink|spic)\b",
        SecuritySeverity.CRITICAL,
    ),
    (
        "threat_or_violence",
        r"\b(?:i will (?:kill|murder|harm|stab|shoot|destroy) you|death to|bomb\s+the)\b",
        SecuritySeverity.CRITICAL,
    ),
    (
        "self_harm",
        r"\b(?:how to (?:commit suicide|kill myself|cut myself|end my life))\b",
        SecuritySeverity.CRITICAL,
    ),
)

_COMPILED_TOXICITY = [
    (name, re.compile(pattern, re.IGNORECASE), severity)
    for name, pattern, severity in _TOXICITY_PATTERNS
]


def detect_toxicity(text: str) -> list[SecurityFinding]:
    """Scan text for toxic, violent, profane, or abusive content."""
    findings: list[SecurityFinding] = []
    if not text:
        return findings

    for name, pattern, severity in _COMPILED_TOXICITY:
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 15)
            end = min(len(text), match.end() + 15)
            span = f"...{text[start:end]}..."
            findings.append(
                SecurityFinding(
                    check=f"toxicity:{name}",
                    severity=severity,
                    detail=f"Toxicity/abuse pattern '{name}' detected in content.",
                    span=span,
                )
            )
    return findings
