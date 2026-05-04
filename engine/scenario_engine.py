"""Three-scenario forecast coordinator.

Produces Conservative / Moderate / Aggressive scenario presets and runs
each end-to-end through the positional, new-content, decay, and combined
engines. This module is a pure coordinator — it never modifies engine
signatures or shares state between scenarios.
"""
from __future__ import annotations

import math

import pandas as pd

from engine.combined_engine import run_combined_forecast
from engine.decay_engine import calculate_portfolio_decay
from engine.new_content_engine import run_new_content_forecast
from engine.positional_engine import run_positional_forecast_mc

_EFFORT_TIERS = ["light", "moderate", "aggressive"]

_GENERIC_PRESETS: dict[str, dict] = {
    "Conservative": {
        "effort_level": "light",
        "content_cadence": 2,
        "maintenance_coverage": 0.3,
        "total_monthly_hours": 10.0,
        "retainer_aud_monthly": 2000.0,
        "position_range": (5, 20),
        "new_content_enabled": False,
        "source": "generic-preset",
    },
    "Moderate": {
        "effort_level": "moderate",
        "content_cadence": 4,
        "maintenance_coverage": 0.6,
        "total_monthly_hours": 25.0,
        "retainer_aud_monthly": 5000.0,
        "position_range": (5, 20),
        "new_content_enabled": True,
        "source": "generic-preset",
    },
    "Aggressive": {
        "effort_level": "aggressive",
        "content_cadence": 8,
        "maintenance_coverage": 0.9,
        "total_monthly_hours": 50.0,
        "retainer_aud_monthly": 10000.0,
        "position_range": (1, 30),
        "new_content_enabled": True,
        "source": "generic-preset",
    },
}


def _shift_effort(effort: str, delta: int) -> str:
    """Move effort one tier up (delta=1) or down (delta=-1), clamped to valid range."""
    idx = _EFFORT_TIERS.index(effort) if effort in _EFFORT_TIERS else 1
    return _EFFORT_TIERS[max(0, min(2, idx + delta))]


def _preset_from_bundle(bundle: dict) -> dict:
    """Build a single scenario preset dict directly from a v2 roadmap bundle."""
    rollup = bundle.get("global_rollup", {})
    meta = bundle.get("client_metadata", {}) or {}
    return {
        "effort_level": rollup.get("effort_level", "moderate"),
        "content_cadence": int(rollup.get("content_cadence", 4)),
        "maintenance_coverage": float(rollup.get("maintenance_coverage", 0.6)),
        "total_monthly_hours": float(rollup.get("total_monthly_hours", 25.0)),
        "retainer_aud_monthly": float(meta.get("retainer_aud_monthly", 5000.0)),
        "position_range": (5, 20),
        "new_content_enabled": True,
        "source": "roadmap-detected",
    }


