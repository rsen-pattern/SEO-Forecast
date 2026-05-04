import calendar
import datetime
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.assumptions import assumptions_summary as _assumptions_summary
from engine.assumptions import get_assumption
from engine.revenue_engine import CURRENCY_SYMBOLS
from engine.snapshot_engine import (
    build_snapshot,
    compare_to_actuals,
    load_snapshot,
    snapshot_to_bytes,
    summarise_variance,
)
from utils.chart_builder import _apply_layout
from utils.cvr_aov_resolver import resolve_aov, resolve_cvr
from utils.forecast_grid import build_seo_forecast_grid
from utils.page_base import setup_page
from utils.session import (
    COMB_RESULTS,
    GA4_DF,
    HIST_RESULTS,
    POS_RESULT,
    SCENARIO_PRESETS,
    SCENARIO_PRESETS_EDITED,
    SCENARIO_RESULTS,
    SEASONALITY,
)

_METRIC_LABELS = {
    "Traffic": "traffic",
    "Revenue": "revenue",
    "Transactions": "transactions",
    "CVR": "cvr",
    "AOV": "aov",
}
_METRIC_ACTUALS_COL = {
    "traffic": "traffic",
    "revenue": "revenue",
    "transactions": "transactions",
    "cvr": "cr",
    "aov": "aov",
}


