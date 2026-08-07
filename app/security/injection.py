from __future__ import annotations

import re
import unicodedata

from app.core.models import SecurityFinding, SecuritySeverity


#: Attempts to override the platform's own instructions.
_INJECTION_PATTERNS: tuple[tuple[str, str, SecuritySeverity], ...] = (
    (
        "instruction_override",
        r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}"
        r"\b(?:previous|prior|above|earlier|all|any)\b[^.\n]{0,20}"
        r"\b(?:instruction|prompt|rule|direction|guideline|context)s?\b",
        SecuritySeverity.HIGH,
    ),
    (
        "system_prompt_exfiltration",
        r"\b(?:reveal|show|print|repeat|output|dump|disclose)\b[^.\n]{0,30}"
        r"\b(?:system prompt|initial instruction|your instruction|hidden prompt"
        r"|developer message|configuration)\b",
        SecuritySeverity.HIGH,
    ),
    (
        "role_reassignment",
        r"\byou are (?:now|no longer)\b|\bact as (?:an? )?(?:unrestricted|jailbroken"
        r"|different)\b|\bpretend (?:to be|you are)\b",
        SecuritySeverity.MEDIUM,
    ),
    (
        "fake_authority",
        r"\b(?:as|this is)\b[^.\n]{0,20}\b(?:the )?(?:system|admin|administrator"
        r"|developer|anthropic|openai)\b[^.\n]{0,30}"
        r"\b(?:i|you|we)\b[^.\n]{0,30}\b(?:authoriz|permit|allow|instruct)",
        SecuritySeverity.HIGH,
    ),
    (
        "credential_harvesting",
        r"\b(?:give|send|show|list|export)\b[^.\n]{0,30}"
        r"\b(?:api key|password|credential|secret|token|connection string"
        r"|\.env|environment variable)s?\b",
        SecuritySeverity.CRITICAL,
    ),
    (
        "graph_write_attempt",
        r"\b(?:create|merge|delete|detach delete|drop|set)\b[^.\n]{0,20}"
        r"\b(?:node|relationship|constraint|index|database|graph)\b",
        SecuritySeverity.HIGH,
    ),
    (
        "data_exfiltration",
        r"\b(?:send|post|upload|email|forward|exfiltrate)\b[^.\n]{0,40}"
        r"\b(?:to|at)\b[^.\n]{0,20}(?:https?://|\b[\w.-]+@[\w.-]+\.\w+)",
        SecuritySeverity.CRITICAL,
    ),
    (
        "tool_abuse",
        r"\b(?:run|execute|invoke)\b[^.\n]{0,25}"
        r"\b(?:shell|bash|powershell|cmd|os\.system|subprocess|rm -rf|del /)\b",
        SecuritySeverity.CRITICAL,
    ),
)

#: Attempts to remove safety behaviour rather than redirect instructions.
_JAILBREAK_PATTERNS: tuple[tuple[str, str, SecuritySeverity], ...] = (
    (
        "persona_jailbreak",
        r"\b(?:dan mode|do anything now|developer mode|god mode|no restrictions"
        r"|unfiltered|without any (?:filter|restriction|limitation))\b",
        SecuritySeverity.HIGH,
    ),
    (
        "safety_removal",
        r"\b(?:ignore|disable|turn off|remove|bypass)\b[^.\n]{0,25}"
        r"\b(?:safety|guardrail|filter|policy|restriction|compliance|security)\b",
        SecuritySeverity.CRITICAL,
    ),
    (
        "hypothetical_wrapper",
        r"\b(?:hypothetically|in a fictional|for research purposes only"
        r"|this is just a test|purely educational)\b[^.\n]{0,60}"
        r"\b(?:how (?:to|do i)|steps to|explain how)\b",
        SecuritySeverity.MEDIUM,
    ),
    (
        "encoded_payload",
        r"\b(?:base64|rot13|hex[- ]?decode|reverse the (?:string|text))\b"
        r"[^.\n]{0,40}\b(?:then|and)\b[^.\n]{0,20}\b(?:execute|run|follow|obey)\b",
        SecuritySeverity.HIGH,
    ),
)

_COMPILED_INJECTION = [
    (name, re.compile(pattern, re.IGNORECASE), severity)
    for name, pattern, severity in _INJECTION_PATTERNS
]
_COMPILED_JAILBREAK = [
    (name, re.compile(pattern, re.IGNORECASE), severity)
    for name, pattern, severity in _JAILBREAK_PATTERNS
]

#: Zero-width and bidirectional control characters used to hide payloads.
_HIDDEN_CHARS = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")


def _normalise(text: str) -> str:
    """Fold obfuscation tricks before matching."""

    decomposed = unicodedata.normalize("NFKC", text)
    without_hidden = _HIDDEN_CHARS.sub("", decomposed)
    # Collapse spaced-out letters such as "i g n o r e".
    return re.sub(r"\s+", " ", without_hidden)