def build_scenario_presets(
    roadmap_bundle: dict | None = None,
    roadmap_bundles: dict | None = None,
    retainer_aud: float | None = None,
) -> dict[str, dict]:
    """Return three scenario preset configs.

    Args:
        roadmap_bundle: Single v2 bundle (Moderate pre-filled; C/A derived at ±40%).
        roadmap_bundles: Per-scenario bundles keyed by scenario name. When all three
                         keys are present each scenario is pre-filled directly from its
                         own bundle. Partial dicts fall back to the single-bundle path
                         for missing scenarios.
        retainer_aud: Optional override for the Moderate retainer (generic path only).

    Returns:
        Dict with keys 'Conservative', 'Moderate', 'Aggressive'. Each value:
            {
                "effort_level": "light" | "moderate" | "aggressive",
                "content_cadence": int,
                "maintenance_coverage": float,
                "total_monthly_hours": float,
                "retainer_aud_monthly": float,
                "position_range": tuple[int, int],
                "new_content_enabled": bool,
                "source": "roadmap-detected" | "roadmap-detected-per-scenario" | "generic-preset",
            }
    """
    # ── Per-scenario bundles path (when all three provided) ───────────────
    if roadmap_bundles and all(k in roadmap_bundles for k in ("Conservative", "Moderate", "Aggressive")):
        result: dict[str, dict] = {}
        for name in ("Conservative", "Moderate", "Aggressive"):
            p = _preset_from_bundle(roadmap_bundles[name])
            p["source"] = "roadmap-detected-per-scenario"
            result[name] = p
        return result

    # ── Single-bundle path ────────────────────────────────────────────────
    if roadmap_bundle is None:
        presets = {k: dict(v) for k, v in _GENERIC_PRESETS.items()}
        if retainer_aud is not None:
            r = float(retainer_aud)
            presets["Moderate"]["retainer_aud_monthly"] = r
            presets["Conservative"]["retainer_aud_monthly"] = round(r * 0.6, 2)
            presets["Aggressive"]["retainer_aud_monthly"] = round(r * 1.6, 2)
        return presets

    # ── Single roadmap: Moderate from bundle, C/A derived ────────────────
    rollup = roadmap_bundle.get("global_rollup", {})
    meta = roadmap_bundle.get("client_metadata", {}) or {}

    mod_effort = rollup.get("effort_level", "moderate")
    mod_cadence = int(rollup.get("content_cadence", 4))
    mod_maintenance = float(rollup.get("maintenance_coverage", 0.6))
    mod_hours = float(rollup.get("total_monthly_hours", 25.0))
    mod_retainer = float(meta.get("retainer_aud_monthly", 5000.0))

    moderate: dict = {
        "effort_level": mod_effort,
        "content_cadence": mod_cadence,
        "maintenance_coverage": mod_maintenance,
        "total_monthly_hours": mod_hours,
        "retainer_aud_monthly": mod_retainer,
        "position_range": (5, 20),
        "new_content_enabled": True,
        "source": "roadmap-detected",
    }

    conservative: dict = {
        "effort_level": _shift_effort(mod_effort, -1),
        "content_cadence": max(1, round(mod_cadence * 0.6)),
        "maintenance_coverage": round(mod_maintenance * 0.6, 4),
        "total_monthly_hours": round(mod_hours * 0.6, 2),
        "retainer_aud_monthly": round(mod_retainer * 0.6, 2),
        "position_range": (5, 20),
        "new_content_enabled": False,
        "source": "roadmap-detected",
    }

    aggressive: dict = {
        "effort_level": _shift_effort(mod_effort, 1),
        "content_cadence": math.ceil(mod_cadence * 1.6),
        "maintenance_coverage": min(0.95, round(mod_maintenance * 1.6, 4)),
        "total_monthly_hours": round(mod_hours * 1.6, 2),
        "retainer_aud_monthly": round(mod_retainer * 1.6, 2),
        "position_range": (5, 20),
        "new_content_enabled": True,
        "source": "roadmap-detected",
    }

    return {"Conservative": conservative, "Moderate": moderate, "Aggressive": aggressive}


