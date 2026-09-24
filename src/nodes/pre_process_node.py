"""AgentCore Platform v1.0 - RET-C2-566 QueryNormalizeEntityExtractNode (pre_process).

Combines QueryNormalize (S-1/S-2: sanitize query, no supplier PII) +
EntityExtract (deterministic entity extraction: ingredients, countries,
regulatory mandates) per the scaffold issue's node flow, since both
operate on the raw input with no branching between them.
"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import to_json
from src.services.service import extract_entities


class QueryNormalizeEntityExtractNode(FunctionNode):
    """Validate the query and extract relationship-query entities."""

    # Corrected from the scaffold issue's invalid "VERIFIED_INTERNAL"
    # literal - TrustLevel has no VERIFIED_INTERNAL member; INTERNAL is
    # the nearest valid value (retail buyer/compliance officer callers only).
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        user_input = state.get("user_input", "")

        # S-4 (CoE R1 S4-2): domain trace before the validation guard so
        # rejects are audited too. Length + ids only, no supplier PII.
        emit_trace_event(
            "relationship_query_received",
            {"correlation_id": state.get("correlation_id"), "input_len": len(user_input or "")},
            state,
        )

        if not user_input or not user_input.strip():
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["QueryNormalizeEntityExtractNode: user_input is empty or missing"],
            }

        entities = extract_entities(user_input)
        if not entities:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["QueryNormalizeEntityExtractNode: no recognizable relationship entities found in query"],
            }

        return {
            "query_entities": to_json(entities),
            "status": AgentStatus.SUCCESS.value,
        }
