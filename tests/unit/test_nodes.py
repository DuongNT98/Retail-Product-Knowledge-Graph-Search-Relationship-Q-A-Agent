# RET-C2-566 - Unit tests: per-node success + error/edge paths.

from framework.schemas.agent_status import AgentStatus

import src.nodes.multi_hop_retrieve_relationship_resolve_node as multi_hop_module
import src.nodes.post_process_node as post_process_module
import src.nodes.pre_process_node as pre_process_module
from src.nodes.multi_hop_retrieve_relationship_resolve_node import MultiHopRetrieveRelationshipResolveNode
from src.nodes.post_process_node import ResponseValidateOutputFormatNode
from src.nodes.pre_process_node import QueryNormalizeEntityExtractNode
from src.schemas.state import from_json, to_json


def _capture_emit(monkeypatch, module):
    calls = []
    monkeypatch.setattr(module, "emit_trace_event", lambda name, payload, state: calls.append((name, payload)))
    return calls


def _assert_non_sensitive(payload: dict) -> None:
    """Payload must be counts/flags/correlation_id only - no raw content/PII."""
    for key, value in payload.items():
        if key == "correlation_id":
            continue
        assert isinstance(value, (int, bool)), f"payload[{key!r}]={value!r} is not a count/flag"

RELATIONSHIP_KB = [
    {"source_entity_id": "sesame", "relation_type": "ingredient_of", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
    {"source_entity_id": "China", "relation_type": "imported_from", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
    {"source_entity_id": "29-allergen mandate", "relation_type": "regulated_by", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
    {"source_entity_id": "sesame", "relation_type": "ingredient_of", "target_entity_id": "SKU-002", "sku_id": "SKU-002"},
]
SKU_KB = [
    {"sku_id": "SKU-001", "supplier": "sup-a", "category": "snacks"},
    {"sku_id": "SKU-002", "supplier": "sup-b", "category": "snacks"},
]


class TestQueryNormalizeEntityExtractNode:
    def test_success(self):
        state = {"user_input": "Which SKUs contain sesame and are imported from China under the 29-allergen mandate?"}
        r = QueryNormalizeEntityExtractNode().execute(state)
        assert r["status"] == AgentStatus.SUCCESS
        entities = from_json(r["query_entities"], [])
        assert "sesame" in entities

    def test_empty_input_error(self):
        assert QueryNormalizeEntityExtractNode().execute({"user_input": ""})["status"] == AgentStatus.ERROR

    def test_no_entities_error(self):
        assert QueryNormalizeEntityExtractNode().execute({"user_input": "unrelated question"})["status"] == AgentStatus.ERROR

    def test_emits_on_success(self, monkeypatch):
        calls = _capture_emit(monkeypatch, pre_process_module)
        QueryNormalizeEntityExtractNode().execute(
            {"user_input": "Which SKUs contain sesame and are imported from China under the 29-allergen mandate?"}
        )
        assert calls
        assert calls[0][0] == "relationship_query_received"
        _assert_non_sensitive(calls[0][1])

    def test_emits_on_empty_input_reject(self, monkeypatch):
        calls = _capture_emit(monkeypatch, pre_process_module)
        QueryNormalizeEntityExtractNode().execute({"user_input": ""})
        assert calls, "reject path must be traced too, not only success"
        _assert_non_sensitive(calls[0][1])


class TestMultiHopRetrieveRelationshipResolveNode:
    def test_success_intersection_resolved(self):
        entities = ["sesame", "China", "29-allergen mandate"]
        state = {"query_entities": to_json(entities)}
        r = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB).execute(state)
        assert r["status"] == AgentStatus.SUCCESS
        resolved = from_json(r["resolved_skus"], [])
        assert len(resolved) == 1
        assert resolved[0]["sku_id"] == "SKU-001"

    def test_success_no_intersection(self):
        entities = ["sesame", "nonexistent-entity"]
        state = {"query_entities": to_json(entities)}
        r = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB).execute(state)
        assert r["status"] == AgentStatus.SUCCESS
        assert from_json(r["resolved_skus"], []) == []

    def test_missing_entities_error(self):
        assert MultiHopRetrieveRelationshipResolveNode().execute({"query_entities": ""})["status"] == AgentStatus.ERROR

    def test_kb_unavailable_error(self):
        # POST-02: valid entities but no client and no injected KB → the data
        # source is unavailable; must ERROR, not a false-negative empty success.
        entities = ["sesame", "China", "29-allergen mandate"]
        r = MultiHopRetrieveRelationshipResolveNode().execute({"query_entities": to_json(entities)})
        assert r["status"] == AgentStatus.ERROR
        assert "unavailable" in " ".join(r.get("error_log", [])).lower()

    def test_emits_on_success(self, monkeypatch):
        calls = _capture_emit(monkeypatch, multi_hop_module)
        entities = ["sesame", "China", "29-allergen mandate"]
        state = {"query_entities": to_json(entities)}
        MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB).execute(state)
        names = [name for name, _ in calls]
        assert "multi_hop_retrieval_started" in names
        assert "multi_hop_completed" in names
        for _, payload in calls:
            _assert_non_sensitive(payload)

    def test_emits_on_missing_entities_reject(self, monkeypatch):
        calls = _capture_emit(monkeypatch, multi_hop_module)
        MultiHopRetrieveRelationshipResolveNode().execute({"query_entities": ""})
        assert calls, "reject path must be traced too, not only success"
        _assert_non_sensitive(calls[0][1])

    def test_emits_on_kb_unavailable_reject(self, monkeypatch):
        calls = _capture_emit(monkeypatch, multi_hop_module)
        entities = ["sesame", "China", "29-allergen mandate"]
        MultiHopRetrieveRelationshipResolveNode().execute({"query_entities": to_json(entities)})
        assert calls, "KB-unavailable reject path must be traced too"
        _assert_non_sensitive(calls[0][1])

    def test_configured_llm_success_with_dict_response(self):
        # 3m: canonical BaseLLM.complete() -> dict; must be normalised, not crash.
        class DictLLM:
            def complete(self, messages):
                assert isinstance(messages, list)
                return {"content": "Grounded summary of matching SKUs."}

        entities = ["sesame", "China", "29-allergen mandate"]
        state = {"query_entities": to_json(entities)}
        r = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB, llm=DictLLM()).execute(state)
        assert r["status"] == AgentStatus.SUCCESS
        assert r["formatted_answer"] == "Grounded summary of matching SKUs."

    def test_configured_llm_raises_is_error_not_fallback_success(self):
        # 3m half-fix trap: a configured-but-failing LLM must ERROR, never a
        # silent SUCCESS-with-deterministic-fallback (indistinguishable from a
        # real answer).
        class RaisingLLM:
            def complete(self, messages):
                raise ConnectionError("provider unreachable")

        entities = ["sesame", "China", "29-allergen mandate"]
        state = {"query_entities": to_json(entities)}
        r = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB, llm=RaisingLLM()).execute(state)
        assert r["status"] == AgentStatus.ERROR
        assert "llm synthesis failed" in " ".join(r.get("error_log", [])).lower()

    def test_configured_llm_empty_response_is_error_not_fallback_success(self):
        class EmptyLLM:
            def complete(self, messages):
                return {"content": ""}

        entities = ["sesame", "China", "29-allergen mandate"]
        state = {"query_entities": to_json(entities)}
        r = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB, llm=EmptyLLM()).execute(state)
        assert r["status"] == AgentStatus.ERROR

    def test_no_llm_configured_still_uses_deterministic_fallback_success(self):
        # Distinguishes "no LLM configured" (valid deterministic mode) from
        # "LLM configured but failing" (must ERROR, tested above).
        entities = ["sesame", "China", "29-allergen mandate"]
        state = {"query_entities": to_json(entities)}
        r = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB, llm=None).execute(state)
        assert r["status"] == AgentStatus.SUCCESS
        assert "SKU-001" in r["formatted_answer"]

    def test_emits_degradation_event_on_llm_failure(self, monkeypatch):
        calls = _capture_emit(monkeypatch, multi_hop_module)

        class RaisingLLM:
            def complete(self, messages):
                raise TimeoutError("slow provider")

        entities = ["sesame", "China", "29-allergen mandate"]
        state = {"query_entities": to_json(entities)}
        MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB, llm=RaisingLLM()).execute(state)
        names = [name for name, _ in calls]
        assert "answer_synthesis_llm_degraded" in names