def run_three_scenarios(
    ga4_df: pd.DataFrame | None,
    kw_df: pd.DataFrame,
    kw_existing: pd.DataFrame,
    presets: dict[str, dict],
    months: int = 12,
    seasonality: dict | None = None,
    forecast_start_month: int | None = None,
    aio_intent_penalties: dict | None = None,
    roadmap_content_plan: list[dict] | None = None,
    roadmap_content_plans: dict[str, list[dict]] | None = None,
    historical_forecast_df: pd.DataFrame | None = None,
    seed: int = 42,
    da: int = 30,
) -> dict[str, dict]:
    """Run Conservative / Moderate / Aggressive forecasts end-to-end.

    Args:
        ga4_df: Historical GA4 traffic DataFrame (date, traffic columns).
        kw_df: Full keyword portfolio (used to find unranked keywords for new content).
        kw_existing: Ranking keywords (position 1-100) — input to positional forecast.
        presets: Output of build_scenario_presets().
        months: Forecast horizon in months.
        seasonality: Monthly seasonality modifiers passed to stream engines.
        forecast_start_month: Calendar month of forecast month 1 (for seasonality).
        aio_intent_penalties: Per-intent AIO CTR penalties passed to stream engines.
        roadmap_content_plan: Single content plan used for all scenarios (fallback).
        roadmap_content_plans: Per-scenario content plans keyed by scenario name.
                               Takes priority over roadmap_content_plan; falls back to
                               roadmap_content_plan for any missing scenario key.
        historical_forecast_df: Pre-computed historical forecast; when provided,
                                 overrides the linear baseline in Combined.
        seed: Random seed for MC reproducibility.
        da: Domain authority estimate. Defaults to 30.

    Returns:
        Dict keyed by scenario name. Each value:
            {
                "preset": dict,
                "positional_keyword_df": pd.DataFrame,
                "positional_monthly": pd.DataFrame,
                "new_content_keyword_df": pd.DataFrame | None,
                "new_content_monthly": pd.DataFrame | None,
                "decay_df": pd.DataFrame,
                "combined_df": pd.DataFrame,
            }
        On per-scenario failure: {"error": str(exc)}.
    """
    ga4_baseline = int(ga4_df["traffic"].iloc[-1]) if ga4_df is not None else None
    historical_df = ga4_df

    results: dict[str, dict] = {}

    for scenario_name in ("Conservative", "Moderate", "Aggressive"):
        preset = presets.get(scenario_name, {})

        # Resolve which content plan this scenario uses
        scenario_plan: list[dict] | None = None
        if roadmap_content_plans and scenario_name in roadmap_content_plans:
            scenario_plan = roadmap_content_plans[scenario_name]
        else:
            scenario_plan = roadmap_content_plan

        try:
            # Step 1: Filter kw_existing to non-branded keywords
            kw_pos = kw_existing.copy()
            if "is_branded" in kw_pos.columns:
                kw_pos = kw_pos[~kw_pos["is_branded"].astype(bool)].reset_index(drop=True)

            # Step 2: Positional forecast
            kw_pos_result, positional_monthly = run_positional_forecast_mc(
                kw_pos,
                months=months,
                effort=preset["effort_level"],
                n_trials=500,
                ga4_baseline=ga4_baseline,
                use_attention_curve=True,
                position_range=preset["position_range"],
                seasonality=seasonality,
                forecast_start_month=forecast_start_month,
                aio_intent_penalties=aio_intent_penalties,
                seed=seed,
            )

            # Step 3: New content forecast (optional)
            new_content_kw_df = None
            new_content_monthly = None
            if preset["new_content_enabled"]:
                unranked = (
                    kw_df[kw_df["position"].isna() | (kw_df["position"] > 100)]
                    if "position" in kw_df.columns
                    else pd.DataFrame()
                )
                nc_cols = ["keyword", "volume", "kd"]
                has_unranked = not unranked.empty and all(c in unranked.columns for c in nc_cols)

                if has_unranked or scenario_plan:
                    nc_input = unranked[nc_cols].copy() if has_unranked else pd.DataFrame(columns=nc_cols)
                    new_content_kw_df, new_content_monthly = run_new_content_forecast(
                        nc_input,
                        da=da,
                        cadence=preset["content_cadence"],
                        months=months,
                        seasonality=seasonality,
                        forecast_start_month=forecast_start_month,
                        aio_intent_penalties=aio_intent_penalties,
                        roadmap_content_plan=scenario_plan,
                        seed=seed,
                    )

            # Step 4: Decay
            decay_df = calculate_portfolio_decay(
                kw_existing,
                months=months,
                maintenance_coverage=preset["maintenance_coverage"],
                apply_intent_multipliers=True,
            )

            # Step 5: Combined
            combined_df = run_combined_forecast(
                historical_df=historical_df,
                positional_monthly=positional_monthly,
                new_content_monthly=new_content_monthly,
                months=months,
                decay_df=decay_df,
                historical_forecast_df=historical_forecast_df,
            )

            results[scenario_name] = {
                "preset": preset,
                "positional_keyword_df": kw_pos_result,
                "positional_monthly": positional_monthly,
                "new_content_keyword_df": new_content_kw_df,
                "new_content_monthly": new_content_monthly,
                "decay_df": decay_df,
                "combined_df": combined_df,
            }

        except Exception as exc:  # noqa: BLE001
            results[scenario_name] = {"error": str(exc)}

    return results


