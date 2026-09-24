"""AgentCore Platform v1.0 - RET-C2-566 outer graph (Cat 2).

Flat AgentBaseGraph with the fixed 5-node backbone. No GraphNode
composition needed - multi-hop retrieval is expressible as 3 sequential
deterministic KB lookups inside a single node's execute() (per the
scaffold issue's architecture-feasibility resolution, see
docs/02_design.md Design Decision Record) - no nested domain workflow
requires subgraph isolation.

Backbone: initialize -> pre_process(QueryNormalizeEntityExtract) ->
          main(MultiHopRetrieveRelationshipResolve) ->
          post_process(ResponseValidateOutputFormat) -> finalize
"""

from framework.graph.agent_base_graph import AgentBaseGraph

from src.nodes.multi_hop_retrieve_relationship_resolve_node import MultiHopRetrieveRelationshipResolveNode
from src.nodes.post_process_node import ResponseValidateOutputFormatNode
from src.nodes.pre_process_node import QueryNormalizeEntityExtractNode
from src.schemas.state import State


class RETProductKnowledgeGraphSearchAgent(AgentBaseGraph):
    """RET-C2-566 - Retail Product Knowledge Graph Search & Relationship Q&A Agent (Cat 2)."""

    @property
    def name(self) -> str:
        return "ret-c2-566"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize

        relationship_kb = self.config.get("relationship_kb")
        sku_kb = self.config.get("sku_kb")
        kb_client = self.config.get("kb_client")

        self._nodes["pre_process"] = QueryNormalizeEntityExtractNode()
        # No `llm=` here: the answer-LLM is built fresh per invocation from
        # ctx.secrets inside the node's execute() (see _resolve_llm), never
        # cached on this reused node instance.
        self._nodes["main"] = MultiHopRetrieveRelationshipResolveNode(
            relationship_kb=relationship_kb, sku_kb=sku_kb, kb_client=kb_client
        )
        self._nodes["post_process"] = ResponseValidateOutputFormatNode()

    # add_edges() is NOT overridden - backbone wiring belongs to the framework.


# Alias for agent.yaml module:"src.graph" resolution (AgentRegistry / api/server.py).
Graph = RETProductKnowledgeGraphSearchAgent
