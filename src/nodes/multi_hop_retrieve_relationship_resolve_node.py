"""AgentCore Platform v1.0 - RET-C2-566 MultiHopRetrieveRelationshipResolveNode (main).

Combines MultiHopRetrieve (3 sequential deterministic relationship-node
lookups) + RelationshipResolve (deterministic SKU intersection across all
hops) + LLMAnswer (LLM-optional grounded synthesis) per the scaffold
issue's node flow, since all three operate on the same query_entities
input with no branching between them.
"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.services.llm.azure_openai_client import AzureOpenAIClient
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json
from src.services.kb_adapter import KBRequestError
from src.services.service import format_answer, multi_hop_retrieve, resolve_intersecting_skus

_AZURE_OPENAI_SECRETS = ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")


class MultiHopRetrieveRelationshipResolveNode(FunctionNode):
    """Perform the multi-hop relationship lookup, resolve intersecting SKUs, and synthesize the answer."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def __init__(
        self,
        relationship_kb: list[dict[str, Any]] | None = None,
        sku_kb: list[dict[str, Any]] | None = None,
        llm: Any = None,
        # Duck-typed: HttpProductKGClient, _StubKGClient, or a test double — no
        # common base class in kb_adapter, so Any (not object) avoids attr-defined
        # false positives on relationship_records()/sku_records() below.
        kb_client: Any | None = None,
    ) -> None:
        super().__init__()
        self._relationship_kb = relationship_kb or []
        self._sku_kb = sku_kb or []
        # Test-double seam only: production wiring never passes `llm` (see
        # _resolve_llm — the answer-LLM is built fresh per invocation from
        # ctx.secrets, never cached on the node instance).
        self._llm = llm
        # POST-02: live product-KG client (built from the bound secret provider
        # at the entry point). When both client and injected KBs are absent the
        # data source is unavailable and the node must ERROR, never return a
        # false-negative "No matching SKUs found" success.
        self._kb_client = kb_client

    def _resolve_llm(self, state: dict[str, Any]) -> tuple[object | None, str | None]:
        """Return (llm_or_None, config_error_or_None) for this invocation.

        `self._llm` (test-double) always wins. Otherwise build a fresh
        AzureOpenAIClient from ctx.secrets — never cache: node instances are
        reused across invocations, so a cached client would leak one
        caller's secret to the next.

        Tri-state, per RET-C2-566's own POST-02 R2 contract (deliberately
        stricter than the generic Step 8e "any failure degrades silently"
        pattern):
          - none of the three AZURE_OPENAI_* secrets present (or the
            identity fields InvocationContext needs are absent, e.g. a bare
            unit-test/PB-6 state) -> "not configured" -> (None, None), the
            valid deterministic-fallback path.
          - SOME but not all three present -> real misconfiguration, not a
            deliberate no-LLM choice -> (None, config_error) -> ERROR.
          - all three present but the client itself fails to construct
            (e.g. an endpoint URL containing "/openai" - AzureOpenAIClient
            rejects rather than rewrites) -> (None, config_error) -> ERROR.
        A client that constructs but fails at .complete() time is handled
        downstream by format_answer()'s own degradation path, unchanged.
        """
        if self._llm is not None:
            return self._llm, None
        try:
            ctx = InvocationContext.from_state(state)
        except Exception:
            return None, None

        secrets = ctx.secrets
        values: dict[str, str] = {}
        missing: list[str] = []
        for key in _AZURE_OPENAI_SECRETS:
            try:
                values[key] = secrets.require(key)
            except Exception:
                missing.append(key)

        if len(missing) == len(_AZURE_OPENAI_SECRETS):
            return None, None  # not configured at all -> deterministic fallback
        if missing:
            return None, f"AZURE_OPENAI_* partially configured; missing: {', '.join(missing)}"

        try:
            return (
                AzureOpenAIClient(
                    {
                        "api_key": values["AZURE_OPENAI_API_KEY"],
                        "azure_endpoint": values["AZURE_OPENAI_ENDPOINT"],
                        "azure_deployment": values["AZURE_OPENAI_DEPLOYMENT"],
                    }
                ),
                None,
            )
        except Exception as exc:
            return None, str(exc)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        query_entities = from_json(state.get("query_entities", ""), None)

        # S-4 (CoE R2 sot-path): domain trace before the first branch so the
        # missing-entities / KB-unavailable reject paths are audited too, not
        # only the eventual multi_hop_completed success event below.
        emit_trace_event(
            "multi_hop_retrieval_started",
            {
                "correlation_id": state.get("correlation_id"),
                "entity_count": len(query_entities) if isinstance(query_entities, list) else 0,
            },
            state,
        )

        if not isinstance(query_entities, list) or not query_entities:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["MultiHopRetrieveRelationshipResolveNode: query_entities missing or empty"],
            }

        # Resolve the KB source: live client first, else injected lists.
        relationship_kb, sku_kb = self._relationship_kb, self._sku_kb
        if self._kb_client is not None:
            try:
                relationship_kb = self._kb_client.relationship_records()
                sku_kb = self._kb_client.sku_records()
            except KBRequestError as exc:
                return {
                    "status": AgentStatus.ERROR.value,
                    "error_log": [f"MultiHopRetrieveRelationshipResolveNode: product-KG unavailable: {exc}"],
                }
        elif not relationship_kb and not sku_kb:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "MultiHopRetrieveRelationshipResolveNode: product-KG unavailable — "
                    "no connector configured and no KB injected"
                ],
            }

        hop_chain, matched_sku_sets = multi_hop_retrieve(query_entities, relationship_kb=relationship_kb)
        resolved_skus = resolve_intersecting_skus(matched_sku_sets, sku_kb=sku_kb)

        llm, llm_config_error = self._resolve_llm(state)
        if llm_config_error is not None:
            emit_trace_event(
                "answer_synthesis_llm_degraded",
                {"correlation_id": state.get("correlation_id"), "reason": llm_config_error},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"MultiHopRetrieveRelationshipResolveNode: answer-LLM misconfigured: {llm_config_error}"],
            }
        answer, llm_degradation = format_answer(resolved_skus, llm=llm)

        if llm_degradation is not None:
            # A CONFIGURED LLM failed to produce the synthesis (error / empty / wrong
            # shape). Per the 3m half-fix rule, this must surface as ERROR — a
            # deterministic fallback is only valid when NO LLM is configured
            # (self._llm is None); silently downgrading to SUCCESS-with-fallback here
            # would make an LLM outage indistinguishable from a real answer.
            emit_trace_event(
                "answer_synthesis_llm_degraded",
                {"correlation_id": state.get("correlation_id"), "reason": llm_degradation},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"MultiHopRetrieveRelationshipResolveNode: LLM synthesis failed: {llm_degradation}"],
            }

        # S-4 (CoE R1 S4-1): multi-hop retrieval, SKU intersection, and LLM
        # synthesis are side-effect domain events. Counts only, no KB content/PII.
        emit_trace_event(
            "multi_hop_completed",
            {
                "correlation_id": state.get("correlation_id"),
                "hop_count": len(hop_chain) if isinstance(hop_chain, list) else 0,
                "resolved_sku_count": len(resolved_skus) if isinstance(resolved_skus, list) else 0,
            },
            state,
        )

        return {
            "hop_chain": to_json(hop_chain),
            "resolved_skus": to_json(resolved_skus),
            "formatted_answer": answer,
            "status": AgentStatus.SUCCESS.value,
        }
