from __future__ import annotations

from app.agents.base import AgentOutcome, BaseAgent
from app.core.config import settings
from app.core.models import (
    AgentName,
    SecurityFinding,
    SecuritySeverity,
    SecurityVerdict,
)
from app.core.state import PlatformState
from app.security.audit import record_verdict
from app.security.injection import detect_injection, detect_jailbreak
from app.security.pii import detect_personal_data, redact
from app.security.rbac import allowed_tools_for, resolve_role, validate_tool_access


_MAX_INPUT_CHARS = 8000


class SecurityAgent(BaseAgent):
    """Input gate: validation → PI → PII → injection → jailbreak → RBAC.

    Runs before any tool or graph access. A blocked request short-circuits the
    whole workflow.
    """

    name = AgentName.SECURITY

    def should_skip(self, state: PlatformState) -> str | None:
        return None  # The security gate always runs.

    async def run(self, state: PlatformState) -> AgentOutcome:
        question = state.get("question", "")
        role_name = state.get("role") or settings.security_default_role
        role = resolve_role(role_name)
        findings: list[SecurityFinding] = []

        # 1. Input validation
        if not question.strip():
            findings.append(
                SecurityFinding(
                    check="input:empty",
                    severity=SecuritySeverity.MEDIUM,
                    detail="Empty request.",
                )
            )
        if len(question) > _MAX_INPUT_CHARS:
            findings.append(
                SecurityFinding(
                    check="input:oversized",
                    severity=SecuritySeverity.MEDIUM,
                    detail=(
                        f"Request is {len(question)} characters; truncated to "
                        f"{_MAX_INPUT_CHARS} before processing."
                    ),
                )
            )
            question = question[:_MAX_INPUT_CHARS]

        # 2 & 3. PI and PII detection
        personal = detect_personal_data(question)
        findings.extend(personal)

        # 4. Prompt injection, 5. Jailbreak
        injection = detect_injection(question, source="user")
        jailbreak = detect_jailbreak(question)
        findings.extend(injection)
        findings.extend(jailbreak)

        # 6. Role validation, 7. Tool permission validation
        permitted_tools = allowed_tools_for(role_name)
        plan = state.get("plan")
        if plan and plan.suggested_tools:
            _, denied = validate_tool_access(role_name, plan.suggested_tools)
            findings.extend(denied)

        blocking = [
            finding
            for finding in (*injection, *jailbreak)
            if finding.severity in (SecuritySeverity.HIGH, SecuritySeverity.CRITICAL)
        ]
        blocked = bool(blocking) and settings.security_block_on_injection

        working_question = question
        redacted: str | None = None
        if personal and settings.security_redact_pii and not role.can_see_pii:
            redacted = redact(question)
            # Downstream agents only ever see the redacted text.
            working_question = redacted

        verdict = SecurityVerdict(
            allowed=not blocked,
            findings=findings,
            redacted_text=redacted,
            blocked_reason=(
                "; ".join(finding.detail for finding in blocking) if blocked else None
            ),
            allowed_tools=permitted_tools,
            role=role.name,
        )

        record_verdict(verdict, trace_id=state.get("trace_id", "-"))

        updates: dict = {"security": verdict, "question": working_question}
        if blocked:
            updates["blocked"] = True
            updates["blocked_reason"] = verdict.blocked_reason
            updates["answer"] = (
                "This request was blocked by the platform's security policy.\n\n"
                f"Reason: {verdict.blocked_reason}\n\n"
                "If this is legitimate enterprise work, rephrase the request "
                "without instructions that attempt to override platform rules, "
                "or raise it with your platform administrator."
            )

        severity = verdict.max_severity.value
        summary = (
            f"BLOCKED ({severity}): {verdict.blocked_reason}"
            if blocked
            else (
                f"Cleared for role '{role.name}' — {len(findings)} finding(s), "
                f"max severity {severity}"
                + (", PII redacted" if redacted else "")
            )
        )

        return AgentOutcome(
            updates=updates,
            summary=summary,
            detail={
                "role": role.name,
                "allowed": verdict.allowed,
                "pii_redacted": bool(redacted),
                "permitted_tools": permitted_tools,
                "findings": [
                    {
                        "check": finding.check,
                        "severity": finding.severity.value,
                        "detail": finding.detail,
                    }
                    for finding in findings
                ],
            },
        )
