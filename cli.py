"""AGENTIC STAR Marketplace entrypoint — one-shot Pod process.

Referenced by this repo's Dockerfile as the image `CMD`. Compiles the agent,
provisions its secrets, then hands off to shared.bootstrap.marketplace_app
for the Marketplace lifecycle (identity, input, events, terminal delivery,
exit). Mirrors agentcore's own `agents/base/chat_agent/cli.py` (the pattern
this file was copied from).

`namespace=` here is the Marketplace secret-provisioning namespace — a different
concept from `config/agent.yaml`'s AgentRegistry `namespace:` key that happens to
share its value. Mirrors `src/api/server.py`'s existing
`secrets_factory(namespace="ret", agent_name="ret-c2-566")` call shape rather
than a per-template value: one Marketplace Pod deploys exactly one template, so
there is no cross-template secret-path collision to guard against.

Known operational limitation (HTTP-only reintegration scope): unlike
src/api/server.py, this entry point does not build the product-KG `kb_client`
— the Marketplace runner provisions secrets after construction, so eagerly
building it here (before secrets exist) is not possible without deferred
lazy-wrapper wiring that is out of scope for this pass. Every invoke on this
path will honestly ERROR ("product-KG unavailable") until that wiring is
added; the answer-LLM path is unaffected (built fresh per invocation from
ctx.secrets inside the node itself either way).
"""

from pathlib import Path

from framework.utils.config_loader import load_agent_config
from shared.bootstrap.marketplace_app import run_agent_marketplace
from src.graph.graph import Graph

# Add config overrides here to set values without touching config/config.yaml.
extend_config = {}

if __name__ == "__main__":
    run_agent_marketplace(
        Graph,
        agent_name="ret-c2-566",
        namespace="ret",
        config={**load_agent_config(Path(__file__).resolve().parent), **extend_config},
    )
