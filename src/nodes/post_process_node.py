"""AgentCore Platform v1.0 - RET-C2-566 ResponseValidateOutputFormatNode (post_process).

S-3 output gate: non-suppressible re-check that formatted_answer contains
no raw supplier contract terms/pricing. Own-dict field re-check only
(S-3 self-consistency rule - [[s3-hook-cross-state-recheck-fragile]]).
"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import strip_contract_terms


class ResponseValidateOutputFormatNode(FunctionNode):
    """Validate the answer contains no raw supplier contract terms/pricing."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        formatted_answer = state.get("formatted_answer", "")

        _, blocked = strip_contract_terms(formatted_answer)

        # S-4 (CoE R2 sot-path): domain trace before the branch so both the
        # blocked-reject and compliant-success paths are audited. Counts/flag
        # only, no raw answer content.
        emit_trace_event(
            "output_validate_completed",
            {"correlation_id": state.get("correlation_id"), "blocked": blocked},
            state,
        )

        if blocked:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ResponseValidateOutputFormatNode: answer contains raw supplier contract terms/pricing"],
            }

        return {
            "formatted_output": formatted_answer,
            "status": AgentStatus.SUCCESS.value,
        }

    def _extra_security_gate_output(self, state: dict[str, Any]) -> dict[str, Any]:
        """Non-suppressible re-check on the output dict's own field.

        CoE R1 FINDING-03: re-check the key execute() actually emits
        (`formatted_output`), not the upstream input key `formatted_answer` —
        the latter is absent from the returned dict, so the scan silently
        skipped (S-3 self-consistency rule - [[s3-hook-cross-state-recheck-fragile]]).
        """
        formatted_output = state.get("formatted_output", "")
        _, blocked = strip_contract_terms(formatted_output)
        if blocked:
            emit_trace_event("output_validate_s3_recheck_blocked", {}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "ResponseValidateOutputFormatNode: S-3 re-check blocked an answer with raw contract terms/pricing"
                ],
            }
        return state
