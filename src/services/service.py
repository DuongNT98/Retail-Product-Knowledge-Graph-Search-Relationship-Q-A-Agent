"""AgentCore Platform v1.0 - RET-C2-566 domain services.

Deterministic entity extraction + multi-hop relationship-node retrieval
(Tool layer, no LLM) + LLM-optional grounded answer synthesis
(deterministic fallback, an established pattern for LLM-optional synthesis). Multi-hop retrieval is a
bounded chain of deterministic KB lookups over an explicit relationship-node
schema (source_entity_id -> relation_type -> target_entity_id): each hop is
seeded from the entities the previous hop discovered, so indirectly related
SKUs (A -> B -> SKU) are reached. It is a sequence of keyword lookups, not a
graph DB traversal, per the scaffold issue's architecture-feasibility
resolution (see docs/02_design.md Design Decision Record). S-3 output gate is non-suppressible: no confidential
supplier contract terms in output - excerpt + citation only.
"""

from __future__ import annotations

import re
from typing import Any

_KNOWN_INGREDIENTS = ("sesame", "peanut", "shrimp", "wheat", "milk", "egg")
_KNOWN_COUNTRIES = ("china", "vietnam", "thailand", "japan")
_ALLERGEN_MANDATE_PATTERN = re.compile(r"\b\d{1,2}-allergen mandate\b", re.IGNORECASE)

_CONTRACT_TERM_PATTERNS = [
    re.compile(r"unit price[:\s]*[¥$]?\d+", re.IGNORECASE),
    re.compile(r"contract term[:\s]*.+", re.IGNORECASE),
]


def extract_entities(query: str) -> list[str]:
    """Deterministic entity extraction (Tool layer, no LLM)."""
    lower = query.lower()
    entities = [kw for kw in _KNOWN_INGREDIENTS if kw in lower]
    entities += [kw.capitalize() for kw in _KNOWN_COUNTRIES if kw in lower]
    entities += _ALLERGEN_MANDATE_PATTERN.findall(query)
    return entities


def multi_hop_retrieve(
    entities: list[str],
    relationship_kb: list[dict[str, Any]] | None = None,
    max_hops: int = 3,
) -> tuple[list[dict[str, Any]], list[set[str]]]:
    """Deterministic chained relationship traversal (Tool layer, no LLM).

    Each hop is a plain KB filter over the explicit relationship schema
    {source_entity_id, relation_type, target_entity_id, sku_id} — a sequence of
    keyword lookups, not a graph DB traversal (per the scaffold issue's
    architecture-feasibility resolution).

    Chaining: hop N+1 is seeded from the *intermediate entities discovered by hop N*, so
    a query entity reaches SKUs that are related indirectly (A → B → SKU), which a set of
    independent per-entity filters cannot express.

    A SKU-bearing edge is TERMINAL: the SKU is collected but never used as a seed for the
    next hop. Expanding through a SKU would walk back out to every other attribute of that
    SKU ("China → SKU-001 → sesame → SKU-002"), which would silently weaken the downstream
    intersection into "related to anything the query touches".

    Traversal stops when a hop discovers no new intermediate entity or `max_hops` is
    reached; visited entities are never re-expanded, so a cyclic KB terminates.

    Returns (hop_chain, matched_sku_sets) where `matched_sku_sets` holds ONE set per seed
    entity — the SKUs reachable from that entity — so the downstream intersection still
    means "SKUs satisfying every query entity".
    """
    relationship_kb = relationship_kb or []
    hop_chain: list[dict[str, Any]] = []
    matched_sku_sets: list[set[str]] = []

    for entity in entities:
        reachable_skus: set[str] = set()
        visited: set[str] = {entity}
        frontier = [entity]

        for hop_index in range(max_hops):
            if not frontier:
                break
            hop_matches = [
                entry
                for entry in relationship_kb
                if entry.get("source_entity_id") in frontier or entry.get("target_entity_id") in frontier
            ]
            if not hop_matches:
                break

            # Next frontier = newly-seen entities on the far side of this hop's edges,
            # excluding SKU-bearing (terminal) edges.
            discovered: set[str] = set()
            for m in hop_matches:
                sku_id = m.get("sku_id")
                if sku_id:
                    reachable_skus.add(sku_id)
                    continue  # terminal — do not expand through a SKU
                for side in (m.get("source_entity_id"), m.get("target_entity_id")):
                    if side and side not in visited:
                        discovered.add(side)

            hop_chain.append(
                {
                    "seed_entity": entity,
                    "hop": hop_index + 1,
                    "source_entity": entity,
                    "relation_type": hop_matches[0].get("relation_type"),
                    "target_entities": sorted(discovered),
                }
            )
            if not discovered:
                break
            visited |= discovered
            frontier = sorted(discovered)

        matched_sku_sets.append(reachable_skus)

    return hop_chain, matched_sku_sets


