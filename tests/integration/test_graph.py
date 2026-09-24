# RET-C2-566 - Integration test: full graph compile + invoke.
#
# POST-03 remediation: the prior oracle accepted status in
# ("success", "error", "cancelled") and asserted only history length, so it
# would pass even if the advertised matching query failed end-to-end. These
# tests pin the success path (status == success, expected SKU present) and add
# explicit KB-unavailable / connector-failure error cases (POST-02).

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from src.services.kb_adapter import KBRequestError

RELATIONSHIP_KB = [
    {"source_entity_id": "sesame", "relation_type": "ingredient_of", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
    {"source_entity_id": "China", "relation_type": "imported_from", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
    {"source_entity_id": "29-allergen mandate", "relation_type": "regulated_by", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
]
SKU_KB = [{"sku_id": "SKU-001", "supplier": "sup-a", "category": "snacks"}]

MATCHING_QUERY = "Which SKUs contain sesame and are imported from China under the 29-allergen mandate?"
NO_MATCH_QUERY = "Which SKUs contain peanut and are imported from Vietnam?"
EMPTY_QUERY = ""


def _ctx(session_id: str) -> InvocationContext:
    return InvocationContext(
        session_id=session_id, caller_trust_level=TrustLevel.INTERNAL, caller_id="compliance-001"
    )


class _RaisingKBClient:
    def relationship_records(self):
        raise KBRequestError("product-KG timeout")

    def sku_records(self):
        raise KBRequestError("product-KG timeout")


class TestAgentIntegration:
    def test_matching_relationship_query_returns_expected_sku(self):
        agent = Graph(config={"max_retry": 1, "relationship_kb": RELATIONSHIP_KB, "sku_kb": SKU_KB})
        agent.compile()
        result = agent.invoke(MATCHING_QUERY, ctx=_ctx("it-1"))

        # POST-03: pin the advertised business path, not merely completion.
        assert result["status"] == "success"
        out = result.get("output") or ""
        assert out.strip(), "matching query must emit a non-empty answer"
        assert "SKU-001" in out
        assert "No matching SKUs found" not in out

    def test_no_match_query_completes_success(self):
        agent = Graph(config={"max_retry": 1, "relationship_kb": RELATIONSHIP_KB, "sku_kb": SKU_KB})
        agent.compile()
        result = agent.invoke(NO_MATCH_QUERY, ctx=_ctx("it-2"))
        # A legitimate empty-result success (KB available, no match) — distinct
        # from an unavailable data source.
        assert result["status"] == "success"

    def test_empty_query_error(self):
        agent = Graph(config={"max_retry": 1, "relationship_kb": RELATIONSHIP_KB, "sku_kb": SKU_KB})
        agent.compile()
        result = agent.invoke(EMPTY_QUERY, ctx=_ctx("it-3"))
        assert result["status"] == "error"

    def test_kb_unavailable_errors(self):
        # POST-02: no live client AND no injected KB → unavailable data source
        # must ERROR, never a false-negative "No matching SKUs found" success.
        agent = Graph(config={"max_retry": 1})
        agent.compile()
        result = agent.invoke(MATCHING_QUERY, ctx=_ctx("it-4"))
        assert result["status"] == "error"

    def test_connector_failure_errors(self):
        # POST-02: a live connector that fails must surface ERROR.
        agent = Graph(config={"max_retry": 1, "kb_client": _RaisingKBClient()})
        agent.compile()
        result = agent.invoke(MATCHING_QUERY, ctx=_ctx("it-5"))
        assert result["status"] == "error"
