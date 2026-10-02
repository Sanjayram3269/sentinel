import pytest
from sentinel_ai.prediction.train import FEATURE_COLS

def test_no_leakage_features():
    leaky_features = {"closed_edge_flag", "hazard_overlap", "route_fails", "actual_eta"}
    for feature in FEATURE_COLS:
        assert feature not in leaky_features, f"Leaky feature {feature} found in FEATURE_COLS"
