from __future__ import annotations

import re

from app.core.config import settings
from app.core.models import SecurityFinding, SecuritySeverity
from app.security.pii import detect_personal_data, redact
from app.security.rbac import resolve_role
from app.security.toxicity import detect_toxicity


_LEAK_PATTERNS: tuple[tuple[str, str, SecuritySeverity], ...] = (
    (
        "connection_string",
        r"\b(?:neo4j|bolt|postgres(?:ql)?|mysql|mongodb)(?:\+s{1,2})?://[^\s\"']+",
        SecuritySeverity.CRITICAL,
    ),
    (
        "system_prompt_echo",
        r"\byou are the (?:planner|security|guardrail|knowledge|reasoning|validation) agent\b",
        SecuritySeverity.HIGH,
    ),
    (
        "internal_path",
        r"[A-Za-z]:\\Users\\[^\s\"']+|/(?:home|root|etc)/[^\s\"']+",
        SecuritySeverity.MEDIUM,
    ),
)

_COMPILED_LEAKS = [
    (name, re.compile(pattern, re.IGNORECASE), severity)
    for name, pattern, severity in _LEAK_PATTERNS
]


def apply_output_guardrails(
    answer: str, *, role: str | None = None
) -> tuple[str, list[SecurityFinding]]:
    """Final gate before a response leaves the platform.

    Returns the (possibly redacted) answer plus everything that was caught, so
    the Explanation Agent can be transparent about what was withheld.
    """

    findings: list[SecurityFinding] = []
    if not answer or not answer.strip():
        return answer, [
            SecurityFinding(
                check="output:empty",
                severity=SecuritySeverity.MEDIUM,
                detail="The platform produced an empty response.",
            )
        ]

    safe = answer

    # 1. Toxicity check on output
    toxic_findings = detect_toxicity(safe)
    findings.extend(toxic_findings)

    # 2. Data leakage prevention
    for name, pattern, severity in _COMPILED_LEAKS:
        if pattern.search(safe):
            findings.append(
                SecurityFinding(
                    check=f"output:{name}",
                    severity=severity,
                    detail=f"Response contained '{name}' and was redacted.",
                )
            )
            safe = pattern.sub(f"[REDACTED:{name.upper()}]", safe)

    # 3. PII protection
    resolved = resolve_role(role)
    personal = detect_personal_data(safe)
    if personal:
        if settings.security_redact_pii and not resolved.can_see_pii:
            safe = redact(safe)
            findings.extend(
                SecurityFinding(
                    check=f"output:{finding.check}",
                    severity=finding.severity,
                    detail=(
                        f"{finding.detail} Redacted because role "
                        f"'{resolved.name}' is not cleared for personal data."
                    ),
                    span=finding.span,
                )
                for finding in personal
            )
        else:
            findings.extend(
                SecurityFinding(
                    check=f"output:{finding.check}",
                    severity=SecuritySeverity.INFO,
                    detail=(
                        f"{finding.detail} Released because role "
                        f"'{resolved.name}' is cleared for personal data."
                    ),
                    span=finding.span,
                )
                for finding in personal
            )

    return safe, findings

