import math

from gatekeeper.plan import Plan, sample_size_per_arm
from gatekeeper.verdict import chi2_sf, decide, srm_p


def test_sample_size_matches_textbook():
    # 30% baseline, +10% relative (30% -> 33%), alpha .05, power .8: ~3,760 per arm
    assert 3700 <= sample_size_per_arm(0.30, 0.10) <= 3800


def test_chi2_sf_known_values():
    assert math.isclose(chi2_sf(3.841, 1), 0.05, abs_tol=1e-3)
    assert math.isclose(chi2_sf(10.828, 1), 0.001, abs_tol=1e-4)
    assert math.isclose(chi2_sf(5.991, 2), 0.05, abs_tol=1e-3)


def test_srm_flags_58_42():
    assert srm_p([580, 420], [0.5, 0.5]) < 0.001
    assert srm_p([505, 495], [0.5, 0.5]) > 0.5


def plan(per_arm_target=3760):
    p = Plan(0.30, 0.10).to_dict()
    p["decision_rule"]["min_exposures_per_arm"] = per_arm_target
    p["split"] = {"control": 0.5, "short": 0.5}
    return p


def payload(nc, cc, nt, ct, ctw, lift=0.15, srm=None):
    return {"srm_p_value": srm, "metrics": [{"metric_id": 1,
            "variants": {"control": {"n": nc, "sum": cc, "sum_squares": cc}, "short": {"n": nt, "sum": ct, "sum_squares": ct}},
            "inference": {"control": None, "short": {"lift": lift, "ci_low": -0.14, "ci_high": 0.44, "chance_to_win": ctw}}}]}


def test_blog_example_84_percent_is_keep_running():
    # Flagsmith's own walkthrough: 247 vs 255 visitors, 84% chance to win.
    v = decide(payload(247, 75, 255, 89, 0.84), plan())
    assert v.decision == "keep_running"
    assert v.winner is None


def test_srm_makes_it_invalid_even_with_a_winner():
    v = decide(payload(4200, 1260, 5800, 2030, 0.999), plan())
    assert v.decision == "invalid"


def test_changed_setup_is_invalid():
    v = decide(payload(4000, 1200, 4000, 1400, 0.999), plan(), approved_hash="a", current_hash="b")
    assert v.decision == "invalid"


def test_ship_when_everything_holds():
    v = decide(payload(4000, 1200, 4010, 1400, 0.998), plan())
    assert v.decision == "ship" and v.winner == "short"


def test_drop_when_variant_loses():
    v = decide(payload(4000, 1300, 4010, 1100, 0.01, lift=-0.15), plan())
    assert v.decision == "drop"