def _render_variance(snapshot: dict, ga4_df: pd.DataFrame) -> None:
    st.subheader("Snapshot Metadata")
    meta_cols = st.columns(3)
    meta_cols[0].metric("Client", snapshot.get("client_name", "Unknown"))
    meta_cols[1].metric("Snapshot Date", snapshot.get("snapshot_date", "N/A")[:10])
    engine_versions = snapshot.get("engine_versions", {})
    meta_cols[2].metric("Engine Version", engine_versions.get("snapshot", "N/A"))

    # Metric selector — only revenue/conversion metrics if snapshot has dynamic data
    has_dynamic = bool(snapshot.get("dynamic_metrics"))
    if has_dynamic:
        metric_label = st.selectbox(
            "Metric to grade",
            list(_METRIC_LABELS.keys()),
            index=0,
            key="variance_metric",
        )
    else:
        metric_label = "Traffic"
        st.selectbox(
            "Metric to grade",
            ["Traffic"],
            index=0,
            key="variance_metric",
            disabled=True,
        )
        st.caption(
            "This snapshot was created before the dynamic revenue model. "
            "Only traffic variance is available."
        )

    metric = _METRIC_LABELS[metric_label]

    # Check actuals column availability
    actuals_col = _METRIC_ACTUALS_COL.get(metric, metric)
    if actuals_col not in ga4_df.columns:
        st.warning(
            f"GA4 data does not contain a **{actuals_col}** column. "
            f"Upload GA4 data with {actuals_col} to grade {metric_label} variance."
        )
        return

    comparison = compare_to_actuals(snapshot, ga4_df, metric=metric)

    if comparison.empty:
        st.warning(
            f"No overlapping months between forecast and actuals for **{metric_label}**."
        )
        return

    summary = summarise_variance(comparison)

    st.subheader("Variance Summary")
    kpi_cols = st.columns(4)
    kpi_cols[0].metric("Months Compared", summary["n_months_compared"])
    kpi_cols[1].metric("Mean Variance %", f"{summary['mean_variance_pct']:+.1f}%")
    kpi_cols[2].metric("Within P10-P90 Band", f"{summary['pct_within_band']:.0f}%")
    max_over = summary["max_overshoot_pct"]
    max_under = summary["max_undershoot_pct"]
    kpi_cols[3].metric("Max Over / Undershoot", f"+{max_over:.1f}% / {max_under:.1f}%")

    st.subheader(f"Forecast vs Actuals — {metric_label}")
    fig = go.Figure()
    has_bands = (
        comparison["forecast_p10"].notna().any() and comparison["forecast_p90"].notna().any()
    )
    if has_bands:
        fig.add_trace(go.Scatter(
            x=comparison["date"], y=comparison["forecast_p90"],
            mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=comparison["date"], y=comparison["forecast_p10"],
            mode="lines", line=dict(width=0),
            fill="tonexty", fillcolor="rgba(37, 99, 235, 0.10)",
            name="P10-P90 Band", hoverinfo="skip",
        ))
    fig.add_trace(go.Scatter(
        x=comparison["date"], y=comparison["forecast_p50"],
        mode="lines", name=f"Forecast P50 ({metric_label})",
        line=dict(color="#2563EB", width=2, dash="dash"),
        hovertemplate="%{x|%b %Y}<br>Forecast: %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=comparison["date"], y=comparison["actual"],
        mode="lines+markers", name=f"Actual {metric_label}",
        line=dict(color="#0F172A", width=3),
        hovertemplate="%{x|%b %Y}<br>Actual: %{y:,.2f}<extra></extra>",
    ))
    fig = _apply_layout(fig, f"Forecast vs Actual {metric_label}", "Date", metric_label)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Monthly Comparison")
    display_df = comparison.copy()
    display_df["date"] = display_df["date"].dt.strftime("%b %Y")
    display_df = display_df.rename(columns={
        "date": "Month", "forecast_p10": "Forecast P10", "forecast_p50": "Forecast P50",
        "forecast_p90": "Forecast P90", "actual": f"Actual {metric_label}",
        "variance": "Variance", "variance_pct": "Variance %", "within_band": "Within Band",
    })
    format_cols = ["Forecast P10", "Forecast P50", "Forecast P90",
                   f"Actual {metric_label}", "Variance"]
    for col in format_cols:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(
                lambda v: f"{v:,.2f}" if pd.notna(v) else "-"
            )
    display_df["Variance %"] = display_df["Variance %"].apply(lambda v: f"{v:+.1f}%")
    display_df["Within Band"] = display_df["Within Band"].map({True: "Yes", False: "No"})

    def _highlight_variance(row):
        styles = [""] * len(row)
        if "Variance %" not in row.index or "Within Band" not in row.index:
            return styles
        var_idx = row.index.get_loc("Variance %")
        band_idx = row.index.get_loc("Within Band")
        try:
            var_val = float(row["Variance %"].replace("%", "").replace("+", ""))
        except (ValueError, AttributeError):
            return styles
        if abs(var_val) > 20:
            styles[var_idx] = "background-color: #FEE2E2; color: #991B1B"
        elif abs(var_val) > 10:
            styles[var_idx] = "background-color: #FEF3C7; color: #92400E"
        else:
            styles[var_idx] = "background-color: #DCFCE7; color: #166534"
        styles[band_idx] = (
            "background-color: #FEE2E2; color: #991B1B"
            if row["Within Band"] == "No"
            else "background-color: #DCFCE7; color: #166534"
        )
        return styles

    st.dataframe(
        display_df.style.apply(_highlight_variance, axis=1),
        use_container_width=True, hide_index=True,
    )

    # Assumption context expander — lets analyst compare "what we assumed vs what happened"
    assumptions_at_snapshot = snapshot.get("assumptions_snapshot")
    if assumptions_at_snapshot:
        with st.expander("Assumptions at snapshot time", expanded=False):
            st.caption(
                "These are the assumptions that were active when the forecast was saved. "
                "Compare them to your current settings to understand calibration drift."
            )
            assump_display = pd.DataFrame([
                {
                    "Assumption": row.get("label", row["key"]),
                    "Value": str(row.get("value", "")),
                    "Source": row.get("source", row.get("provenance", "")),
                }
                for row in assumptions_at_snapshot
            ])
            st.dataframe(assump_display, use_container_width=True, hide_index=True)

    st.subheader("Recommendations")
    mean_var = summary["mean_variance_pct"]
    pct_within = summary["pct_within_band"]
    recommendations = []
    if mean_var > 15:
        recommendations.append(
            "Forecast was consistently over-optimistic — consider reducing "
            "effort level or using Conservative scenario."
        )
    if mean_var < -15:
        recommendations.append("Forecast was too conservative — actuals exceeded projections.")
    if pct_within >= 80:
        recommendations.append("Good calibration — 80%+ of actuals fell within the predicted range.")
    if pct_within < 50:
        recommendations.append(
            "Poor calibration — consider widening bands or re-evaluating assumptions."
        )
    if not recommendations:
        recommendations.append("Forecast calibration is within acceptable range. Continue monitoring.")
    for rec in recommendations:
        st.info(rec)