def resolve_intersecting_skus(
    matched_sku_sets: list[set[str]], sku_kb: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Deterministic intersection: SKUs matching ALL hops in the chain (Tool layer, no LLM)."""
    sku_kb = sku_kb or []
    if not matched_sku_sets:
        return []

    intersecting_ids = matched_sku_sets[0]
    for sku_set in matched_sku_sets[1:]:
        intersecting_ids = intersecting_ids & sku_set

    return [sku for sku in sku_kb if sku.get("sku_id") in intersecting_ids]


def strip_contract_terms(text: str) -> tuple[str, bool]:
    """Non-suppressible re-check: strip raw supplier contract terms/pricing.

    Returns (cleaned_text, was_blocked).
    """
    cleaned = text
    blocked = False
    for pattern in _CONTRACT_TERM_PATTERNS:
        if pattern.search(cleaned):
            blocked = True
            cleaned = pattern.sub("[REDACTED_CONTRACT_TERM]", cleaned)
    return cleaned, blocked


# Security (post-audit MEDIUM): only these fields may be projected into the LLM
# prompt. Retrieved SKU records are treated as untrusted — never pass the full
# dict (which may carry supplier contract terms / pricing) to the LLM.
_LLM_ALLOWED_SKU_FIELDS = ("sku_id", "supplier", "category")


def _project_for_llm(resolved_skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allow-list projection of SKU records before they reach the LLM."""
    return [{k: sku.get(k) for k in _LLM_ALLOWED_SKU_FIELDS if k in sku} for sku in resolved_skus]


def _extract_text(raw: Any) -> str:
    """Normalise an LLM response (canonical dict or bare string) into text."""
    if isinstance(raw, dict):
        content = raw.get("content", "")
        return content if isinstance(content, str) else ""
    if isinstance(raw, str):
        return raw
    return ""


def format_answer(resolved_skus: list[dict[str, Any]], llm: Any | None = None) -> tuple[str, str | None]:
    """Synthesize the grounded, structured answer.

    LLM-essential in a real deployment; deterministic fallback keeps the pipeline
    runnable without an LLM. Only an allow-listed projection of each SKU reaches the LLM
    (retrieved content is untrusted); the canonical BaseLLM.complete() -> dict response
    is normalised before use.

    Returns `(answer, degradation_reason)`. `degradation_reason` is None on the normal
    path and a short non-sensitive reason string when a CONFIGURED LLM did not produce
    the answer — the caller emits that as an S-4 degradation event. Silently swallowing a
    provider error made an LLM outage invisible in production.
    """
    if llm is not None and hasattr(llm, "complete"):
        try:
            safe_skus = _project_for_llm(resolved_skus)
            messages = [
                {"role": "system", "content": "You are a retail compliance officer's assistant."},
                {"role": "user", "content": f"Summarize these matching SKUs: {safe_skus}"},
            ]
            text = _extract_text(llm.complete(messages))
            if text.strip():
                cleaned, _ = strip_contract_terms(text.strip())
                return cleaned, None
            degradation = "empty_or_non_canonical_response"
        except (ConnectionError, TimeoutError, ValueError, RuntimeError) as exc:
            degradation = type(exc).__name__
    else:
        degradation = None

    return _deterministic_answer(resolved_skus), degradation


def _deterministic_answer(resolved_skus: list[dict[str, Any]]) -> str:
    """LLM-free answer body (also the fallback when a configured LLM is unavailable)."""
    if not resolved_skus:
        return "該当するSKUが見つかりませんでした。 / No matching SKUs found for the given relationship criteria."

    lines = [f"{len(resolved_skus)} matching SKU(s) found."]
    for sku in resolved_skus:
        lines.append(f"- {sku.get('sku_id')} ({sku.get('supplier')}, {sku.get('category')})")
    body = "\n".join(lines)
    cleaned, _ = strip_contract_terms(body)
    return cleaned
