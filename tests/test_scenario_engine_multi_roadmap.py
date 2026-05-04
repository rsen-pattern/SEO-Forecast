"""Tests for per-scenario roadmap support in engine/scenario_engine.py."""
from __future__ import annotations

import pytest

from engine.scenario_engine import build_scenario_presets, run_three_scenarios
from tests.fixtures import make_ga4_df, make_semrush_kw_df

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bundle(hours: float, cadence: int, maintenance: float, retainer: float) -> dict:
    return {
        "global_rollup": {
            "effort_level": "moderate",
            "content_cadence": cadence,
            "maintenance_coverage": maintenance,
            "total_monthly_hours": hours,
        },
        "client_metadata": {"retainer_aud_monthly": retainer},
    }


_THREE_BUNDLES = {
    "Conservative": _bundle(hours=10.0, cadence=2, maintenance=0.3, retainer=2000.0),
    "Moderate":     _bundle(hours=25.0, cadence=4, maintenance=0.6, retainer=5000.0),
    "Aggressive":   _bundle(hours=50.0, cadence=8, maintenance=0.9, retainer=10000.0),
}


# ---------------------------------------------------------------------------
# build_scenario_presets — multi-bundle path
# ---------------------------------------------------------------------------

class TestBuildPresetsMultipleBundles:
    def test_each_scenario_uses_own_bundle(self):
        presets = build_scenario_presets(roadmap_bundles=_THREE_BUNDLES)
        assert presets["Conservative"]["total_monthly_hours"] == pytest.approx(10.0)
        assert presets["Moderate"]["total_monthly_hours"] == pytest.approx(25.0)
        assert presets["Aggressive"]["total_monthly_hours"] == pytest.approx(50.0)

    def test_each_scenario_uses_own_retainer(self):
        presets = build_scenario_presets(roadmap_bundles=_THREE_BUNDLES)
        assert presets["Conservative"]["retainer_aud_monthly"] == pytest.approx(2000.0)
        assert presets["Moderate"]["retainer_aud_monthly"] == pytest.approx(5000.0)
        assert presets["Aggressive"]["retainer_aud_monthly"] == pytest.approx(10000.0)

    def test_each_scenario_uses_own_cadence(self):
        presets = build_scenario_presets(roadmap_bundles=_THREE_BUNDLES)
        assert presets["Conservative"]["content_cadence"] == 2
        assert presets["Moderate"]["content_cadence"] == 4
        assert presets["Aggressive"]["content_cadence"] == 8

    def test_source_is_per_scenario(self):
        presets = build_scenario_presets(roadmap_bundles=_THREE_BUNDLES)
        for p in presets.values():
            assert p["source"] == "roadmap-detected-per-scenario"

    def test_multi_bundle_overrides_single_bundle(self):
        """roadmap_bundles takes priority over roadmap_bundle when all three present."""
        single = _bundle(hours=99.0, cadence=99, maintenance=0.99, retainer=99000.0)
        presets = build_scenario_presets(roadmap_bundle=single, roadmap_bundles=_THREE_BUNDLES)
        assert presets["Moderate"]["total_monthly_hours"] == pytest.approx(25.0)
        assert presets["Moderate"]["source"] == "roadmap-detected-per-scenario"

    def test_partial_bundles_falls_back_to_single_bundle(self):
        """Only two scenarios in roadmap_bundles → fall back to single-bundle derivation."""
        partial = {
            "Conservative": _bundle(10, 2, 0.3, 2000),
            "Moderate":     _bundle(25, 4, 0.6, 5000),
            # Aggressive missing
        }
        single = _bundle(hours=30.0, cadence=5, maintenance=0.7, retainer=6000.0)
        presets = build_scenario_presets(roadmap_bundle=single, roadmap_bundles=partial)
        # Partial bundles don't satisfy all-three requirement — falls back to single path
        assert presets["Moderate"]["source"] == "roadmap-detected"
        assert presets["Moderate"]["total_monthly_hours"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# build_scenario_presets — single-bundle fallback (existing behaviour preserved)
# ---------------------------------------------------------------------------

class TestBuildPresetsSingleBundleFallback:
    def test_no_multi_bundles_falls_back_to_single(self):
        single = _bundle(hours=20.0, cadence=4, maintenance=0.6, retainer=4000.0)
        presets = build_scenario_presets(roadmap_bundle=single)
        assert presets["Moderate"]["source"] == "roadmap-detected"
        assert presets["Moderate"]["total_monthly_hours"] == pytest.approx(20.0)

    def test_conservative_derived_at_60_pct(self):
        single = _bundle(hours=20.0, cadence=4, maintenance=0.6, retainer=4000.0)
        presets = build_scenario_presets(roadmap_bundle=single)
        assert presets["Conservative"]["total_monthly_hours"] == pytest.approx(12.0)

    def test_no_bundles_returns_generic(self):
        presets = build_scenario_presets()
        for p in presets.values():
            assert p["source"] == "generic-preset"


# ---------------------------------------------------------------------------
# run_three_scenarios — per-scenario content plan routing
# ---------------------------------------------------------------------------

class TestRunThreeScenariosPerPlanRouting:
    @pytest.fixture
    def inputs(self):
        ga4 = make_ga4_df(months=18, starting_traffic=15_000, trend=200)
        kw = make_semrush_kw_df(n=30, positions=[8, 12, 15] * 10, kds=[30] * 30)
        kw["is_branded"] = False
        return ga4, kw

    def test_per_plans_accepted_without_error(self, inputs):
        ga4, kw = inputs
        presets = build_scenario_presets(roadmap_bundles=_THREE_BUNDLES)
        per_plans = {
            "Conservative": [],
            "Moderate":     [{"title": "Guide", "publish_month": 2, "target_keywords": []}],
            "Aggressive":   [{"title": "A", "publish_month": 1, "target_keywords": []},
                             {"title": "B", "publish_month": 2, "target_keywords": []}],
        }
        results = run_three_scenarios(
            ga4_df=ga4, kw_df=kw, kw_existing=kw,
            presets=presets, months=6, seed=42,
            roadmap_content_plans=per_plans,
        )
        assert set(results.keys()) == {"Conservative", "Moderate", "Aggressive"}
        for name, res in results.items():
            assert "error" not in res, f"{name}: {res.get('error')}"

    def test_single_plan_fallback_when_no_per_plans(self, inputs):
        ga4, kw = inputs
        presets = build_scenario_presets()
        single_plan = [{"title": "Post", "publish_month": 1, "target_keywords": []}]
        results = run_three_scenarios(
            ga4_df=ga4, kw_df=kw, kw_existing=kw,
            presets=presets, months=6, seed=42,
            roadmap_content_plan=single_plan,
        )
        assert set(results.keys()) == {"Conservative", "Moderate", "Aggressive"}

    def test_per_plans_take_priority_over_single_plan(self, inputs):
        """With roadmap_content_plans supplied, roadmap_content_plan is ignored for covered scenarios."""
        ga4, kw = inputs
        presets = build_scenario_presets(roadmap_bundles=_THREE_BUNDLES)
        per_plans = {
            "Conservative": [],
            "Moderate":     [],
            "Aggressive":   [],
        }
        # A single_plan with impossible content that would cause an error if used
        single_plan = [{"title": "X" * 10000, "publish_month": 1, "target_keywords": []}]
        results = run_three_scenarios(
            ga4_df=ga4, kw_df=kw, kw_existing=kw,
            presets=presets, months=6, seed=42,
            roadmap_content_plan=single_plan,
            roadmap_content_plans=per_plans,
        )
        # Should complete without using the (irrelevant) single_plan for any scenario
        for name, res in results.items():
            assert "error" not in res, f"{name}: {res.get('error')}"

    def test_missing_scenario_key_falls_back_to_single_plan(self, inputs):
        """Scenario missing from roadmap_content_plans falls back to roadmap_content_plan."""
        ga4, kw = inputs
        presets = build_scenario_presets()
        # Only Moderate and Aggressive have per-plans; Conservative will use single_plan
        per_plans = {
            "Moderate":   [],
            "Aggressive": [],
        }
        single_plan = [{"title": "Post", "publish_month": 1, "target_keywords": []}]
        results = run_three_scenarios(
            ga4_df=ga4, kw_df=kw, kw_existing=kw,
            presets=presets, months=6, seed=42,
            roadmap_content_plan=single_plan,
            roadmap_content_plans=per_plans,
        )
        for name, res in results.items():
            assert "error" not in res, f"{name}: {res.get('error')}"