class TestResolveLlmLifecycle:
    """The answer-LLM must be built fresh per invocation from ctx.secrets,
    never cached on the (reused) node instance. See _resolve_llm."""

    @staticmethod
    def _full_state(entities):
        # InvocationContext.from_state() indexes these four identity keys
        # directly (not .get()); a real Graph.invoke() always seeds them.
        return {
            "query_entities": to_json(entities),
            "correlation_id": "corr-1",
            "session_id": "sess-1",
            "thread_id": "thread-1",
            "trace_id": "trace-1",
        }

    def test_bare_state_with_no_llm_injected_falls_back_to_deterministic(self):
        # PB-6-shaped fixture: no identity fields at all. InvocationContext.from_state
        # raises KeyError; _resolve_llm must swallow it as "unconfigured", not crash.
        entities = ["sesame", "China", "29-allergen mandate"]
        node = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB)
        llm, config_error = node._resolve_llm({"query_entities": to_json(entities)})
        assert llm is None
        assert config_error is None

    _AZURE_SECRETS = {
        "AZURE_OPENAI_API_KEY": "k",
        "AZURE_OPENAI_ENDPOINT": "https://example.services.ai.azure.com",
        "AZURE_OPENAI_DEPLOYMENT": "gpt-4o",
    }

    def test_configured_secret_builds_a_real_client(self):
        from framework.secrets.context import bound_secrets

        class FakeSecrets:
            def require(self, key):
                return TestResolveLlmLifecycle._AZURE_SECRETS[key]

        entities = ["sesame", "China", "29-allergen mandate"]
        node = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB)
        with bound_secrets(FakeSecrets()):
            llm, config_error = node._resolve_llm(self._full_state(entities))
        assert config_error is None
        assert type(llm).__name__ == "AzureOpenAIClient"

    def test_no_azure_secrets_at_all_falls_back_to_deterministic(self):
        from framework.secrets.context import bound_secrets

        class FakeSecrets:
            def require(self, key):
                raise KeyError(key)

        entities = ["sesame", "China", "29-allergen mandate"]
        node = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB)
        with bound_secrets(FakeSecrets()):
            llm, config_error = node._resolve_llm(self._full_state(entities))
        assert llm is None
        assert config_error is None

    def test_partially_configured_secrets_is_a_config_error_not_silent_fallback(self):
        from framework.secrets.context import bound_secrets

        class FakeSecrets:
            def require(self, key):
                if key == "AZURE_OPENAI_API_KEY":
                    return "k"
                raise KeyError(key)

        entities = ["sesame", "China", "29-allergen mandate"]
        node = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB)
        with bound_secrets(FakeSecrets()):
            llm, config_error = node._resolve_llm(self._full_state(entities))
        assert llm is None
        assert config_error is not None

        with bound_secrets(FakeSecrets()):
            r = node.execute(self._full_state(entities))
        assert r["status"] == AgentStatus.ERROR

    def test_all_configured_but_endpoint_malformed_is_a_config_error(self):
        # AzureOpenAIClient rejects (never rewrites) an endpoint containing "/openai" —
        # documented gotcha; a real-world copy-paste mistake, not a network failure.
        from framework.secrets.context import bound_secrets

        class FakeSecrets:
            def require(self, key):
                if key == "AZURE_OPENAI_ENDPOINT":
                    return "https://example.services.ai.azure.com/openai/v1/responses"
                return TestResolveLlmLifecycle._AZURE_SECRETS[key]

        entities = ["sesame", "China", "29-allergen mandate"]
        node = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB)
        with bound_secrets(FakeSecrets()):
            llm, config_error = node._resolve_llm(self._full_state(entities))
        assert llm is None
        assert config_error is not None

    def test_explicit_llm_double_always_wins_over_secrets(self):
        from framework.secrets.context import bound_secrets

        class FakeSecrets:
            def require(self, key):
                raise AssertionError("secrets must not be consulted when self._llm is injected")

        class DictLLM:
            def complete(self, messages):
                return {"content": "from the test double"}

        entities = ["sesame", "China", "29-allergen mandate"]
        node = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB, llm=DictLLM())
        with bound_secrets(FakeSecrets()):
            llm, config_error = node._resolve_llm(self._full_state(entities))
        assert config_error is None
        assert llm.complete([]) == {"content": "from the test double"}