# ── Page header ───────────────────────────────────────────────────────────────
store = setup_page(
    "Deliverables",
    "Export the forecast grid, grade past forecasts, and review methodology.",
    data_requirements=["comb_results:optional"],
)

# ── Sidebar: Grid Export Settings ─────────────────────────────────────────────
st.sidebar.header("Grid Export Settings")

_cvr_val, _cvr_src, _cvr_lbl = resolve_cvr(store)
_aov_val, _aov_src, _aov_lbl = resolve_aov(store)
default_cur = str(get_assumption(store, "currency"))

cvr = st.sidebar.number_input(
    "Conversion Rate (%)", 0.1, 100.0, _cvr_val, step=0.1, key="grid_cvr"
)
aov = st.sidebar.number_input(
    "Average Order Value", 1.0, 100000.0, _aov_val, step=10.0, key="grid_aov"
)
st.sidebar.caption(f"CVR: {_cvr_lbl} · AOV: {_aov_lbl}")
_cur_options = list(CURRENCY_SYMBOLS.keys())
_cur_idx = _cur_options.index(default_cur) if default_cur in _cur_options else 0
currency = st.sidebar.selectbox("Currency", _cur_options, index=_cur_idx, key="grid_currency")
sym = CURRENCY_SYMBOLS.get(currency, "$")
# Derive defaults from uploaded data so the analyst doesn't have to type them
_default_client = str(get_assumption(store, "client_name") or "")
_default_start_month = int(get_assumption(store, "strategy_restart_month") or 7)
_today = datetime.date.today()
_fy_year = _today.year if _today.month <= 6 else _today.year + 1
_default_fy = f"FY{str(_fy_year)[-2:]}"

grid_client = st.sidebar.text_input("Client Name", value=_default_client, key="grid_client")
fy_label = st.sidebar.text_input("FY Label", value=_default_fy, key="grid_fy")
start_month = st.sidebar.selectbox(
    "Start Month", list(range(1, 13)), index=_default_start_month - 1,
    format_func=lambda m: calendar.month_name[m], key="grid_start_month",
)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_grid, tab_variance, tab_methodology = st.tabs([
    "\U0001f4e5 Forecast Grid",
    "\U0001f4ca Variance Analysis",
    "\U0001f4d6 Methodology",
])

