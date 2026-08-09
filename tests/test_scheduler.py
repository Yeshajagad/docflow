"""
Unit tests for the bin-packing policies - pure logic, no DB/HTTP needed.
Uses lightweight Node-like stand-ins so these run instantly and don't
depend on Stage 1's SQLAlchemy setup.
"""
from types import SimpleNamespace

from app.scheduler import FirstFitPolicy, BestFitPolicy, FairSharePolicy


def make_node(id, capacity, current_load):
    return SimpleNamespace(id=id, capacity=capacity, current_load=current_load)


def test_first_fit_picks_first_node_with_room():
    nodes = [
        make_node("a", capacity=10, current_load=8),   # 2 free - too small
        make_node("b", capacity=10, current_load=2),   # 8 free - fits, first match
        make_node("c", capacity=10, current_load=0),   # 10 free - also fits, but later
    ]
    policy = FirstFitPolicy()
    chosen = policy.select_node(nodes, job_weight=5)
    assert chosen.id == "b"


def test_first_fit_returns_none_when_nothing_fits():
    nodes = [make_node("a", capacity=10, current_load=9)]
    policy = FirstFitPolicy()
    assert policy.select_node(nodes, job_weight=5) is None


def test_best_fit_picks_tightest_sufficient_node():
    nodes = [
        make_node("a", capacity=10, current_load=2),   # 8 free
        make_node("b", capacity=10, current_load=6),   # 4 free - tightest fit for weight=3
        make_node("c", capacity=10, current_load=0),   # 10 free
    ]
    policy = BestFitPolicy()
    chosen = policy.select_node(nodes, job_weight=3)
    assert chosen.id == "b"


def test_fair_share_picks_most_free_capacity():
    nodes = [
        make_node("a", capacity=10, current_load=2),   # 8 free
        make_node("b", capacity=10, current_load=6),   # 4 free
        make_node("c", capacity=20, current_load=5),   # 15 free - most room
    ]
    policy = FairSharePolicy()
    chosen = policy.select_node(nodes, job_weight=3)
    assert chosen.id == "c"


def test_all_policies_ignore_nodes_without_enough_room():
    nodes = [make_node("a", capacity=10, current_load=9.5)]
    for policy in (FirstFitPolicy(), BestFitPolicy(), FairSharePolicy()):
        assert policy.select_node(nodes, job_weight=1) is None