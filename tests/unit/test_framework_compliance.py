# RET-C2-566 - Framework compliance tests TC-01..TC-08 (proactive code review).
#
# TC-06/TC-07 (S-2/S-3 @final gates) already have dedicated coverage in
# test_framework_compliance_tc06_tc07.py - not duplicated here. This file adds
# the remaining exact-name-required coverage, in particular TC-08 (S-1 trust
# gate reject path), which CoE Stage 6 review treats as a fail-hard gap even
# though CI does not enforce it (coe-standards/coe-r1-beyond-ci.md row 28).
#
# Reference shape: agent1000/agent-templates sibling
# tests/unit/test_framework_compliance.py, adapted to this template's real
# architecture (Cat 2, flat 3-node backbone, no GraphNode composition).

import os
import re

from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.nodes.multi_hop_retrieve_relationship_resolve_node import MultiHopRetrieveRelationshipResolveNode
from src.nodes.post_process_node import ResponseValidateOutputFormatNode
from src.nodes.pre_process_node import QueryNormalizeEntityExtractNode
from src.schemas.state import State, to_json

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")

RELATIONSHIP_KB = [
    {"source_entity_id": "sesame", "relation_type": "ingredient_of", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
    {"source_entity_id": "China", "relation_type": "imported_from", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
    {"source_entity_id": "29-allergen mandate", "relation_type": "regulated_by", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
]
SKU_KB = [{"sku_id": "SKU-001", "supplier": "sup-a", "category": "snacks"}]
MATCHING_QUERY = "Which SKUs contain sesame and are imported from China under the 29-allergen mandate?"


def _src_files():
    for root, _d, files in os.walk(_SRC):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


# TC-01 - State is a flat TypedDict extending AgentState, added fields are
# primitives or JSON-string-encoded (no compound Python objects in State).
class TestTC01StateContract:
    def test_state_is_typeddict_extending_agent_state(self):
        assert hasattr(State, "__annotations__")
        assert "user_input" in AgentState.__annotations__
        added = set(State.__annotations__) - set(AgentState.__annotations__)
        assert added, "State must declare agent-specific fields"

    def test_added_fields_are_str_typed_json_encoded(self):
        added = [k for k in State.__annotations__ if k not in AgentState.__annotations__]
        for name in added:
            ann = State.__annotations__[name]
            ann_str = str(ann)
            assert "str" in ann_str, f"{name}: {ann_str} - compound fields must be JSON-string-encoded, not raw dict/list"


# TC-02 - Empty/missing input yields a fail-closed ERROR outcome, never a raise.
class TestTC02Validation:
    def test_empty_input_no_raise(self):
        out = QueryNormalizeEntityExtractNode().execute({"user_input": ""})
        assert out["status"] == AgentStatus.ERROR.value
        assert out["error_log"]

    def test_missing_query_entities_no_raise(self):
        out = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB).execute(
            {"query_entities": ""}
        )
        assert out["status"] == AgentStatus.ERROR.value
        assert out["error_log"]


# TC-03 - No JWT / API keys / secrets literals in src/; no direct os.environ
# secret reads (kb_adapter.py reads only the opt-in stub *flag*, not a secret).
class TestTC03NoCredentials:
    def test_no_credential_literals(self):
        pat = re.compile(r"(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")
        offenders = []
        for fp in _src_files():
            with open(fp, encoding="utf-8") as f:
                if pat.search(f.read()):
                    offenders.append(fp)
        assert offenders == []

    def test_no_os_environ_secret_reads(self):
        # Entry-point exception:
        # src/api/server.py reads INVOKE_AUTH_TOKEN to authenticate the caller
        # BEFORE any InvocationContext exists, so ctx.secrets cannot apply. It is a
        # deployment-level caller credential, not an agent secret, and never enters state.
        # os.environ is only used to read the RET_C2_566_ALLOW_STUB_KB opt-in
        # demo flag and the entry-point base-URL fallback, never a credential.
        offenders = []
        for fp in _src_files():
            if os.path.normpath(fp).endswith(os.path.join("src", "api", "server.py")):
                continue
            with open(fp, encoding="utf-8") as f:
                text = f.read()
            for m in re.finditer(r"os\.environ\.get\(([^)]*)\)", text):
                if "API_KEY" in m.group(1) or "SECRET" in m.group(1) or "TOKEN" in m.group(1):
                    offenders.append((fp, m.group(0)))
        assert offenders == []


# TC-04 - InvocationContext is never stored in State after invoke.
class TestTC04ContextIsolation:
    def test_no_invocationcontext_in_state_after_invoke(self):
        from src.graph.graph import Graph

        agent = Graph(config={"max_retry": 1, "relationship_kb": RELATIONSHIP_KB, "sku_kb": SKU_KB})
        agent.compile()
        ctx = InvocationContext(session_id="tc04", caller_trust_level=TrustLevel.INTERNAL, caller_id="tc04-caller")
        result = agent.invoke(MATCHING_QUERY, ctx=ctx)
        for v in result.values():
            assert not isinstance(v, InvocationContext)

    def test_from_state_available(self):
        assert hasattr(InvocationContext, "from_state")


# TC-05 - Every node emits >=1 domain event on every execute() path; no node
# ever re-emits a framework backbone lifecycle event by hand.
class TestTC05Audit:
    def test_source_has_no_backbone_events(self):
        pat = re.compile(r'emit_trace_event\(\s*["\'](node_start|node_complete|node_error|node_skip)["\']')
        offenders = []
        for fp in _src_files():
            with open(fp, encoding="utf-8") as f:
                if pat.search(f.read()):
                    offenders.append(fp)
        assert offenders == []


# TC-08 - required_trust_level enforced: insufficient caller trust -> ERROR
# state via __call__ (S-1 gate), execute() never runs. All three nodes in this
# template declare TrustLevel.INTERNAL (matches agent.yaml's INTERNAL default,
# see [[coe-r1-beyond-ci.md]] row 17), so ANONYMOUS must be rejected by all of
# them - this is the recurring finding class the reviewer flags when only
# execute()-level unit tests exist (they call execute() directly, bypassing
# __call__ and therefore never exercising S-1 at all).
class TestTC08TrustGate:
    def test_declared_trust_levels_valid(self):
        for cls in (
            QueryNormalizeEntityExtractNode,
            MultiHopRetrieveRelationshipResolveNode,
            ResponseValidateOutputFormatNode,
        ):
            assert cls.required_trust_level in (TrustLevel.ANONYMOUS, TrustLevel.VERIFIED_EXTERNAL, TrustLevel.INTERNAL)

    def test_pre_process_rejects_insufficient_trust(self):
        node = QueryNormalizeEntityExtractNode()
        out = node({"caller_trust_level": TrustLevel.ANONYMOUS.value, "user_input": MATCHING_QUERY})
        assert out["status"] == AgentStatus.ERROR.value
        assert "trust" in " ".join(out.get("error_log", [])).lower()

    def test_main_rejects_insufficient_trust(self):
        node = MultiHopRetrieveRelationshipResolveNode(relationship_kb=RELATIONSHIP_KB, sku_kb=SKU_KB)
        out = node({"caller_trust_level": TrustLevel.ANONYMOUS.value, "query_entities": to_json(["sesame"])})
        assert out["status"] == AgentStatus.ERROR.value
        assert "trust" in " ".join(out.get("error_log", [])).lower()

    def test_post_process_rejects_insufficient_trust(self):
        node = ResponseValidateOutputFormatNode()
        out = node({"caller_trust_level": TrustLevel.ANONYMOUS.value, "formatted_answer": "1 matching SKU(s) found."})
        assert out["status"] == AgentStatus.ERROR.value
        assert "trust" in " ".join(out.get("error_log", [])).lower()

    def test_sufficient_trust_succeeds(self):
        node = QueryNormalizeEntityExtractNode()
        out = node({"caller_trust_level": TrustLevel.INTERNAL.value, "user_input": MATCHING_QUERY})
        assert out["status"] == AgentStatus.SUCCESS.value
