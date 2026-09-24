"""Unit tests for the chained multi-hop traversal (post-release audit POST-02).

The audit finding was that "multi-hop" was N independent per-entity filters: hop N+1 was
never seeded from hop N's discovered targets, so an indirectly-related SKU (A -> B -> SKU)
was unreachable. These tests pin the chaining behaviour itself, not just the result shape.
"""

from src.services.service import multi_hop_retrieve, resolve_intersecting_skus

# sesame --(contains)--> BLEND-X --(used_in)--> SKU-900
# The SKU edge hangs off BLEND-X, NOT off the query entity, so only a chained
# traversal can reach it.
_INDIRECT_KB = [
    {"source_entity_id": "sesame", "relation_type": "contains", "target_entity_id": "BLEND-X"},
    {"source_entity_id": "BLEND-X", "relation_type": "used_in", "target_entity_id": "SKU-900", "sku_id": "SKU-900"},
]


def test_second_hop_is_seeded_from_first_hop_targets():
    hop_chain, sku_sets = multi_hop_retrieve(["sesame"], relationship_kb=_INDIRECT_KB)
    # Reached the SKU that is two edges away from the query entity.
    assert sku_sets == [{"SKU-900"}], sku_sets
    assert [h["hop"] for h in hop_chain] == [1, 2]
    assert "BLEND-X" in hop_chain[0]["target_entities"]


def test_traversal_stops_at_max_hops():
    hop_chain, sku_sets = multi_hop_retrieve(["sesame"], relationship_kb=_INDIRECT_KB, max_hops=1)
    # With a single hop the SKU behind BLEND-X is out of reach.
    assert sku_sets == [set()]
    assert len(hop_chain) == 1


def test_cyclic_kb_terminates():
    """A cycle among intermediate (non-SKU) entities must not expand forever."""
    cyclic = [
        {"source_entity_id": "A", "relation_type": "r", "target_entity_id": "B"},
        {"source_entity_id": "B", "relation_type": "r", "target_entity_id": "A"},
        {"source_entity_id": "B", "relation_type": "in", "target_entity_id": "SKU-1", "sku_id": "SKU-1"},
    ]
    hop_chain, sku_sets = multi_hop_retrieve(["A"], relationship_kb=cyclic, max_hops=10)
    assert sku_sets == [{"SKU-1"}]
    assert len(hop_chain) <= 10  # visited-set prevents infinite expansion


def test_sku_edges_are_terminal_and_do_not_widen_the_match():
    """Expanding *through* a SKU would make every co-attribute reachable.

    'China' relates only to SKU-001. Walking back out through SKU-001 would reach
    'sesame' and hence SKU-002, silently weakening the intersection.
    """
    kb = [
        {"source_entity_id": "sesame", "relation_type": "in", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
        {"source_entity_id": "China", "relation_type": "from", "target_entity_id": "SKU-001", "sku_id": "SKU-001"},
        {"source_entity_id": "sesame", "relation_type": "in", "target_entity_id": "SKU-002", "sku_id": "SKU-002"},
    ]
    _, sku_sets = multi_hop_retrieve(["China"], relationship_kb=kb, max_hops=3)
    assert sku_sets == [{"SKU-001"}], sku_sets


def test_intersection_still_requires_every_query_entity():
    kb = [
        {"source_entity_id": "sesame", "relation_type": "in", "target_entity_id": "SKU-1", "sku_id": "SKU-1"},
        {"source_entity_id": "sesame", "relation_type": "in", "target_entity_id": "SKU-2", "sku_id": "SKU-2"},
        {"source_entity_id": "China", "relation_type": "from", "target_entity_id": "SKU-2", "sku_id": "SKU-2"},
    ]
    _, sku_sets = multi_hop_retrieve(["sesame", "China"], relationship_kb=kb, max_hops=1)
    sku_kb = [{"sku_id": "SKU-1"}, {"sku_id": "SKU-2"}]
    resolved = resolve_intersecting_skus(sku_sets, sku_kb=sku_kb)
    # Only SKU-2 satisfies BOTH query entities.
    assert [s["sku_id"] for s in resolved] == ["SKU-2"]


def test_unknown_entity_yields_no_skus():
    _, sku_sets = multi_hop_retrieve(["unobtainium"], relationship_kb=_INDIRECT_KB)
    assert sku_sets == [set()]
