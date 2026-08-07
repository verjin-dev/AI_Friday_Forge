from __future__ import annotations

from app.agents.base import AgentOutcome, BaseAgent
from app.core.config import settings
from app.core.models import (
    AgentName,
    GuardrailResult,
    SecurityFinding,
    SecuritySeverity,
)
from app.core.state import PlatformState
from app.security.guardrails import apply_output_guardrails
from app.security.injection import (
    detect_injection,
    detect_jailbreak,
    detect_tool_abuse,
    sanitize_prompt,
)
from app.security.pii import detect_personal_data, process_pii_actions
from app.security.rbac import resolve_role
from app.security.toxicity import detect_toxicity


class GuardrailAgent(BaseAgent):
    """Guardrail Agent responsible for pre-orchestration and post-orchestration security.

    Responsible for:
    - PI Protection (Prompt Injection, Jailbreak, Tool Abuse, Prompt Sanitization)
    - PII Detection & Actions (Aadhaar, PAN, Passport, Phone, Email, Address, Credit Card, SSN, Bank Account, Medical IDs -> Mask, Remove, Encrypt, Audit Log)
    - Toxicity Detection
    - Data Leakage Prevention & Sensitive Information Filtering
    - Output Validation
    """

    name = AgentName.GUARDRAIL

    def should_skip(self, state: PlatformState) -> str | None:
        return None  # Guardrail Agent always executes

    async def run(self, state: PlatformState) -> AgentOutcome:
        question = state.get("question", "")
        role_name = state.get("role") or settings.security_default_role
        role = resolve_role(role_name)
        trace_id = state.get("trace_id", "-")

        # 1. PI Protection & Sanitization
        sanitized_prompt = sanitize_prompt(question)
        pi_findings = detect_injection(sanitized_prompt, source="user")
        jailbreak_findings = detect_jailbreak(sanitized_prompt)
        tool_abuse_findings = detect_tool_abuse(sanitized_prompt)

        combined_pi_findings = [*pi_findings, *jailbreak_findings]

        # 2. Toxicity Detection
        toxicity_findings = detect_toxicity(sanitized_prompt)

        # 3. PII Protection & Actions
        pii_action = getattr(settings, "security_pii_action", "mask") if hasattr(settings, "security_pii_action") else "mask"
        processed_question, pii_findings = process_pii_actions(
            sanitized_prompt,
            action=pii_action,
            trace_id=trace_id,
            role=role.name,
        )

        # 4. Output Validation & Data Leakage Prevention (if answer exists in state)
        answer = state.get("answer")
        data_leakage_findings: list[SecurityFinding] = []
        output_validated = True
        working_answer = answer

        if answer:
            working_answer, data_leakage_findings = apply_output_guardrails(answer, role=role.name)
            if any(f.severity in (SecuritySeverity.HIGH, SecuritySeverity.CRITICAL) for f in data_leakage_findings):
                output_validated = False

        # Evaluate blocking criteria
        blocking_findings = [
            f for f in (*combined_pi_findings, *tool_abuse_findings, *toxicity_findings)
            if f.severity in (SecuritySeverity.HIGH, SecuritySeverity.CRITICAL)
        ]
        blocked = bool(blocking_findings) and settings.security_block_on_injection
        blocked_reason = "; ".join(f.detail for f in blocking_findings) if blocked else None

        guardrail_result = GuardrailResult(
            allowed=not blocked,
            sanitized_prompt=sanitized_prompt,
            pi_findings=combined_pi_findings,
            pii_findings=pii_findings,
            toxicity_findings=toxicity_findings,
            tool_abuse_findings=tool_abuse_findings,
            data_leakage_findings=data_leakage_findings,
            pii_action_taken=pii_action,
            output_validated=output_validated,
            blocked_reason=blocked_reason,
        )

        updates: dict = {
            "guardrail": guardrail_result,
            "question": processed_question,
        }

        if answer and working_answer != answer:
            updates["answer"] = working_answer

        if blocked:
            updates["blocked"] = True
            updates["blocked_reason"] = blocked_reason
            updates["answer"] = (
                "This request was blocked by the platform's Guardrail Agent.\n\n"
                f"Reason: {blocked_reason}\n\n"
                "Please revise your input to comply with enterprise safety policies."
            )

        total_findings = (
            len(combined_pi_findings)
            + len(pii_findings)
            + len(toxicity_findings)
            + len(tool_abuse_findings)
            + len(data_leakage_findings)
        )

        summary = (
            f"BLOCKED: {blocked_reason}"
            if blocked
            else f"Guardrail passed ({total_findings} finding(s), PII action '{pii_action}')"
        )

        return AgentOutcome(
            updates=updates,
            summary=summary,
            detail={
                "allowed": not blocked,
                "pii_action": pii_action,
                "sanitized": sanitized_prompt != question,
                "findings_count": total_findings,
                "output_validated": output_validated,
            },
        )
