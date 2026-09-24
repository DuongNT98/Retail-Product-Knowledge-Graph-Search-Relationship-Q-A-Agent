# Template Design Specification

## Position in AgentCore Architecture

- **Agent Class**: `RETProductKnowledgeGraphSearchAgent`
- **L1 Base**: AgentBaseGraph
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Architecture Overview

Flat `AgentBaseGraph` with the fixed 5-node backbone. No GraphNode
composition needed — multi-hop retrieval is expressible as 3 sequential
deterministic KB lookups inside a single node's `execute()`; no nested
domain workflow requires subgraph isolation.

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version/session_id/trust_level | - | - | InitializeNode (default) |
| pre_process | `QueryNormalizeEntityExtractNode` — S-1/S-2 gate + deterministic entity extraction | `user_input` | `query_entities` | FunctionNode |
| main | `MultiHopRetrieveRelationshipResolveNode` — 3-hop relationship lookup + SKU intersection + LLM-optional synthesis | `query_entities` | `hop_chain`, `resolved_skus`, `formatted_answer` | FunctionNode |
| post_process | `ResponseValidateOutputFormatNode` — non-suppressible contract-term redaction re-check | `formatted_answer` | (validated) | FunctionNode |
| finalize | response_metadata, total_time_ms | - | - | FinalizeNode (default) |

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (retry)
                                          pre_process
```

### State Definition

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `query_entities` | str (JSON list[str]) | entities extracted from the query (ingredients, countries, regulatory mandates) | Yes |
| `hop_chain` | str (JSON list[dict]) | sequence of relationship-node lookups performed, each `{source_entity, relation_type, target_entities}` | Yes |
| `resolved_skus` | str (JSON list[dict]) | final SKUs matching ALL hops in the chain | Yes |
| `formatted_answer` | str | final structured answer (excerpt + citation, no confidential contract terms) | Yes |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle)
- [x] SecurityViolationError
- [x] S-1: `required_trust_level = INTERNAL` on every node.
      **Correction:** the scaffold issue specified `VERIFIED_INTERNAL`,
      which is not a valid `TrustLevel` enum member (only `ANONYMOUS` /
      `VERIFIED_EXTERNAL` / `INTERNAL` exist); `INTERNAL` is the nearest
      valid value (retail buyer/compliance officer callers only).
- [x] S-3: `_extra_security_gate_output()` — non-suppressible re-check on
      `ResponseValidateOutputFormatNode` that the answer contains no raw
      supplier contract terms/pricing. Own-dict field re-check only,
      per the S-3 hook self-consistency rule.
- [x] S-4: `emit_trace_event()` — `output_validate_s3_recheck_blocked` on
      S-3 violation. Query metadata only in audit logs, no raw
      product/supplier data.

**Known constraint — Marketplace one-shot Pod entry point.** `required_trust_level: INTERNAL`
above (retail buyer / compliance officer callers only) is currently incompatible with the
Marketplace one-shot Pod runtime: the Marketplace entry point (`cli.py`) hard-stamps every caller
`VERIFIED_EXTERNAL` with no elevation path, so the S-1 gate rejects every invocation reaching this
agent through that path — confirmed by exercising the built image locally before push: the first
real invocation returns `status='error'` with no error detail forwarded. The standalone HTTP entry
point (`src/api/server.py`) is unaffected — trust is established upstream there and can reach
`INTERNAL`.

This is a known platform contract gap in the Marketplace entry point's trust handling
(`caller_trust_level` hardcoded `VERIFIED_EXTERNAL`, no elevation path), not a template defect.
Lowering `required_trust_level` to force Marketplace compatibility is not an acceptable fix — it
widens a security boundary (this agent handles supplier/compliance data) and needs a PM/architect
decision, not a code change. Until the entry point's trust-elevation question is resolved
upstream, the Marketplace one-shot Pod is **out of scope for real-stg / Marketplace registration**
for this template; the standalone HTTP entry point remains the supported deployment path. This is
a present-state constraint, not a permanent exclusion.

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: framework/ and shared/ only (no agents/base/ required)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| Composition pattern | GraphNode (subgraph) | Flat 3-node (pre_process/main/post_process) | Flat | Multi-hop retrieval is a sequence of deterministic KB lookups inside one node's `execute()`, not a nested multi-node domain workflow — no subgraph isolation needed |
| **Multi-hop retrieval architecture (Engineer Finding #2 feasibility gate)** | Escalate to ToolCallingAgent with a custom multi-node graph | 3 sequential deterministic KB lookups inside one `FunctionNode`, over an explicit relationship-node schema (`source_entity_id -> relation_type -> target_entity_id`) | Sequential lookups in one node | Per the scaffold issue's own suggested resolution path: each hop is a plain KB filter/lookup, not a graph DB traversal — fully expressible within the VectorRAGAgent pattern's `AgentBaseGraph`/`FunctionNode` node contract. No escalation needed. **This resolves the issue's pre-Stage-② design-confirmation requirement.** |
| Entity extraction / relationship lookup / SKU intersection | LLM-based | Deterministic (Tool) | Deterministic | All three are keyword/set-based computations against structured relationship data — no LLM needed; the LLM-optional value is answer narrative synthesis, not the retrieval/intersection logic |
| S-1 trust level | VERIFIED_INTERNAL (issue spec, invalid) | INTERNAL | INTERNAL | `VERIFIED_INTERNAL` is not a valid `TrustLevel` enum member; `INTERNAL` is the nearest valid value |
| Node flow mapping | 7 separate nodes matching scaffold issue exactly | Combine QueryNormalize+EntityExtract (pre_process); MultiHopRetrieve+RelationshipResolve+LLMAnswer (main); ResponseValidate+OutputFormat (post_process) | Combined groups | Each combined group operates on the same input with no branching/retry boundary between the sub-steps |
| Dedup | Hold pending sibling review | Implement — differentiated (issue's own analysis, re-verified) | Implement | `status:dedup-hold` — re-verified all 4 named siblings; no overlap (single-hop/single-modality vs this template's multi-hop relationship traversal). Label left untouched for PM/CoE confirmation |