# ── Tab: Forecast Grid ────────────────────────────────────────────────────────
with tab_grid:
    sources = []
    if SCENARIO_RESULTS in st.session_state:
        sources.insert(0, "All Three Scenarios (Conservative / Moderate / Aggressive)")
    else:
        st.info(
            "💡 To export all three scenarios in one xlsx, run the **Strategy** page first. "
            "It will populate the 'All Three Scenarios' source option here."
        )
    if COMB_RESULTS in st.session_state:
        sources.append("Combined Forecast")
    if POS_RESULT in st.session_state:
        sources.append("Positional Forecast")
    if HIST_RESULTS in st.session_state:
        sources.append("Historical Forecast")

    if not sources:
        st.info(
            "Run forecasts first. The fastest path: **Data Upload → Strategy → Run All Forecasts**. "
            "Or run individual forecasts on the Positional / Historical / Combined pages."
        )
    else:
        source = st.selectbox("Forecast Source", sources, key="grid_source")

        if source.startswith("All Three Scenarios"):
            st.caption(
                "Four-sheet xlsx: one sheet per scenario (Conservative / Moderate / Aggressive) "
                "plus a Comparison summary. Each sheet has the full monthly metric grid — traffic bands, "
                "transactions, revenue, AOV, CVR, average position, average CTR, seasonality."
            )
            from engine.scenario_engine import summarise_scenarios
            from utils.forecast_grid import build_three_scenario_grid

            _results = st.session_state[SCENARIO_RESULTS]
            _presets = (
                st.session_state.get(SCENARIO_PRESETS_EDITED)
                or st.session_state[SCENARIO_PRESETS]
            )
            _seasonality = st.session_state.get(SEASONALITY)
            _months = st.session_state.get("strat_months", 12)

            st.subheader("Scenario Comparison")
            _summary = summarise_scenarios(_results, months=_months)
            st.dataframe(_summary, use_container_width=True, hide_index=True)

            _mod = _results.get("Moderate", {})
            if "combined_df" in _mod:
                _combined = _mod["combined_df"]
                _forecast = _combined[_combined["is_forecast"]]
                _col = "combined_p50" if "combined_p50" in _forecast.columns else "combined"
                _total_traffic = int(_forecast[_col].sum())
                _total_transactions = int(_total_traffic * cvr / 100.0)
                _total_revenue = _total_transactions * aov
                c1, c2, c3 = st.columns(3)
                c1.metric("Moderate — Year 1 Traffic", f"{_total_traffic:,.0f}")
                c2.metric("Moderate — Year 1 Transactions", f"{_total_transactions:,.0f}")
                c3.metric("Moderate — Year 1 Revenue", f"{sym}{_total_revenue:,.2f}")
                st.caption("Conservative and Aggressive totals are in the downloaded xlsx.")

            _buf = build_three_scenario_grid(
                scenario_results=_results,
                presets=_presets,
                cvr=cvr,
                aov=aov,
                seasonality=_seasonality,
                apply_seasonal_aov=True,
                currency=currency,
                start_month=start_month,
                client_name=grid_client,
                fy_label=fy_label,
            )
            st.download_button(
                "Download 3-Scenario Forecast Grid XLSX",
                _buf,
                "seo-forecast-three-scenarios.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="grid_three_scenario_dl",
            )
            st.caption(
                "Four-sheet xlsx: Conservative, Moderate, Aggressive scenarios plus "
                "a comparison summary. Drop into your client deck or multi-channel plan."
            )

        else:
            scenario_options = {"Conservative (P10)": "p10", "Median (P50)": "p50", "Aggressive (P90)": "p90"}
            has_bands = False

            if source == "Combined Forecast":
                comb_df = st.session_state[COMB_RESULTS]["combined_df"]
                has_bands = "combined_p10" in comb_df.columns
            elif source == "Positional Forecast":
                pos_monthly = st.session_state[POS_RESULT]["monthly"]
                has_bands = "traffic_p10" in pos_monthly.columns

            if has_bands:
                scenario_label = st.selectbox(
                    "Scenario", list(scenario_options.keys()), index=1, key="grid_scenario"
                )
                scenario = scenario_options[scenario_label]
            else:
                scenario = "p50"

            monthly_traffic = []
            monthly_baseline = None
            monthly_positional_uplift = None
            monthly_new_content_uplift = None
            monthly_decay = None
            traffic_p10 = None
            traffic_p90 = None
            comb_data = None

            if source == "Combined Forecast":
                comb_data = st.session_state[COMB_RESULTS]
                combined_df = comb_data["combined_df"]
                forecast_rows = combined_df[combined_df["is_forecast"]].reset_index(drop=True)
                col = f"combined_{scenario}" if f"combined_{scenario}" in forecast_rows.columns else "combined"
                monthly_traffic = forecast_rows[col].tolist()

                # Stream breakdown columns
                monthly_baseline = forecast_rows["baseline"].tolist()
                pos_col = "positional_uplift_p50" if "positional_uplift_p50" in forecast_rows.columns else "positional_uplift"
                if pos_col in forecast_rows.columns:
                    monthly_positional_uplift = forecast_rows[pos_col].tolist()
                if "new_content_uplift" in forecast_rows.columns:
                    monthly_new_content_uplift = forecast_rows["new_content_uplift"].tolist()
                if "decay" in forecast_rows.columns:
                    monthly_decay = forecast_rows["decay"].tolist()

                # Traffic bands
                if has_bands:
                    traffic_p10 = forecast_rows.get("combined_p10", pd.Series()).tolist()
                    traffic_p90 = forecast_rows.get("combined_p90", pd.Series()).tolist()

            elif source == "Positional Forecast":
                pos = st.session_state[POS_RESULT]
                pos_monthly = pos["monthly"]
                col = f"traffic_{scenario}" if f"traffic_{scenario}" in pos_monthly.columns else "traffic"
                monthly_traffic = pos_monthly[col].tolist()
            elif source == "Historical Forecast":
                hist = st.session_state[HIST_RESULTS]
                result = hist["result"]
                forecast_rows = result[result["is_forecast"]]
                best_col = "linear" if "linear" in result.columns else (
                    "exponential_smoothing" if "exponential_smoothing" in result.columns else "sma"
                )
                monthly_traffic = forecast_rows[best_col].tolist()

            if not monthly_traffic:
                st.warning("The selected forecast source contains no forecast data.")
            else:
                n_months = len(monthly_traffic)

                # Per-month CVR/AOV: prefer dynamic metrics from Combined Forecast
                monthly_cvr_list = None
                monthly_aov_list = None
                revenue_p10 = None
                revenue_p90 = None

                if source == "Combined Forecast" and comb_data is not None:
                    metrics_df = comb_data.get("metrics_df")
                    if metrics_df is not None and not metrics_df.empty and len(metrics_df) == n_months:
                        monthly_cvr_list = metrics_df["cvr"].tolist()
                        monthly_aov_list = metrics_df["aov"].tolist()
                        monthly_transactions = metrics_df["transactions"].tolist()
                        monthly_revenue = metrics_df["revenue"].tolist()
                        # Revenue bands from traffic bands × CVR × AOV
                        if traffic_p10 is not None:
                            revenue_p10 = [
                                round(traffic_p10[i] * monthly_cvr_list[i] / 100.0 * monthly_aov_list[i], 2)
                                for i in range(n_months)
                            ]
                        if traffic_p90 is not None:
                            revenue_p90 = [
                                round(traffic_p90[i] * monthly_cvr_list[i] / 100.0 * monthly_aov_list[i], 2)
                                for i in range(n_months)
                            ]
                    else:
                        cvr_decimal = cvr / 100.0
                        monthly_transactions = [round(t * cvr_decimal) for t in monthly_traffic]
                        monthly_revenue = [round(t * aov, 2) for t in monthly_transactions]
                else:
                    cvr_decimal = cvr / 100.0
                    monthly_transactions = [round(t * cvr_decimal) for t in monthly_traffic]
                    monthly_revenue = [round(t * aov, 2) for t in monthly_transactions]

                total_traffic = sum(monthly_traffic)
                total_transactions = sum(monthly_transactions)
                total_revenue = sum(monthly_revenue)

                c1, c2, c3 = st.columns(3)
                c1.metric("Year 1 Traffic", f"{total_traffic:,.0f}")
                c2.metric("Year 1 Transactions", f"{total_transactions:,.0f}")
                c3.metric("Year 1 Revenue", f"{sym}{total_revenue:,.2f}")

                st.subheader("Monthly Preview")
                month_labels = [
                    calendar.month_abbr[(start_month - 1 + i) % 12 + 1]
                    for i in range(n_months)
                ]
                preview_data: dict = {"Month": month_labels}
                preview_data["Traffic"] = [f"{t:,.0f}" for t in monthly_traffic]
                if monthly_cvr_list is not None:
                    preview_data["CVR %"] = [f"{c:.2f}%" for c in monthly_cvr_list]
                if monthly_aov_list is not None:
                    preview_data["AOV"] = [f"{sym}{a:,.2f}" for a in monthly_aov_list]
                preview_data["Transactions"] = [f"{t:,}" for t in monthly_transactions]
                preview_data["Revenue"] = [f"{sym}{r:,.2f}" for r in monthly_revenue]
                preview_df = pd.DataFrame(preview_data)
                st.dataframe(preview_df, use_container_width=True, hide_index=True)

                st.divider()

                # Build assumptions text for the grid's Assumptions column
                assump_rows = _assumptions_summary(store)
                assumptions_text = "\n".join(
                    f"{r['label']}: {r['value']} ({r['source']})"
                    for r in assump_rows
                    if r.get("label") and r.get("value") is not None
                )

                # Budget: use retainer assumption repeated per month (no budget tracking in single-source grid)
                _retainer = float(get_assumption(store, "retainer_aud_monthly") or 0.0)
                monthly_budget_list = [_retainer] * n_months

                # CVR/AOV: default to scalar-derived per-month lists when not available from Combined metrics
                _cvr_list = monthly_cvr_list if monthly_cvr_list is not None else [float(cvr)] * n_months
                _aov_list = monthly_aov_list if monthly_aov_list is not None else [float(aov)] * n_months

                xlsx_buf = build_seo_forecast_grid(
                    monthly_traffic=[float(t) for t in monthly_traffic],
                    monthly_transactions=[float(t) for t in monthly_transactions],
                    monthly_revenue=[float(r) for r in monthly_revenue],
                    monthly_cvr=_cvr_list,
                    monthly_aov=_aov_list,
                    monthly_budget=monthly_budget_list,
                    months=n_months,
                    client_name=grid_client,
                    fy_label=fy_label,
                    start_month=start_month,
                    assumptions_text=assumptions_text,
                )

                sheet_count = 2  # Forecast + Charts (always added by build_seo_forecast_grid)

                dl_col, snap_col = st.columns(2)
                with dl_col:
                    st.download_button(
                        "Download Forecast Grid XLSX",
                        xlsx_buf,
                        "seo-forecast-grid.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="grid_xlsx_dl",
                    )
                    st.caption(
                        "Includes 2 sheets: SEO Channel Forecast (GAZMAN row format) + Charts."
                    )

                # Snapshot download (Combined Forecast only)
                if source == "Combined Forecast" and comb_data is not None:
                    with snap_col:
                        snap_params = {
                            "cvr": cvr,
                            "aov": aov,
                            "currency": currency,
                            "months": n_months,
                            "scenario": scenario,
                        }
                        metrics_df_for_snap = comb_data.get("metrics_df")
                        snap = build_snapshot(
                            client_name=grid_client or "Unknown",
                            combined_df=comb_data["combined_df"],
                            parameters=snap_params,
                            metrics_df=metrics_df_for_snap,
                            assumptions_snapshot=assump_rows,
                        )
                        st.download_button(
                            "Download Forecast Snapshot JSON",
                            snapshot_to_bytes(snap),
                            "forecast-snapshot.json",
                            "application/json",
                            key="grid_snap_dl",
                        )
                        st.caption(
                            "Re-upload to Variance Analysis tab months later "
                            "to grade forecast accuracy."
                        )

# ── Tab: Variance Analysis ────────────────────────────────────────────────────
with tab_variance:
    snapshot_file = st.file_uploader(
        "Upload a forecast snapshot JSON", type=["json"], key="variance_snapshot_upload",
    )
    ga4_df = st.session_state.get(GA4_DF)

    if ga4_df is None:
        st.info("Load GA4 data on the **Data Upload** page first.")
    elif snapshot_file is None:
        st.info("Upload a forecast snapshot JSON to begin the variance analysis.")
    else:
        try:
            snapshot = load_snapshot(snapshot_file.read())
        except Exception as exc:
            st.error(f"Could not parse snapshot file: {exc}")
        else:
            _render_variance(snapshot, ga4_df)

# ── Tab: Methodology ──────────────────────────────────────────────────────────
with tab_methodology:
    methodology_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "methodology.md"
    )
    try:
        with open(methodology_path) as f:
            st.markdown(f.read())
    except FileNotFoundError:
        st.error("Methodology document not found. Ensure methodology.md exists in the project root.")