def _excerpt(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - 20)
    end = min(len(text), match.end() + 20)
    return f"...{text[start:end].strip()}..."


def detect_injection(text: str, *, source: str = "user") -> list[SecurityFinding]:
    """Detect prompt-injection attempts in user input or retrieved content."""

    findings: list[SecurityFinding] = []
    if not text:
        return findings

    normalised = _normalise(text)

    if _HIDDEN_CHARS.search(text):
        findings.append(
            SecurityFinding(
                check=f"injection:hidden_characters[{source}]",
                severity=SecuritySeverity.HIGH,
                detail="Zero-width or bidirectional control characters found — a "
                "common way to hide instructions from human reviewers.",
            )
        )

    for name, pattern, severity in _COMPILED_INJECTION:
        match = pattern.search(normalised)
        if match:
            findings.append(
                SecurityFinding(
                    check=f"injection:{name}[{source}]",
                    severity=severity,
                    detail=f"Prompt-injection pattern '{name}' detected in {source} content.",
                    span=_excerpt(normalised, match),
                )
            )
    return findings


def detect_jailbreak(text: str) -> list[SecurityFinding]:
    """Detect attempts to strip the platform's safety behaviour."""

    findings: list[SecurityFinding] = []
    if not text:
        return findings

    normalised = _normalise(text)
    for name, pattern, severity in _COMPILED_JAILBREAK:
        match = pattern.search(normalised)
        if match:
            findings.append(
                SecurityFinding(
                    check=f"jailbreak:{name}",
                    severity=severity,
                    detail=f"Jailbreak pattern '{name}' detected.",
                    span=_excerpt(normalised, match),
                )
            )
    return findings


def scan_retrieved_content(text: str, *, source: str) -> list[SecurityFinding]:
    """Indirect-injection scan for graph rows, documents and web snippets.

    Retrieved content is data, never instructions — anything imperative aimed at
    the model is flagged so downstream agents can quarantine it.
    """

    findings = detect_injection(text, source=source)
    normalised = _normalise(text)
    imperative = re.search(
        r"\b(?:assistant|ai|model|claude|gpt)\b[^.\n]{0,20}"
        r"\b(?:must|should|shall|please)\b[^.\n]{0,40}",
        normalised,
        re.IGNORECASE,
    )
    if imperative:
        findings.append(
            SecurityFinding(
                check=f"injection:embedded_instruction[{source}]",
                severity=SecuritySeverity.MEDIUM,
                detail=(
                    f"Retrieved {source} content addresses the model directly; "
                    "treated as data, not instructions."
                ),
                span=_excerpt(normalised, imperative),
            )
        )
    return findings


_TOOL_ABUSE_PATTERNS: tuple[tuple[str, str, SecuritySeverity], ...] = (
    (
        "os_command_execution",
        r"\b(?:sudo|rm\s+-rf|del\s+/|cmd\.exe|powershell|bash\s+-c|eval\(|exec\()|curl\s+.*\|\s*sh\b",
        SecuritySeverity.CRITICAL,
    ),
    (
        "sql_cypher_injection",
        r"\b(?:DROP\s+TABLE|DETACH\s+DELETE|UNION\s+SELECT|INSERT\s+INTO|ALTER\s+TABLE|DELETE\s+FROM)\b",
        SecuritySeverity.HIGH,
    ),
    (
        "unauthorized_tool_invocation",
        r"\b(?:invoke_tool|call_function|override_permissions|escalate_privilege)\b",
        SecuritySeverity.HIGH,
    ),
)
_COMPILED_TOOL_ABUSE = [
    (name, re.compile(pattern, re.IGNORECASE), severity)
    for name, pattern, severity in _TOOL_ABUSE_PATTERNS
]


def detect_tool_abuse(text: str) -> list[SecurityFinding]:
    """Detect attempts to abuse system tools or inject unauthorized operational commands."""
    findings: list[SecurityFinding] = []
    if not text:
        return findings
    normalised = _normalise(text)
    for name, pattern, severity in _COMPILED_TOOL_ABUSE:
        match = pattern.search(normalised)
        if match:
            findings.append(
                SecurityFinding(
                    check=f"tool_abuse:{name}",
                    severity=severity,
                    detail=f"Tool abuse vector '{name}' detected in request.",
                    span=_excerpt(normalised, match),
                )
            )
    return findings


def sanitize_prompt(text: str) -> str:
    """Sanitize user prompt by removing hidden control characters and instruction overrides."""
    if not text:
        return text

    # Remove hidden zero-width and control characters
    sanitized = _HIDDEN_CHARS.sub("", text)
    
    # Strip explicit instruction override prefixes if present
    override_pattern = re.compile(
        r"^(?:ignore|disregard|forget|override)\s+(?:all\s+)?(?:previous|prior|above|earlier|all)\s+instructions?[.:,\s]*",
        re.IGNORECASE,
    )
    sanitized = override_pattern.sub("", sanitized).strip()

    return sanitized