class TestResponseValidateOutputFormatNode:
    def test_success_compliant_answer(self):
        state = {"formatted_answer": "1 matching SKU(s) found.\n- SKU-001 (sup-a, snacks)"}
        r = ResponseValidateOutputFormatNode().execute(state)
        assert r["status"] == AgentStatus.SUCCESS

    def test_contract_term_error(self):
        state = {"formatted_answer": "unit price: $50 for this SKU"}
        assert ResponseValidateOutputFormatNode().execute(state)["status"] == AgentStatus.ERROR

    def test_extra_gate_blocks_contract_term(self):
        # CoE R1 FINDING-03: the S-3 hook re-checks the output dict's own key
        # `formatted_output` (what execute() emits), not the input key.
        bad_state = {"formatted_output": "contract term: exclusive 3-year deal"}
        out = ResponseValidateOutputFormatNode()._extra_security_gate_output(bad_state)
        assert out["status"] == AgentStatus.ERROR

    def test_extra_gate_passthrough_compliant_answer(self):
        good_state = {"formatted_output": "1 matching SKU(s) found."}
        assert ResponseValidateOutputFormatNode()._extra_security_gate_output(good_state) is good_state

    def test_emits_on_success(self, monkeypatch):
        calls = _capture_emit(monkeypatch, post_process_module)
        state = {"formatted_answer": "1 matching SKU(s) found.\n- SKU-001 (sup-a, snacks)"}
        ResponseValidateOutputFormatNode().execute(state)
        assert calls
        assert calls[0][0] == "output_validate_completed"
        assert calls[0][1]["blocked"] is False
        _assert_non_sensitive(calls[0][1])

    def test_emits_on_contract_term_reject(self, monkeypatch):
        calls = _capture_emit(monkeypatch, post_process_module)
        state = {"formatted_answer": "unit price: $50 for this SKU"}
        ResponseValidateOutputFormatNode().execute(state)
        assert calls, "reject path must be traced too, not only success"
        assert calls[0][1]["blocked"] is True
        _assert_non_sensitive(calls[0][1])
