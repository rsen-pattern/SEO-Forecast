"""Tests for Revised Task 9 — Pattern-native parser, format detection,
industry seasonality priors, and roadmap_content_plan in new_content_engine."""
from __future__ import annotations

import os
import io

import numpy as np
import pandas as pd
import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────

_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample_pattern_native_roadmap.xlsx")


def _fixture_bytes() -> bytes:
    with open(_FIXTURE_PATH, "rb") as f:
        return f.read()


# ── TestDetectRoadmapFormat ───────────────────────────────────────────────────


class TestDetectRoadmapFormat:
    def test_pattern_native_xlsx_detected(self):
        from engine.roadmap_ai_engine import detect_roadmap_format
        result = detect_roadmap_format(_fixture_bytes(), "xlsx")
        assert result == "pattern_native"

    def test_task_table_csv(self):
        from engine.roadmap_ai_engine import detect_roadmap_format
        csv = b"Task,Focus,Occurrence,Hours\nArticle Writing,Content,Monthly,10\n"
        assert detect_roadmap_format(csv, "csv") == "task_table"

    def test_param_table_csv(self):
        from engine.roadmap_ai_engine import detect_roadmap_format
        csv = b"cadence,effort_level,maintenance_coverage\n4,moderate,0.5\n"
        assert detect_roadmap_format(csv, "csv") == "param_table"

    def test_unknown_returns_unknown(self):
        from engine.roadmap_ai_engine import detect_roadmap_format
        csv = b"col1,col2\nfoo,bar\n"
        assert detect_roadmap_format(csv, "csv") == "unknown"

    def test_partial_sheet_match_not_native(self):
        """An xlsx with only 1 matching sheet should NOT be detected as pattern_native."""
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.title = "Breakdown"
        buf = io.BytesIO()
        wb.save(buf)
        from engine.roadmap_ai_engine import detect_roadmap_format
        result = detect_roadmap_format(buf.getvalue(), "xlsx")
        assert result != "pattern_native"


# ── TestParsePatternNative ────────────────────────────────────────────────────


