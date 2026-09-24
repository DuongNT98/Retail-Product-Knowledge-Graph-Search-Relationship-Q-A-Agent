# RET-C2-566 — Test Specification

## Test Strategy

Deterministic-core: no LLM required, full suite runs offline. Answer
synthesis accepts an optional `llm` client and falls back to deterministic
citation-based synthesis when absent.

## Unit Tests (`tests/unit/test_nodes.py`)

| Node | Cases |
|---|---|
| QueryNormalizeEntityExtractNode | success (ingredient + country + mandate entities extracted); empty user_input→ERROR; no recognizable entities→ERROR |
| MultiHopRetrieveRelationshipResolveNode | success (intersecting SKU across all hops resolved); success (no intersection, empty result); missing query_entities→ERROR |
| ResponseValidateOutputFormatNode | success (compliant answer); raw contract term present→ERROR (non-suppressible hook) |

## Integration Tests (`tests/integration/test_graph.py`)

| ID | Test | Expected |
|---|---|---|
| I-1 | 3-entity relationship query with a matching SKU | pipeline completes (≥4 nodes); answer lists the matching SKU |
| I-2 | relationship query with no intersecting SKU | pipeline completes; answer says no matching SKUs |
| I-3 | empty query | ERROR |

> Per [[framework-s2-gate-can-also-break-success-path]], integration tests
> assert pipeline-completion invariants rather than an exact terminal status
> for success cases — unit tests already pin the success-path logic
> deterministically.

## Proof-of-Boundary Tests (`tests/proof_of_boundary/`)

| PB-ID | Boundary | Test | Expected Result |
|-------|----------|------|----------------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass |
| PB-6 | Invoke execution order | S-1 → node_start → S-2 → execute → S-3 → node_complete | Order verified |

## Business Logic Tests

| TC-ID | Test | Input | Expected Result |
|-------|------|-------|----------------|
| BL-01 | entity extraction | query mentions "sesame", "China", "29-allergen mandate" | all 3 entities extracted |
| BL-02 | 3-hop intersection resolution | a SKU matching all 3 hop conditions | SKU present in resolved_skus |
| BL-03 | non-intersection exclusion | a SKU matching only 2 of 3 hops | SKU absent from resolved_skus |
| BL-04 | non-suppressible contract-term redaction | answer text contains "unit price: $50" | text replaced with `[REDACTED_CONTRACT_TERM]` |

## Non-suppressible output tests (critical)

- `formatted_answer` must never contain raw supplier contract terms/pricing
  (unit + I-1/I-2 + BL-04).
- The post_process S-3 hook re-checks `formatted_answer`'s own field only
  (S-3 hook self-consistency rule) — never cross-references separate state
  fields read independently.

## Test Execution Summary
- Execution date: 2026-07-08
- Total tests: see CI `run-tests` job output
- Coverage: deterministic-core, no LLM dependency
