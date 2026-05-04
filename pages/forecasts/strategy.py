import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.aio_risk_engine import INTENT_AIO_CTR_PENALTY
from engine.assumptions import get_assumption
from engine.historical_engine import calculate_growth_rates, run_historical_forecast_v4
from engine.new_content_engine import get_ctr
from engine.scenario_engine import build_scenario_presets, run_three_scenarios, summarise_scenarios
from utils.chart_builder import (
    _apply_layout,
    combined_three_stream_chart,
    traffic_streams_by_scenario_chart,
)
from utils.design_tokens import PRIMARY, SLATE_400, SLATE_900, SUCCESS
from utils.page_base import setup_page
from utils.session import (
    GA4_DF,
    HIST_RESULTS,
    KW_DF,
    KW_EXISTING,
    ROADMAP_BUNDLE,
    ROADMAP_BUNDLES,
    ROADMAP_CONTENT_PLAN,
    ROADMAP_CONTENT_PLANS,
    SCENARIO_PRESETS,
    SCENARIO_PRESETS_EDITED,
    SCENARIO_RESULTS,
    SEASONALITY,
)

store = setup_page(
    "Strategy",
    "Diagnose the portfolio, pick from three scenario presets, and run all forecasts in one click.",
    show_assumptions_banner=True,
    data_requirements=["ga4", "kw_existing", "roadmap:optional"],
)

ga4 = st.session_state.get(GA4_DF)
kw_existing = st.session_state.get(KW_EXISTING)
kw_df = st.session_state.get(KW_DF)
roadmap_bundle = st.session_state.get(ROADMAP_BUNDLE)
roadmap_bundles = st.session_state.get(ROADMAP_BUNDLES)  # per-scenario override (optional)
roadmap_content_plans = st.session_state.get(ROADMAP_CONTENT_PLANS)  # per-scenario plans

# Invalidate cached presets when per-scenario bundles change so Strategy re-derives them
_bundles_sig = tuple(sorted(roadmap_bundles.keys())) if roadmap_bundles else ()
if st.session_state.get("_roadmap_bundles_sig") != _bundles_sig:
    st.session_state.pop(SCENARIO_PRESETS, None)
    st.session_state["_roadmap_bundles_sig"] = _bundles_sig

if ga4 is None or kw_existing is None:
    st.info(
        "Strategy needs both GA4 organic data and a SEMrush keyword export. "
        "Go to **Data Upload** to load them, then return here."
    )
    st.stop()
