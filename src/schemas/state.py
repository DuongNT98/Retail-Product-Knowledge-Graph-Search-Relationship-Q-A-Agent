"""AgentCore Platform v1.0 - RET-C2-566 state schema.

Retail Product Knowledge Graph Search & Relationship Q&A Agent. Multi-hop
relationship retrieval over a vector-indexed KB with explicit relationship
nodes (no graph DB). Flat TypedDict extension of AgentState (ADR-005).
Structured payloads (dict/list) are JSON-string-encoded before being
stored in state fields.
"""

import json
from typing import Any, NotRequired

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class State(AgentState):
    """Agent state for RET-C2-566.

    query_entities: JSON list[str] - entities extracted from the query
      (e.g. ["sesame", "China", "29-allergen mandate"]).
    hop_chain: JSON list[dict] - the sequence of relationship-node lookups
      performed, each {source_entity, relation_type, target_entities}.
    resolved_skus: JSON list[dict] - final SKUs matching ALL hops in the
      chain, each {sku_id, supplier, category, matched_hops}.
    formatted_answer: str - final structured answer (excerpt + citation
      only, no confidential supplier contract terms).
    formatted_output: str - post_process's own copy of formatted_answer,
      echoed so the S-3 `_extra_security_gate_output()` hook re-checks the
      key execute() actually returns (own-output-field re-check).
    """

    # NotRequired[] is only valid inside a TypedDict definition; mypy can't see
    # that AgentState is one at runtime without SDK-side py.typed stubs, so it
    # flags every field here. Stub-visibility limitation, not a code error —
    # each field really is optional/JSON-safe at runtime.
    query_entities: NotRequired[str]  # type: ignore[valid-type]
    hop_chain: NotRequired[str]  # type: ignore[valid-type]
    resolved_skus: NotRequired[str]  # type: ignore[valid-type]
    formatted_answer: NotRequired[str]  # type: ignore[valid-type]
    formatted_output: NotRequired[str]  # type: ignore[valid-type]
