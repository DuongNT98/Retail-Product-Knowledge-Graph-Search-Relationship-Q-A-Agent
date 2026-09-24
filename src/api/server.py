"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

import os
import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.utils.config_loader import load_config
from shared.secrets import factory as secrets_factory
from src.graph.graph import Graph
from src.services.kb_adapter import build_kb_client

app = FastAPI(title="Agent")

# Same config_dir / "config.yaml" convention as AgentRegistry._compile_and_cache()
# (mediator/registry/agent_registry.py) — absent config.yaml is tolerated, matching
# the registry's own `if exists() else {}` guard. Without this, the standalone
# adapter always ran with config={}, so max_retry/timeout_s never reached Graph()
# on this path.
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_runtime_config = load_config(str(_CONFIG_PATH)) if _CONFIG_PATH.exists() else {}

# POST-02: bind a concrete, authenticated product-KG KB client at startup from
# the declared PRODUCT_KG_API_KEY secret, instead of running against empty KBs
# (which returned a false-negative "No matching SKUs found" success). Production
# is mandatory — a missing key raises at startup (N-53: a missing secret must
# still fail the invocation closed in production/real-stg). The in-memory stub
# is served only when RET_C2_566_ALLOW_STUB_KB is explicitly set (local/demo),
# or when the shared scaffold's provisional deploy-stg job sets STG_MOCK_MODE=true
# (that CI job has no way to materialize a real secret — an established
# precedent for STG-only stub fallbacks) — never true in a real deployment,
# where STG_MOCK_MODE is unset.
_stg_mock_mode = os.environ.get("STG_MOCK_MODE", "").lower() == "true"
_allow_stub_kb = _stg_mock_mode or os.environ.get("RET_C2_566_ALLOW_STUB_KB", "").lower() in ("1", "true", "yes")
if _stg_mock_mode:
    os.environ.setdefault("RET_C2_566_ALLOW_STUB_KB", "true")

_secrets_provider = secrets_factory(namespace="ret", agent_name="ret-c2-566")
with bound_secrets(_secrets_provider):
    _kb_client = build_kb_client(_secrets_provider, allow_stub=_allow_stub_kb)

# The answer-synthesis LLM is OPTIONAL (the deterministic answer body is a valid
# product contract per docs/02_design.md DDR) and is built fresh per invocation
# from ctx.secrets inside the node itself (never here, never cached) — see
# MultiHopRetrieveRelationshipResolveNode._resolve_llm.
agent = Graph(config={**_runtime_config, "kb_client": _kb_client})
agent.compile()
agent.provision_secrets(_secrets_provider)


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Any:
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    # Standalone/STG caller auth: this adapter is the entry-point auth boundary
    # (standalone equivalent of platform AuthMiddleware) — a deployment-level
    # caller credential, not an agent secret, so ctx.secrets does not apply (no
    # InvocationContext exists before auth). Two separate per-run tokens are
    # recognised, since this template's entry node declares required_trust_level: INTERNAL:
    #   - STG_INTERNAL_RUNNER_TOKEN (checked first) -> TrustLevel.INTERNAL.
    #     Presented by scripts/stg_invoke_evidence.py for an INTERNAL-required
    #     entry contract instead of INVOKE_AUTH_TOKEN (see that script's own
    #     docstring). Without this branch, the STG smoke invoke always 401s:
    #     the S-1 gate needs INTERNAL and this adapter previously could only
    #     ever grant up to VERIFIED_EXTERNAL.
    #   - INVOKE_AUTH_TOKEN (fallback) -> TrustLevel.VERIFIED_EXTERNAL, for any
    #     other caller that no upstream middleware vouched for.
    # Middleware-established trust is never demoted (the ANONYMOUS guard below).
    if trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        runner_expected = os.environ.get("STG_INTERNAL_RUNNER_TOKEN")
        expected = os.environ.get("INVOKE_AUTH_TOKEN")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str input
        # (headers decode as latin-1), which would 500 instead of the generic 401.
        if runner_expected and secrets.compare_digest(supplied.encode(), f"Bearer {runner_expected}".encode()):
            trust = TrustLevel.INTERNAL
        elif expected:
            if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
                # Generic body on purpose — do not leak whether the token was
                # absent, malformed, or wrong (or which of the two it was checked
                # against).
                raise HTTPException(status_code=401, detail="Token is invalid or expired.")
            trust = TrustLevel.VERIFIED_EXTERNAL
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return agent.invoke(req.input, ctx=ctx)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "ret-c2-566"}