else:
    # ── Section 1: Portfolio Diagnosis ───────────────────────────────────────

    st.subheader("Portfolio Diagnosis")

    latest_traffic = int(ga4["traffic"].iloc[-1])
    growth_rates = calculate_growth_rates(ga4["traffic"])
    latest_yoy = growth_rates.get("latest_yoy", 0.0) or 0.0
    avg_mom = growth_rates.get("avg_mom", 0.0) or 0.0

    kw_for_count = kw_existing.copy()
    if "is_branded" in kw_for_count.columns:
        kw_for_count = kw_for_count[~kw_for_count["is_branded"].astype(bool)]
    n_ranking = len(kw_for_count)

    aio_exposure_pct = 0.0
    if "has_aio" in kw_existing.columns:
        aio_exposure_pct = float(kw_existing["has_aio"].astype(bool).mean() * 100)

    branded_share_pct = 0.0
    if kw_df is not None and "is_branded" in kw_df.columns:
        branded_share_pct = float(kw_df["is_branded"].astype(bool).mean() * 100)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Current Monthly Traffic", f"{latest_traffic:,}")
    with c2:
        yoy_str = f"{latest_yoy:+.1f}%" if latest_yoy else "—"
        st.metric("YoY Growth", yoy_str, delta=yoy_str if latest_yoy else None,
                  delta_color="normal")
    with c3:
        mom_str = f"{avg_mom:+.1f}%" if avg_mom else "—"
        st.metric("Avg MoM Growth", mom_str)

    c4, c5, c6 = st.columns(3)
    with c4:
        st.metric("Ranking Keywords (non-branded)", f"{n_ranking:,}")
    with c5:
        st.metric("AIO Exposure", f"{aio_exposure_pct:.1f}%")
    with c6:
        st.metric("Branded Share", f"{branded_share_pct:.1f}%")

    INTENT_COLORS = {
        "informational": "#3B82F6",
        "commercial": "#10B981",
        "transactional": "#F59E0B",
        "navigational": "#8B5CF6",
    }

    col_left, col_right = st.columns(2)

    with col_left:
        if "intent" in kw_existing.columns and "current_traffic" in kw_existing.columns:
            intent_traffic = (
                kw_existing.groupby("intent")["current_traffic"]
                .sum()
                .reset_index()
                .sort_values("current_traffic", ascending=False)
            )
            colors = [INTENT_COLORS.get(i, "#94A3B8") for i in intent_traffic["intent"]]
            fig_donut = go.Figure(go.Pie(
                labels=intent_traffic["intent"],
                values=intent_traffic["current_traffic"],
                hole=0.55,
                marker_colors=colors,
                textinfo="label+percent",
                hovertemplate="%{label}: %{value:,} sessions<extra></extra>",
            ))
            fig_donut = _apply_layout(fig_donut, "Intent Mix (by traffic)", "", "")
            fig_donut.update_layout(showlegend=False, height=300)
            st.plotly_chart(fig_donut, use_container_width=True)
        else:
            st.caption("Intent data not available — upload a keyword portfolio.")

    with col_right:
        if "position" in kw_existing.columns and "volume" in kw_existing.columns:
            qw = kw_existing.copy()
            if "is_branded" in qw.columns:
                qw = qw[~qw["is_branded"].astype(bool)]
            qw = qw[qw["position"].between(5, 20)].copy()
            if not qw.empty:
                ctr_p5 = get_ctr(5)
                qw["opp_score"] = qw.apply(
                    lambda r: int(r["volume"] * (ctr_p5 - get_ctr(int(r["position"]))) / 100),
                    axis=1,
                )
                top10 = qw.nlargest(10, "opp_score")[
                    ["keyword", "position", "volume", "opp_score"]
                ].reset_index(drop=True)
                fig_bar = go.Figure(go.Bar(
                    x=top10["opp_score"],
                    y=top10["keyword"],
                    orientation="h",
                    marker_color=PRIMARY,
                    hovertemplate="%{y}: %{x:,} opp. sessions<extra></extra>",
                ))
                fig_bar = _apply_layout(fig_bar, "Top 10 Quick Wins (pos 5–20)",
                                        "Opportunity (sessions)", "")
                fig_bar.update_layout(height=300, yaxis={"autorange": "reversed"})
                st.plotly_chart(fig_bar, use_container_width=True)
            else:
                st.caption("No non-branded keywords in positions 5–20.")
        else:
            st.caption("Position/volume data not available.")

    # Auto-generated recommendations
    recommendations = []
    if aio_exposure_pct > 10:
        recommendations.append(
            f"**{aio_exposure_pct:.0f}% of your portfolio shows AI Overviews** — "
            "consider shifting content mix toward commercial/transactional queries."
        )
    informational_traffic_pct = 0.0
    if "intent" in kw_existing.columns and "current_traffic" in kw_existing.columns:
        total_t = kw_existing["current_traffic"].sum()
        if total_t > 0:
            info_t = kw_existing.loc[kw_existing["intent"] == "informational", "current_traffic"].sum()
            informational_traffic_pct = float(info_t / total_t * 100)
    if informational_traffic_pct > 50:
        recommendations.append(
            "Informational queries dominate your traffic. "
            "They decay faster than commercial content — make sure new content "
            "targets transactional keywords to offset."
        )
    if branded_share_pct > 40:
        recommendations.append(
            f"Branded keywords account for {branded_share_pct:.0f}% of your portfolio. "
            "Non-branded forecasts exclude these to avoid distorting uplift math."
        )
    for rec in recommendations:
        st.info(rec)

    st.divider()

    # ── Section 2: Three Scenario Presets ────────────────────────────────────

    st.subheader("Three Scenarios")
    st.caption(
        "Pick a preset to tune before running. Moderate is pre-filled from your "
        "roadmap if uploaded; otherwise generic industry presets."
    )

    if SCENARIO_PRESETS not in st.session_state:
        st.session_state[SCENARIO_PRESETS] = build_scenario_presets(
            roadmap_bundle=roadmap_bundle,
            roadmap_bundles=roadmap_bundles,
        )
    presets = st.session_state[SCENARIO_PRESETS]

    _per_scenario_count = len(roadmap_bundles) if roadmap_bundles else 0
    if _per_scenario_count == 3:
        st.success(
            "Per-scenario roadmaps loaded — each scenario is pre-filled from its own SOW. "
            "Conservative, Moderate, and Aggressive content plans will differ."
        )
    elif _per_scenario_count > 0:
        _missing = [s for s in ("Conservative", "Moderate", "Aggressive") if s not in roadmap_bundles]
        st.info(
            f"{_per_scenario_count}/3 per-scenario roadmaps loaded. "
            f"**{', '.join(_missing)}** inherit from the primary roadmap. "
            "Upload all three on the Roadmap page to fully differentiate each scenario."
        )
    elif presets["Moderate"].get("source") == "roadmap-detected":
        st.success(
            f"Moderate preset pre-filled from your roadmap "
            f"({presets['Moderate']['total_monthly_hours']:.0f} hours/month at "
            f"${presets['Moderate']['retainer_aud_monthly']:,.0f}/month retainer)."
        )

    edited = {s: dict(v) for s, v in presets.items()}
    EFFORT_OPTIONS = ["light", "moderate", "aggressive"]
    SCENARIO_ORDER = ["Conservative", "Moderate", "Aggressive"]

    cols = st.columns(3)
    for col, scenario_name in zip(cols, SCENARIO_ORDER, strict=True):
        with col:
            p = edited[scenario_name]
            st.markdown(f"**{scenario_name}**")
            p["effort_level"] = st.selectbox(
                "Effort", EFFORT_OPTIONS,
                index=EFFORT_OPTIONS.index(p["effort_level"]),
                key=f"strat_effort_{scenario_name}",
            )
            p["total_monthly_hours"] = st.number_input(
                "Monthly Hours", min_value=0.0, max_value=200.0,
                value=float(p["total_monthly_hours"]), step=5.0,
                key=f"strat_hours_{scenario_name}",
            )
            p["content_cadence"] = st.number_input(
                "Content Cadence (posts/mo)", min_value=0, max_value=30,
                value=int(p["content_cadence"]), step=1,
                key=f"strat_cadence_{scenario_name}",
            )
            p["maintenance_coverage"] = st.slider(
                "Maintenance Coverage", 0.0, 1.0,
                value=float(p["maintenance_coverage"]), step=0.05,
                key=f"strat_maint_{scenario_name}",
            )
            p["retainer_aud_monthly"] = st.number_input(
                "Retainer (AUD/mo)", min_value=0.0,
                value=float(p["retainer_aud_monthly"]), step=500.0,
                key=f"strat_retainer_{scenario_name}",
            )
            with st.expander("Advanced: Position range"):
                pr_low, pr_high = p["position_range"]
                pr_low = st.number_input(
                    "Target positions — from", min_value=1, max_value=100,
                    value=int(pr_low), step=1,
                    key=f"strat_pr_low_{scenario_name}",
                )
                pr_high = st.number_input(
                    "Target positions — to", min_value=1, max_value=100,
                    value=int(pr_high), step=1,
                    key=f"strat_pr_high_{scenario_name}",
                )
            p["position_range"] = (pr_low, pr_high)

    st.session_state[SCENARIO_PRESETS_EDITED] = edited

    st.divider()

    # ── Section 3: Run All Forecasts ─────────────────────────────────────────

    months = st.slider("Forecast horizon (months)", 6, 36, 12, key="strat_months")

    if HIST_RESULTS not in st.session_state:
        st.caption(
            "Tip: run the **Historical** forecast page first — Strategy will reuse "
            "that baseline instead of computing a fresh one."
        )

    if st.button("Run All Forecasts", type="primary", key="strat_run_all"):
        seasonality = st.session_state.get(SEASONALITY)
        forecast_start_month = pd.Timestamp.now().month
        aio_penalty = get_assumption(store, "aio_ctr_penalty_informational") * 100
        aio_intent_penalties = {**INTENT_AIO_CTR_PENALTY, "informational": aio_penalty}
        content_plan = st.session_state.get(ROADMAP_CONTENT_PLAN)
        active_presets = st.session_state.get(SCENARIO_PRESETS_EDITED) or presets

        with st.spinner("Running three scenarios — Conservative, Moderate, Aggressive..."):
            _hist_cached = st.session_state.get(HIST_RESULTS)
            if _hist_cached and "result" in _hist_cached:
                hist_forecast = _hist_cached["result"]
                st.info("Reusing historical baseline from the Historical page.")
            else:
                hist_forecast = run_historical_forecast_v4(ga4, months=months)
            results = run_three_scenarios(
                ga4_df=ga4,
                kw_df=kw_df if kw_df is not None else kw_existing,
                kw_existing=kw_existing,
                presets=active_presets,
                months=months,
                seasonality=seasonality,
                forecast_start_month=forecast_start_month,
                aio_intent_penalties=aio_intent_penalties,
                roadmap_content_plan=content_plan,
                roadmap_content_plans=roadmap_content_plans,
                historical_forecast_df=hist_forecast,
                seed=42,
                da=st.session_state.get("da", 30),
            )
            st.session_state[SCENARIO_RESULTS] = results
        st.rerun()

    # ── Section 4: Results ───────────────────────────────────────────────────

    results = st.session_state.get(SCENARIO_RESULTS)
    if results is None:
        st.caption("Run forecasts above to see results.")
    else:
        fcast_months = st.session_state.get("strat_months", 12)
        summary = summarise_scenarios(results, months=fcast_months)

        st.subheader("Scenario Comparison")
        st.dataframe(
            summary.style.format({
                "Maintenance": "{:.0%}",
                "Monthly Hours": "{:.0f}",
                "Retainer": "${:,.0f}",
                "Baseline End Traffic": "{:,.0f}",
                "Combined End Traffic (P50)": "{:,.0f}",
                "Total Uplift (P50)": "{:,.0f}",
                "Uplift %": "{:.1f}%",
                "Traffic-at-Risk-from-Decay": "{:,.0f}",
            }),
            use_container_width=True,
            hide_index=True,
        )

        # Three-line comparison chart
        SCENARIO_COLORS = {
            "Conservative": SLATE_400,
            "Moderate": PRIMARY,
            "Aggressive": SUCCESS,
        }
        SCENARIO_ORDER_RESULTS = ["Conservative", "Moderate", "Aggressive"]

        st.subheader("Combined Traffic — All Scenarios")
        st.caption(
            "Bands represent Monte Carlo uncertainty: "
            "**P10** = pessimistic, **P50** = median, **P90** = optimistic. "
            "Solid lines show P50. Run individual forecast pages for full band charts."
        )
        fig_cmp = go.Figure()
        baseline_plotted = False
        for scenario_name in SCENARIO_ORDER_RESULTS:
            scenario = results.get(scenario_name, {})
            if "error" in scenario or "combined_df" not in scenario:
                continue
            cdf = scenario["combined_df"]
            fmask = cdf["is_forecast"]
            if not baseline_plotted:
                fig_cmp.add_trace(go.Scatter(
                    x=cdf.loc[fmask, "date"], y=cdf.loc[fmask, "baseline"],
                    mode="lines", name="Baseline (do nothing)",
                    line=dict(color=SLATE_400, dash="dash", width=2),
                ))
                amask = cdf["actual"].notna()
                fig_cmp.add_trace(go.Scatter(
                    x=cdf.loc[amask, "date"], y=cdf.loc[amask, "actual"],
                    mode="lines+markers", name="Historical",
                    line=dict(color=SLATE_900, width=2),
                ))
                baseline_plotted = True
            p50_col = "combined_p50" if "combined_p50" in cdf.columns else "combined"
            fig_cmp.add_trace(go.Scatter(
                x=cdf.loc[fmask, "date"], y=cdf.loc[fmask, p50_col],
                mode="lines", name=scenario_name,
                line=dict(color=SCENARIO_COLORS.get(scenario_name, "#94A3B8"), width=2),
            ))
        fig_cmp = _apply_layout(
            fig_cmp, "Scenario Comparison (Combined P50)", "Date", "Monthly Organic Sessions"
        )
        st.plotly_chart(fig_cmp, use_container_width=True)

        # Stream-composition chart
        st.subheader("End-of-Horizon Traffic Breakdown")
        st.caption(
            "How the final forecast month is composed: Baseline + Positional Uplift + "
            "New Content − Decay.  Taller bars mean more uplift; deeper decay bars "
            "indicate unmaintained keyword risk."
        )
        st.plotly_chart(
            traffic_streams_by_scenario_chart(results),
            use_container_width=True,
            key="strat_stream_composition_chart",
        )

        # Per-scenario drill-downs
        st.subheader("Per-Scenario Detail")
        for scenario_name in SCENARIO_ORDER_RESULTS:
            scenario = results.get(scenario_name, {})
            with st.expander(f"{scenario_name} scenario", expanded=False):
                if "error" in scenario:
                    st.error(f"Forecast failed: {scenario['error']}")
                    continue
                preset = scenario.get("preset", {})
                cdf = scenario["combined_df"]
                decay_df = scenario.get("decay_df", pd.DataFrame())
                forecast = cdf[cdf["is_forecast"]]

                k1, k2, k3 = st.columns(3)
                p50_col = "combined_p50" if "combined_p50" in forecast.columns else "combined"
                with k1:
                    end_t = int(forecast[p50_col].iloc[-1]) if not forecast.empty else 0
                    st.metric("End Traffic (P50)", f"{end_t:,}")
                with k2:
                    up_col = (
                        "positional_uplift_p50"
                        if "positional_uplift_p50" in forecast.columns
                        else "positional_uplift"
                    )
                    tot_up = (
                        int(forecast[up_col].sum() + forecast["new_content_uplift"].sum())
                        if not forecast.empty else 0
                    )
                    st.metric("Total Uplift (P50)", f"{tot_up:,}")
                with k3:
                    d_risk = (
                        int(decay_df.iloc[-1]["cumulative_decay"])
                        if not decay_df.empty and "cumulative_decay" in decay_df.columns
                        else 0
                    )
                    st.metric("Traffic at Risk (Decay)", f"{d_risk:,}")

                # Ensure backward-compat alias for combined_three_stream_chart
                if "positional_uplift" not in cdf.columns and "positional_uplift_p50" in cdf.columns:
                    cdf = cdf.copy()
                    cdf["positional_uplift"] = cdf["positional_uplift_p50"]
                st.plotly_chart(
                    combined_three_stream_chart(cdf),
                    use_container_width=True,
                    key=f"strat_combined_chart_{scenario_name}",
                )
                st.caption(
                    f"Effort: **{preset.get('effort_level', '—')}** | "
                    f"Cadence: **{preset.get('content_cadence', 0)} posts/mo** | "
                    f"Maintenance: **{preset.get('maintenance_coverage', 0):.0%}** | "
                    f"Retainer: **${preset.get('retainer_aud_monthly', 0):,.0f}/mo**"
                )
                st.caption(
                    "For deeper analysis on this scenario's positional, new content, or combined "
                    "components, head to the individual forecast pages — they'll let you adjust "
                    "settings beyond what the scenario presets offer."
                )

    st.divider()

    if SCENARIO_RESULTS in st.session_state:
        from utils.forecast_grid import build_three_scenario_grid

        _dl_results = st.session_state[SCENARIO_RESULTS]
        _dl_presets = (
            st.session_state.get(SCENARIO_PRESETS_EDITED)
            or st.session_state[SCENARIO_PRESETS]
        )
        _dl_seasonality = st.session_state.get(SEASONALITY)
        _dl_cvr = float(get_assumption(store, "blended_cr_pct"))
        _dl_aov = float(get_assumption(store, "aov"))
        _dl_currency = str(get_assumption(store, "currency"))
        _dl_client = get_assumption(store, "client_name") or ""

        _dl_buf = build_three_scenario_grid(
            scenario_results=_dl_results,
            presets=_dl_presets,
            cvr=_dl_cvr,
            aov=_dl_aov,
            seasonality=_dl_seasonality,
            apply_seasonal_aov=True,
            currency=_dl_currency,
            start_month=pd.Timestamp.now().month,
            client_name=_dl_client,
            fy_label="FY26",
        )
        st.download_button(
            "Download 3-Scenario Forecast Grid XLSX",
            _dl_buf,
            "seo-forecast-three-scenarios.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key="strat_grid_dl",
        )
        st.caption(
            "For deeper customisation (retainer, FY label, start month, CR/AOV overrides), "
            "use the **Deliverables** page."
        )

    st.divider()
    st.info(
        "The three-scenario forecast grid is the headline deliverable for client "
        "presentations. You can also download individual scenario forecasts from the "
        "forecast pages (Historical, Positional, New Content, Combined) for deeper "
        "analysis, or use the **Deliverables** page for full export options including "
        "variance grading and methodology."
    )