def summarise_scenarios(results: dict[str, dict], months: int) -> pd.DataFrame:
    """Build a side-by-side comparison table of the three scenarios.

    Args:
        results: Output of run_three_scenarios().
        months: Forecast horizon (used for labelling only).

    Returns:
        DataFrame with columns:
            Scenario, Effort, Cadence, Maintenance, Monthly Hours, Retainer,
            Baseline End Traffic, Combined End Traffic (P50),
            Total Uplift (P50), Uplift %, Traffic-at-Risk-from-Decay.
        One row per scenario; failed scenarios get zeroed numeric fields.
    """
    rows = []
    for scenario_name in ("Conservative", "Moderate", "Aggressive"):
        scenario = results.get(scenario_name, {})
        preset = scenario.get("preset", {})

        if "error" in scenario:
            rows.append({
                "Scenario": scenario_name,
                "Effort": preset.get("effort_level", "—"),
                "Cadence": preset.get("content_cadence", 0),
                "Maintenance": preset.get("maintenance_coverage", 0.0),
                "Monthly Hours": preset.get("total_monthly_hours", 0.0),
                "Retainer": preset.get("retainer_aud_monthly", 0.0),
                "Baseline End Traffic": 0,
                "Combined End Traffic (P50)": 0,
                "Total Uplift (P50)": 0,
                "Uplift %": 0.0,
                "Traffic-at-Risk-from-Decay": 0,
            })
            continue

        combined_df = scenario.get("combined_df", pd.DataFrame())
        decay_df = scenario.get("decay_df", pd.DataFrame())

        forecast = (
            combined_df[combined_df["is_forecast"]]
            if not combined_df.empty and "is_forecast" in combined_df.columns
            else pd.DataFrame()
        )

        baseline_end = (
            int(forecast["baseline"].iloc[-1])
            if not forecast.empty and "baseline" in forecast.columns
            else 0
        )

        has_p50 = not forecast.empty and "combined_p50" in forecast.columns
        combined_end = int(forecast["combined_p50"].iloc[-1]) if has_p50 else 0

        p50_col = "positional_uplift_p50" if "positional_uplift_p50" in forecast.columns else "positional_uplift"
        pos_uplift = int(forecast[p50_col].sum()) if not forecast.empty and p50_col in forecast.columns else 0
        nc_uplift = (
            int(forecast["new_content_uplift"].sum())
            if not forecast.empty and "new_content_uplift" in forecast.columns
            else 0
        )
        total_uplift = pos_uplift + nc_uplift
        uplift_pct = round(total_uplift / baseline_end * 100, 1) if baseline_end > 0 else 0.0

        traffic_at_risk = (
            int(decay_df.iloc[-1]["cumulative_decay"])
            if not decay_df.empty and "cumulative_decay" in decay_df.columns
            else 0
        )

        rows.append({
            "Scenario": scenario_name,
            "Effort": preset.get("effort_level", "—"),
            "Cadence": preset.get("content_cadence", 0),
            "Maintenance": preset.get("maintenance_coverage", 0.0),
            "Monthly Hours": preset.get("total_monthly_hours", 0.0),
            "Retainer": preset.get("retainer_aud_monthly", 0.0),
            "Baseline End Traffic": baseline_end,
            "Combined End Traffic (P50)": combined_end,
            "Total Uplift (P50)": total_uplift,
            "Uplift %": uplift_pct,
            "Traffic-at-Risk-from-Decay": traffic_at_risk,
        })

    return pd.DataFrame(rows)