class TestParsePatternNative:
    def test_returns_v2_bundle(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        assert bundle["schema_version"] == "2.0"
        assert bundle["source_format"] == "pattern_native"

    def test_client_metadata_extracted(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        meta = bundle["client_metadata"]
        assert "helen kaminski" in meta.get("client_name", "").lower()
        assert meta.get("retainer_aud_monthly", 0) > 0

    def test_per_focus_all_keys_present(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        for fk in ("content", "technical", "on_page", "off_page", "local", "analytics", "strategy"):
            assert fk in bundle["per_focus"]

    def test_content_monthly_hours_positive(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        assert bundle["per_focus"]["content"]["monthly_hours"] > 0

    def test_content_plan_extracted(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        assert len(bundle["content_plan"]) >= 3

    def test_content_plan_has_required_fields(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        for item in bundle["content_plan"]:
            assert "url" in item or "keyword" in item
            assert "publish_month" in item
            assert "page_type" in item

    def test_optimise_page_type_detected(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        types = [i["page_type"] for i in bundle["content_plan"]]
        assert "optimise" in types

    def test_timeline_months_from_content_plan(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        # Content plan has max publish_month = 5, so timeline should be >= 5
        assert bundle["timeline"]["months_covered"] >= 5

    def test_global_rollup_has_hours(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        assert bundle["global_rollup"]["total_monthly_hours"] > 0

    def test_returns_raw_task_descriptions_list(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        _, raw_tasks = parse_pattern_native(_fixture_bytes())
        assert isinstance(raw_tasks, list)
        assert len(raw_tasks) > 0

    def test_effort_level_valid(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        bundle, _ = parse_pattern_native(_fixture_bytes())
        for fk in ("content", "technical", "on_page"):
            effort = bundle["per_focus"][fk]["effort_level"]
            assert effort in ("light", "moderate", "aggressive")

    def test_invalid_file_raises_value_error(self):
        from engine.roadmap_ai_engine import parse_pattern_native
        with pytest.raises((ValueError, Exception)):
            parse_pattern_native(b"\x00\x01\x02\x03garbage")


# ── TestClassifyHours ─────────────────────────────────────────────────────────


class TestClassifyHours:
    def test_8h_is_light(self):
        from engine.roadmap_ai_engine import _classify_hours
        assert _classify_hours(8.0) == "light"

    def test_9h_is_moderate(self):
        from engine.roadmap_ai_engine import _classify_hours
        assert _classify_hours(9.0) == "moderate"

    def test_20h_is_moderate(self):
        from engine.roadmap_ai_engine import _classify_hours
        assert _classify_hours(20.0) == "moderate"

    def test_21h_is_aggressive(self):
        from engine.roadmap_ai_engine import _classify_hours
        assert _classify_hours(21.0) == "aggressive"

    def test_zero_is_light(self):
        from engine.roadmap_ai_engine import _classify_hours
        assert _classify_hours(0.0) == "light"


# ── TestMonthlyHoursFromDf ────────────────────────────────────────────────────


class TestMonthlyHoursFromDf:
    def _make_df(self, rows):
        return pd.DataFrame(rows)

    def test_monthly_full_weight(self):
        from engine.roadmap_ai_engine import _monthly_hours_from_df
        df = self._make_df([{"Focus": "Content", "Hours": 10.0, "Occurrence": "Monthly"}])
        result = _monthly_hours_from_df(df)
        assert result.get("content", 0) == pytest.approx(10.0)

    def test_quarterly_one_third(self):
        from engine.roadmap_ai_engine import _monthly_hours_from_df
        df = self._make_df([{"Focus": "Technical", "Hours": 12.0, "Occurrence": "Quarterly"}])
        result = _monthly_hours_from_df(df)
        assert result.get("technical", 0) == pytest.approx(4.0, rel=0.01)

    def test_bi_annual_one_sixth(self):
        from engine.roadmap_ai_engine import _monthly_hours_from_df
        df = self._make_df([{"Focus": "Technical", "Hours": 12.0, "Occurrence": "Bi-Annual"}])
        result = _monthly_hours_from_df(df)
        assert result.get("technical", 0) == pytest.approx(2.0, rel=0.01)

    def test_missing_hours_column_returns_empty(self):
        from engine.roadmap_ai_engine import _monthly_hours_from_df
        df = pd.DataFrame({"Focus": ["Content"]})
        result = _monthly_hours_from_df(df)
        assert result == {}


# ── TestCanonicalizeFocus ─────────────────────────────────────────────────────


class TestCanonicalizeFocus:
    def test_content_variants(self):
        from engine.roadmap_ai_engine import _canonicalize_focus
        for raw in ("Content", "content production", "Copywriting", "Article"):
            assert _canonicalize_focus(raw) == "content"

    def test_off_page_variants(self):
        from engine.roadmap_ai_engine import _canonicalize_focus
        for raw in ("Off-Page", "Links", "Link Building", "Digital PR"):
            assert _canonicalize_focus(raw) == "off_page"

    def test_on_page_variants(self):
        from engine.roadmap_ai_engine import _canonicalize_focus
        for raw in ("On-Page", "on page", "SEO"):
            assert _canonicalize_focus(raw) == "on_page"

    def test_unknown_falls_back_to_strategy(self):
        from engine.roadmap_ai_engine import _canonicalize_focus
        assert _canonicalize_focus("foobar") == "strategy"


# ── TestWrapLegacyAsBundle ────────────────────────────────────────────────────


class TestWrapLegacyAsBundle:
    def test_schema_version_1(self):
        from engine.roadmap_ai_engine import _wrap_legacy_as_bundle
        bundle = _wrap_legacy_as_bundle({"effort_level": "moderate", "content_cadence": 4})
        assert bundle["schema_version"] == "1.0"

    def test_preserves_effort_level(self):
        from engine.roadmap_ai_engine import _wrap_legacy_as_bundle
        bundle = _wrap_legacy_as_bundle({"effort_level": "aggressive"})
        assert bundle["global_rollup"]["effort_level"] == "aggressive"

    def test_per_focus_all_present(self):
        from engine.roadmap_ai_engine import _wrap_legacy_as_bundle
        bundle = _wrap_legacy_as_bundle({})
        for fk in ("content", "technical", "on_page", "off_page", "local", "analytics", "strategy"):
            assert fk in bundle["per_focus"]


# ── TestLoadRoadmapV2NativeNoAI ───────────────────────────────────────────────


class TestLoadRoadmapV2NativeNoAI:
    """load_roadmap_v2 with pattern_native format, client=None, enrich=False."""

    def test_returns_v2_bundle(self):
        from engine.roadmap_ai_engine import load_roadmap_v2
        bundle, used_model = load_roadmap_v2(
            client=None,
            raw_bytes=_fixture_bytes(),
            filename="sample_pattern_native_roadmap.xlsx",
            enrich=False,
        )
        assert bundle["schema_version"] == "2.0"
        assert used_model == "deterministic"

    def test_content_plan_present(self):
        from engine.roadmap_ai_engine import load_roadmap_v2
        bundle, _ = load_roadmap_v2(
            client=None,
            raw_bytes=_fixture_bytes(),
            filename="sample_pattern_native_roadmap.xlsx",
            enrich=False,
        )
        assert len(bundle.get("content_plan", [])) > 0


# ── TestAssumptionsV2Bundle ───────────────────────────────────────────────────


class TestAssumptionsV2Bundle:
    """Verify v2 bundles correctly populate the assumptions store."""

    def _make_v2_bundle(self):
        return {
            "schema_version": "2.0",
            "client_metadata": {
                "client_name": "Test Brand",
                "industry": "Fashion / Apparel",
                "retainer_aud_monthly": 4500.0,
            },
            "per_focus": {
                "content": {"effort_level": "aggressive", "monthly_hours": 25.0},
                "technical": {"effort_level": "moderate", "monthly_hours": 8.0},
                "on_page": {"effort_level": "moderate", "monthly_hours": 10.0},
                "off_page": {"effort_level": "light", "monthly_hours": 5.0},
                "local": {"effort_level": "light", "monthly_hours": 0.0},
                "analytics": {"effort_level": "light", "monthly_hours": 3.0},
                "strategy": {"effort_level": "light", "monthly_hours": 2.0},
            },
            "timeline": {
                "months_covered": 12,
                "strategy_restart_month": 10,
            },
        }

    def test_client_name_detected(self):
        from engine.assumptions import initialise_assumptions, run_detection, get_assumption
        store = {}
        initialise_assumptions(store)
        run_detection(store, roadmap_data=self._make_v2_bundle())
        assert get_assumption(store, "client_name") == "Test Brand"

    def test_industry_detected(self):
        from engine.assumptions import initialise_assumptions, run_detection, get_assumption
        store = {}
        initialise_assumptions(store)
        run_detection(store, roadmap_data=self._make_v2_bundle())
        assert "fashion" in get_assumption(store, "industry").lower()

    def test_retainer_detected(self):
        from engine.assumptions import initialise_assumptions, run_detection, get_assumption
        store = {}
        initialise_assumptions(store)
        run_detection(store, roadmap_data=self._make_v2_bundle())
        assert get_assumption(store, "retainer_aud_monthly") == pytest.approx(4500.0)

    def test_strategy_restart_month_detected(self):
        from engine.assumptions import initialise_assumptions, run_detection, get_assumption
        store = {}
        initialise_assumptions(store)
        run_detection(store, roadmap_data=self._make_v2_bundle())
        assert get_assumption(store, "strategy_restart_month") == 10

    def test_rollup_effort_level_correct(self):
        from engine.assumptions import initialise_assumptions, run_detection, get_assumption
        store = {}
        initialise_assumptions(store)
        run_detection(store, roadmap_data=self._make_v2_bundle())
        # content=aggressive, on_page=moderate, off_page=light → max = aggressive
        assert get_assumption(store, "effort_level") == "aggressive"

    def test_timeline_months_detected(self):
        from engine.assumptions import initialise_assumptions, run_detection, get_assumption
        store = {}
        initialise_assumptions(store)
        run_detection(store, roadmap_data=self._make_v2_bundle())
        assert get_assumption(store, "timeline_months_covered") == 12


# ── TestIndustrySeasonalityPriors ─────────────────────────────────────────────


class TestIndustrySeasonalityPriors:
    def test_fashion_november_boost(self):
        from engine.seasonality_engine import INDUSTRY_SEASONALITY_PRIORS
        assert INDUSTRY_SEASONALITY_PRIORS["Fashion / Apparel"][11] > 0

    def test_finance_june_boost(self):
        from engine.seasonality_engine import INDUSTRY_SEASONALITY_PRIORS
        assert INDUSTRY_SEASONALITY_PRIORS["Finance & Insurance"][6] > 0

    def test_all_industries_have_12_months(self):
        from engine.seasonality_engine import INDUSTRY_SEASONALITY_PRIORS
        for industry, priors in INDUSTRY_SEASONALITY_PRIORS.items():
            assert len(priors) == 12, f"{industry} has {len(priors)} months"

    def test_all_values_in_reasonable_range(self):
        from engine.seasonality_engine import INDUSTRY_SEASONALITY_PRIORS
        for industry, priors in INDUSTRY_SEASONALITY_PRIORS.items():
            for m, val in priors.items():
                assert -0.5 <= val <= 0.5, f"{industry} month {m} value {val} out of range"


class TestApplyIndustryBias:
    def _make_flat_seasonality(self):
        return {m: {"traffic_mod": 0.0, "cr_mod": 0.0, "aov_mod": 0.0, "label": f"Month {m}"}
                for m in range(1, 13)}

    def test_fashion_nov_positive_bias(self):
        from engine.seasonality_engine import apply_industry_bias
        base = self._make_flat_seasonality()
        result = apply_industry_bias(base, "Fashion / Apparel", bias_weight=1.0)
        assert result[11]["traffic_mod"] > 0

    def test_unknown_industry_unchanged(self):
        from engine.seasonality_engine import apply_industry_bias
        base = self._make_flat_seasonality()
        result = apply_industry_bias(base, "Completely Unknown Industry XYZ")
        for m in range(1, 13):
            assert result[m]["traffic_mod"] == pytest.approx(base[m]["traffic_mod"])

    def test_bias_weight_zero_no_change(self):
        from engine.seasonality_engine import apply_industry_bias
        base = self._make_flat_seasonality()
        result = apply_industry_bias(base, "Fashion / Apparel", bias_weight=0.0)
        for m in range(1, 13):
            assert result[m]["traffic_mod"] == pytest.approx(0.0)

    def test_partial_bias_weight(self):
        from engine.seasonality_engine import apply_industry_bias, INDUSTRY_SEASONALITY_PRIORS
        base = self._make_flat_seasonality()
        result = apply_industry_bias(base, "Fashion / Apparel", bias_weight=0.5)
        nov_expected = 0.5 * INDUSTRY_SEASONALITY_PRIORS["Fashion / Apparel"][11]
        assert result[11]["traffic_mod"] == pytest.approx(nov_expected)

    def test_case_insensitive_industry_match(self):
        from engine.seasonality_engine import apply_industry_bias
        base = self._make_flat_seasonality()
        result_lower = apply_industry_bias(base, "fashion / apparel", bias_weight=0.5)
        result_upper = apply_industry_bias(base, "FASHION / APPAREL", bias_weight=0.5)
        assert result_lower[11]["traffic_mod"] == pytest.approx(result_upper[11]["traffic_mod"])

    def test_partial_name_match(self):
        from engine.seasonality_engine import apply_industry_bias
        base = self._make_flat_seasonality()
        # "Fashion" is a substring of "Fashion / Apparel"
        result = apply_industry_bias(base, "Fashion", bias_weight=1.0)
        assert result[11]["traffic_mod"] > 0


# ── TestRoadmapContentPlanMatching ────────────────────────────────────────────


class TestRoadmapContentPlanMatching:
    def _make_kw_df(self, keywords):
        return pd.DataFrame({
            "keyword": keywords,
            "volume": [1000] * len(keywords),
            "kd": [30] * len(keywords),
        })

    def test_exact_keyword_match(self):
        from engine.new_content_engine import _match_roadmap_content_plan
        df = self._make_kw_df(["straw hats women"])
        plan = [{"url": "/blog/straw-hats", "keyword": "straw hats women", "page_type": "new", "publish_month": 3}]
        result = _match_roadmap_content_plan(df, plan)
        assert 0 in result
        assert result[0]["publish_month"] == 3

    def test_url_slug_partial_match(self):
        from engine.new_content_engine import _match_roadmap_content_plan
        df = self._make_kw_df(["winter hats women"])
        plan = [{"url": "/collections/winter-hats", "keyword": "", "page_type": "optimise", "publish_month": 5}]
        result = _match_roadmap_content_plan(df, plan)
        # "winter" (6 chars) should match
        assert 0 in result

    def test_empty_plan_returns_empty(self):
        from engine.new_content_engine import _match_roadmap_content_plan
        df = self._make_kw_df(["straw hats"])
        result = _match_roadmap_content_plan(df, [])
        assert result == {}

    def test_no_match_returns_empty(self):
        from engine.new_content_engine import _match_roadmap_content_plan
        df = self._make_kw_df(["completely unrelated keyword xyz"])
        plan = [{"url": "/blog/straw-hats", "keyword": "straw hats women", "page_type": "new", "publish_month": 1}]
        result = _match_roadmap_content_plan(df, plan)
        assert len(result) == 0


# ── TestNewContentEngineWithContentPlan ───────────────────────────────────────


class TestNewContentEngineWithContentPlan:
    def _make_df(self):
        return pd.DataFrame({
            "keyword": ["straw hats women", "raffia baskets", "summer hats", "winter hats women"],
            "volume": [1000, 500, 800, 600],
            "kd": [30, 20, 25, 35],
        })

    def _make_plan(self):
        return [
            {"url": "/blog/straw-hats", "keyword": "straw hats women", "page_type": "new", "publish_month": 2},
            {"url": "/collections/winter-hats", "keyword": "winter hats women", "page_type": "optimise", "publish_month": 4},
        ]

    def test_forecast_runs_with_content_plan(self):
        from engine.new_content_engine import run_new_content_forecast
        kw_df, monthly_df = run_new_content_forecast(
            self._make_df(), da=40, cadence=2, months=12,
            roadmap_content_plan=self._make_plan(),
        )
        assert len(monthly_df) == 12

    def test_amplitude_scale_column_added(self):
        from engine.new_content_engine import run_new_content_forecast
        kw_df, _ = run_new_content_forecast(
            self._make_df(), da=40, cadence=2, months=12,
            roadmap_content_plan=self._make_plan(),
        )
        assert "amplitude_scale" in kw_df.columns

    def test_optimise_gets_reduced_amplitude(self):
        from engine.new_content_engine import run_new_content_forecast
        kw_df, _ = run_new_content_forecast(
            self._make_df(), da=40, cadence=2, months=12,
            roadmap_content_plan=self._make_plan(),
        )
        # winter hats women matches "optimise" type → amplitude_scale should be 0.3
        winter_rows = kw_df[kw_df["keyword"] == "winter hats women"]
        if len(winter_rows) > 0:
            assert winter_rows.iloc[0]["amplitude_scale"] == pytest.approx(0.3)

    def test_no_content_plan_unchanged(self):
        from engine.new_content_engine import run_new_content_forecast
        kw_df, monthly_df = run_new_content_forecast(
            self._make_df(), da=40, cadence=2, months=12,
            roadmap_content_plan=None,
        )
        # All amplitude_scale should be 1.0 (default)
        assert (kw_df["amplitude_scale"] == 1.0).all()

    def test_forecast_runs_without_content_plan(self):
        from engine.new_content_engine import run_new_content_forecast
        kw_df, monthly_df = run_new_content_forecast(
            self._make_df(), da=40, cadence=2, months=12,
        )
        assert len(monthly_df) == 12
        assert len(kw_df) == 4
