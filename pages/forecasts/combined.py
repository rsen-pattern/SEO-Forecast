import pandas as pd
import streamlit as st

from engine.baseline_metrics_engine import forecast_baseline_metrics
from engine.combined_engine import run_combined_forecast
from engine.decay_engine import calculate_portfolio_decay
from engine.revenue_engine import (
    CURRENCY_SYMBOLS,
    INTENT_CVR_MULTIPLIERS,
    compute_intent_weighted_cvr,
    compute_intent_weighted_cvr_per_month,
    intent_revenue_breakdown,
)
from utils.chart_builder import combined_revenue_chart, combined_three_stream_chart
from utils.cvr_aov_resolver import resolve_aov, resolve_cvr
from utils.export import to_csv, to_html_report
from utils.metric_cards import render_forecast_kpis
from utils.page_base import setup_page
from utils.session import (
    COMB_RESULTS,
    GA4_DF,
    HIST_RESULTS,
    KW_EXISTING,
    NC_RESULT,
    POS_RESULT,
    SCENARIO_RESULTS,
    SEASONALITY,
)

store = setup_page(
    "Combined Forecast",
    "Layer multiple forecast streams into a single projection with intent-weighted revenue.",
    data_requirements=["pos_result|nc_result|hist_results:optional"],
)

if SCENARIO_RESULTS not in st.session_state:
    st.info(
        "💡 **Want to compare three scenarios at once?** "
        "Use the **Strategy** page to see the Combined view across three scenarios — "
        "Conservative, Moderate, Aggressive — side-by-side. "
        "This page is for deep-dive analysis on a single forecast configuration."
    )
else:
    # Show all three scenario combined forecasts as tabs — no need to re-run anything
    st.subheader("Strategy Scenario Forecasts")
    _scen_results = st.session_state[SCENARIO_RESULTS]
    _stab_labels = ["🔵 Conservative", "🟢 Moderate", "🟠 Aggressive"]
    _stabs = st.tabs(_stab_labels)
    for _stab, _sname in zip(_stabs, ("Conservative", "Moderate", "Aggressive"), strict=True):
        _sd = _scen_results.get(_sname, {})
        with _stab:
            if "error" in _sd:
                st.error(f"Forecast failed: {_sd['error']}")
                continue
            if "combined_df" not in _sd:
                st.info("No combined data for this scenario.")
                continue
            _scdf = _sd["combined_df"]
            _sfdf = _scdf[_scdf["is_forecast"]]
            _sp50 = "combined_p50" if "combined_p50" in _sfdf.columns else "combined"
            _sup = "positional_uplift_p50" if "positional_uplift_p50" in _sfdf.columns else "positional_uplift"
            _s_end = int(_sfdf[_sp50].iloc[-1]) if not _sfdf.empty else 0
            _s_base = int(_sfdf["baseline"].iloc[-1]) if "baseline" in _sfdf.columns and not _sfdf.empty else 0
            _s_uplift = int(_sfdf[_sup].sum() + _sfdf.get("new_content_uplift", pd.Series(0)).sum()) if not _sfdf.empty else 0
            _s_decay = int(_sfdf["decay"].sum()) if "decay" in _sfdf.columns and not _sfdf.empty else 0
            _preset = _sd.get("preset", {})

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("End Traffic (P50)", f"{_s_end:,}")
            k2.metric("Baseline (End)", f"{_s_base:,}")
            k3.metric("Total Uplift", f"{_s_uplift:,}")
            k4.metric("Decay Risk", f"{_s_decay:,}")

            if "positional_uplift" not in _scdf.columns and "positional_uplift_p50" in _scdf.columns:
                _scdf = _scdf.copy()
                _scdf["positional_uplift"] = _scdf["positional_uplift_p50"]
            st.plotly_chart(
                combined_three_stream_chart(_scdf),
                use_container_width=True,
                key=f"comb_scen_chart_{_sname}",
            )
            st.caption(
                f"Effort: **{_preset.get('effort_level', '—')}** | "
                f"Cadence: **{_preset.get('content_cadence', 0)} posts/mo** | "
                f"Maintenance: **{_preset.get('maintenance_coverage', 0):.0%}** | "
                f"Retainer: **${_preset.get('retainer_aud_monthly', 0):,.0f}/mo**"
            )

    st.divider()
    st.subheader("Standalone Deep-Dive")

# ── Data Availability ──────────────────────────────────────────────────────
ga4_df = st.session_state.get(GA4_DF)
pos_result = st.session_state.get(POS_RESULT)
nc_result = st.session_state.get(NC_RESULT)

has_ga4 = ga4_df is not None
has_positional = (
    pos_result is not None
    and pos_result.get("monthly") is not None
    and not pos_result["monthly"].empty
)
has_new_content = (
    nc_result is not None
    and nc_result.get("monthly_df") is not None
    and not nc_result["monthly_df"].empty
)

if not has_ga4 and not has_positional and not has_new_content:
    st.info(
        "Load data on **Data Upload**, then run at least one forecast:\n\n"
        "- **Positional Forecast** — uplift from improving existing rankings\n"
        "- **New Content Forecast** — traffic from new keyword-targeting pages\n\n"
        "Come back here to combine them."
    )
    st.stop()

# ── Data Status ────────────────────────────────────────────────────────────
st.subheader("Available Data")
c1, c2, c3 = st.columns(3)

ga4_has_revenue = has_ga4 and "revenue" in ga4_df.columns

with c1:
    if has_ga4:
        extras = []
        if ga4_has_revenue:
            extras.append("revenue")
        if "transactions" in ga4_df.columns:
            extras.append("transactions")
        if "aov" in ga4_df.columns:
            extras.append("AOV")
        detail = f" ({', '.join(extras)})" if extras else ""
        st.success(f"GA4: {len(ga4_df)} months{detail}")
    else:
        st.info("GA4: Not loaded")

with c2:
    if has_positional:
        n = len(pos_result.get("keyword_df", []))
        st.success(f"Positional: {n:,} keywords")
    else:
        st.info("Positional: Not yet run")

with c3:
    if has_new_content:
        n = len(nc_result.get("keyword_df", []))
        st.success(f"New Content: {n:,} keywords")
    else:
        st.info("New Content: Not yet run")

# ── Stream Selection ───────────────────────────────────────────────────────
st.subheader("What would you like to combine?")

include_baseline = st.checkbox(
    "Historical Baseline — do-nothing trajectory from GA4 trend",
    value=has_ga4,
    disabled=not has_ga4,
    key="comb_inc_baseline",
)
include_positional = st.checkbox(
    "Positional Uplift — traffic gain from improving existing rankings",
    value=has_positional,
    disabled=not has_positional,
    key="comb_inc_pos",
)
include_new_content = st.checkbox(
    "New Content — traffic from publishing new keyword-targeting pages",
    value=has_new_content,
    disabled=not has_new_content,
    key="comb_inc_nc",
)

if not include_baseline and not include_positional and not include_new_content:
    st.info("Select at least one stream above.")
    st.stop()

# ── Sidebar ────────────────────────────────────────────────────────────────
st.sidebar.header("Combined Forecast Settings")
months = st.sidebar.slider("Forecast Horizon (months)", 6, 36, 12, key="comb_months")

st.sidebar.divider()
st.sidebar.subheader("Revenue Settings")
enable_revenue = st.sidebar.checkbox("Enable Revenue Projection", value=True, key="comb_rev")

_cvr_val, _cvr_src, _cvr_lbl = resolve_cvr(store)
_aov_val, _aov_src, _aov_lbl = resolve_aov(store)

currency_keys = list(CURRENCY_SYMBOLS.keys())
default_cur_idx = currency_keys.index("AUD") if "AUD" in currency_keys else 0

cvr = st.sidebar.number_input(
    "Base Conversion Rate (%)", 0.1, 100.0, _cvr_val, step=0.1,
    key="comb_cvr", disabled=not enable_revenue,
)
aov = st.sidebar.number_input(
    "Average Order Value", 1.0, 100000.0, _aov_val, step=10.0,
    key="comb_aov", disabled=not enable_revenue,
)
currency = st.sidebar.selectbox(
    "Currency", currency_keys, index=default_cur_idx,
    key="comb_cur", disabled=not enable_revenue,
)

if enable_revenue:
    st.sidebar.caption(f"CVR: {_cvr_lbl} · AOV: {_aov_lbl}")
    st.sidebar.caption(
        "Revenue uses intent-weighted conversion: "
        "commercial/transactional keywords convert higher than informational."
    )
    with st.sidebar.expander("Intent CVR Multipliers"):
        for intent, mult in INTENT_CVR_MULTIPLIERS.items():
            st.text(f"  {intent.title()}: {mult}x base CVR")

st.sidebar.divider()
st.sidebar.subheader("Keyword Decay")
include_decay = st.sidebar.checkbox(
    "Model keyword decay (unmaintained pages)", value=True, key="comb_decay",
)
maintenance_coverage = st.sidebar.slider(
    "Maintenance coverage", 0.0, 1.0, 0.0, 0.1,
    key="comb_maint", disabled=not include_decay,
)
st.sidebar.caption(
    "AIO erosion is applied per-stream inside the Positional and New Content forecasts "
    "as a CTR penalty. Run those pages with AIO settings to include it."
)

# ── Generate ───────────────────────────────────────────────────────────────
if st.button("Generate Combined Forecast", type="primary", key="comb_run"):
    with st.spinner("Running combined forecast..."):
        historical_df = ga4_df if include_baseline else None

        pos_monthly = None
        pos_keyword_df = None
        if include_positional and pos_result:
            pos_monthly = pos_result["monthly"]
            pos_keyword_df = pos_result.get("keyword_df")

        nc_monthly = None
        nc_keyword_df = None
        if include_new_content and nc_result:
            nc_monthly = nc_result["monthly_df"]
            nc_keyword_df = nc_result.get("keyword_df")

        decay_df = None
        if include_decay:
            kw_for_decay = st.session_state.get(KW_EXISTING)
            if kw_for_decay is not None and not kw_for_decay.empty:
                decay_df = calculate_portfolio_decay(
                    kw_for_decay, months, maintenance_coverage=maintenance_coverage,
                )

        seasonality = st.session_state.get(SEASONALITY)
        forecast_start_month = None
        if ga4_df is not None and not ga4_df.empty:
            forecast_start_month = (
                ga4_df["date"].iloc[-1] + pd.DateOffset(months=1)
            ).month

        # Pass the Historical page's forecast result as the baseline source so the
        # Combined projection uses the same model (Holt's / Prophet / linear) and
        # trend that the analyst already reviewed — not the internal YoY shortcut.
        hist_results = st.session_state.get(HIST_RESULTS)
        historical_forecast_df = hist_results.get("result") if hist_results else None

        combined_df = run_combined_forecast(
            historical_df=historical_df,
            positional_monthly=pos_monthly,
            new_content_monthly=nc_monthly,
            months=months,
            decay_df=decay_df,
            seasonality=seasonality,
            forecast_start_month=forecast_start_month,
            historical_forecast_df=historical_forecast_df,
        )

        # Build merged keyword set for intent-weighted revenue
        all_kw = []
        if pos_keyword_df is not None and not pos_keyword_df.empty:
            all_kw.append(pos_keyword_df)
        if nc_keyword_df is not None and not nc_keyword_df.empty:
            all_kw.append(nc_keyword_df)

        intent_cvr = cvr
        intent_breakdown = pd.DataFrame()
        if all_kw and enable_revenue:
            merged_kw = pd.concat(all_kw, ignore_index=True)
            intent_cvr = compute_intent_weighted_cvr(merged_kw, cvr)
            intent_breakdown = intent_revenue_breakdown(merged_kw, cvr, aov)

        # Dynamic per-month CVR/AOV from GA4 trend + seasonality
        metrics_df = None
        intent_cvr_series = None
        if enable_revenue and include_baseline and has_ga4:
            seasonality = st.session_state.get("seasonality")
            metrics_df = forecast_baseline_metrics(
                ga4_df, months,
                seasonality=seasonality,
                fallback_cvr=cvr,
                fallback_aov=aov,
            )
            base_cvr_list = metrics_df["cvr"].tolist()
            if all_kw:
                intent_cvr_series = compute_intent_weighted_cvr_per_month(
                    merged_kw, base_cvr_list
                )
            else:
                intent_cvr_series = base_cvr_list

        # Revenue per session from GA4 (legacy fallback when no cr/aov history)
        ga4_rev_per_session = None
        if ga4_has_revenue and include_baseline and metrics_df is None:
            total_rev = ga4_df["revenue"].sum()
            total_traffic = ga4_df["traffic"].sum()
            if total_traffic > 0:
                ga4_rev_per_session = total_rev / total_traffic

        st.session_state[COMB_RESULTS] = {
            "combined_df": combined_df,
            "yoy_rate": combined_df.attrs.get("yoy_rate"),
            "baseline_method": (
                historical_forecast_df.attrs.get("chosen_method") if historical_forecast_df is not None else None
            ),
            "include_baseline": include_baseline,
            "include_positional": include_positional,
            "include_new_content": include_new_content,
            "enable_revenue": enable_revenue,
            "currency": currency,
            "cvr": cvr,
            "intent_cvr": intent_cvr,
            "aov": aov,
            "months": months,
            "intent_breakdown": intent_breakdown,
            "ga4_rev_per_session": ga4_rev_per_session,
            "decay_df": decay_df,
            "metrics_df": metrics_df,
            "intent_cvr_series": intent_cvr_series,
        }

# ── Results ────────────────────────────────────────────────────────────────
if COMB_RESULTS in st.session_state:
    r = st.session_state[COMB_RESULTS]
    combined_df = r["combined_df"]
    forecast_mask = combined_df["is_forecast"]
    forecast_df = combined_df[forecast_mask]

    has_bands = "combined_p50" in combined_df.columns
    combined_col = "combined_p50" if has_bands else "combined"
    pos_col = "positional_uplift_p50" if has_bands else "positional_uplift"

    baseline_end = int(forecast_df["baseline"].iloc[-1])
    combined_end = int(forecast_df[combined_col].iloc[-1])
    pos_total = int(forecast_df[pos_col].sum())
    nc_total = int(forecast_df["new_content_uplift"].sum())
    total_decay = int(forecast_df["decay"].sum()) if "decay" in forecast_df.columns else 0
    total_aio = 0  # AIO is now per-stream; no longer tracked at combined level
    uplift_end = (
        round((combined_end - baseline_end) / baseline_end * 100, 1)
        if baseline_end > 0 else 0
    )

    tab_names = ["\U0001f4ca Combined Chart", "\U0001f4cb Uplift Table", "\U0001f4c8 YoY / MoM"]
    if r["enable_revenue"]:
        tab_names.append("\U0001f4b0 Revenue Analysis")
    tab_names.append("\U0001f4e5 Export")
    tabs = st.tabs(tab_names)
    tab_idx = 0

    # ── Tab: Combined Chart ─────────────────────────────────────────
    with tabs[tab_idx]:
        tab_idx += 1

        render_forecast_kpis(
            baseline_traffic=baseline_end,
            forecast_end_traffic=combined_end,
            total_uplift=pos_total,
            uplift_pct=float(uplift_end),
            baseline_label="Baseline (End)",
            forecast_label="Combined (End)",
            uplift_label="Total Uplift",
            pct_label="Uplift at End",
        )

        _baseline_method = r.get("baseline_method")
        if _baseline_method:
            st.caption(
                f"Baseline projection sourced from Historical Forecast page "
                f"(**{_baseline_method.replace('_', ' ').title()}** method)."
            )
        else:
            st.caption(
                "No Historical Forecast result in session — using internal YoY baseline. "
                "Run the **Historical Forecast** page first for a trend-aware baseline."
            )

        _yoy = r.get("yoy_rate")
        if _yoy is not None and not _baseline_method:
            st.caption(
                f"Baseline uses year-over-year growth at **{_yoy:+.1%}/year** "
                f"(median of same-month comparisons). "
                f"Each forecast month anchors to the same calendar month 12 months prior."
            )

        _season_src = st.session_state.get("assumptions", {}).get(
            "seasonality_source", {}
        )
        _src_val = (
            _season_src.get("value") if isinstance(_season_src, dict) else _season_src
        ) or "default"
        _season_captions = {
            "learned": "Seasonality: learned from GA4 (24+ months of history).",
            "blended": "Seasonality: 50/50 blend of GA4 actuals + AU retail defaults.",
            "default": "Seasonality: AU retail defaults (upload ≥12 months of GA4 to learn).",
        }
        st.caption(_season_captions.get(str(_src_val), _season_captions["default"]))

        fig = combined_three_stream_chart(combined_df)
        st.plotly_chart(fig, use_container_width=True)

        if ga4_df is not None and len(ga4_df) >= 12:
            from engine.historical_engine import calculate_growth_rates
            _rates = calculate_growth_rates(ga4_df["traffic"])
            g1, g2 = st.columns(2)
            g1.metric("Avg MoM Growth (historical)", f"{_rates['avg_mom']:+.1f}%")
            g2.metric(
                "Latest YoY Growth (historical)",
                f"{_rates['latest_yoy']:+.1f}%" if _rates.get("latest_yoy") is not None else "N/A",
            )
            st.caption(
                "These are the historical trends. The baseline projection above should "
                "broadly continue them. If it doesn't, run the **Historical Forecast** page "
                "to see what each model projects, then re-run Combined."
            )

        st.divider()
        streams_desc = []
        if r["include_baseline"]:
            streams_desc.append("historical baseline")
        if r["include_positional"]:
            streams_desc.append(f"positional uplift (**{pos_total:,}** visits)")
        if r["include_new_content"]:
            streams_desc.append(f"new content (**{nc_total:,}** visits)")
        if total_decay > 0:
            streams_desc.append(f"keyword decay (**-{total_decay:,}** visits)")
        streams_desc.append("AIO impact baked into positional/new-content streams")
        band_note = ""
        if has_bands:
            p10_end = int(forecast_df["combined_p10"].iloc[-1])
            p90_end = int(forecast_df["combined_p90"].iloc[-1])
            band_note = f"\n\nP10/P90 range at end: **{p10_end:,}** — **{p90_end:,}** visits/month."
        st.info(
            f"Combined projection using: {', '.join(streams_desc)}.\n\n"
            f"Projected end traffic: **{combined_end:,}** visits/month"
            + (f" — **{uplift_end}%** uplift over baseline." if baseline_end > 0 else ".")
            + band_note
        )

    # ── Tab: Uplift Table ───────────────────────────────────────────
    with tabs[tab_idx]:
        tab_idx += 1

        table_cols = ["date", "baseline", pos_col, "new_content_uplift"]
        rename_map = {
            "date": "Month",
            "baseline": "Baseline",
            pos_col: "Positional",
            "new_content_uplift": "New Content",
        }
        if "decay" in forecast_df.columns and total_decay > 0:
            table_cols.append("decay")
            rename_map["decay"] = "Decay"
        table_cols += [combined_col, "uplift_pct"]
        rename_map[combined_col] = "Combined"
        rename_map["uplift_pct"] = "Uplift %"
        if has_bands:
            table_cols += ["combined_p10", "combined_p90"]
            rename_map["combined_p10"] = "P10"
            rename_map["combined_p90"] = "P90"

        display_df = forecast_df[table_cols].copy()
        display_df["date"] = display_df["date"].dt.strftime("%b %Y")
        display_df = display_df.rename(columns=rename_map)
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ── Tab: YoY / MoM ─────────────────────────────────────────────
    with tabs[tab_idx]:
        tab_idx += 1

        fc_rows = forecast_df.copy()

        mom_vals = fc_rows["mom_pct"].dropna() if "mom_pct" in fc_rows.columns else pd.Series(dtype=float)
        yoy_vals = fc_rows["yoy_pct"].dropna() if "yoy_pct" in fc_rows.columns else pd.Series(dtype=float)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Avg MoM %", f"{mom_vals.mean():+.1f}%" if not mom_vals.empty else "—")
        if not mom_vals.empty:
            peak_mom_idx = mom_vals.abs().idxmax()
            peak_mom_month = fc_rows.loc[peak_mom_idx, "date"].strftime("%b")
            k2.metric("Peak MoM %", f"{mom_vals[peak_mom_idx]:+.1f}% ({peak_mom_month})")
        else:
            k2.metric("Peak MoM %", "—")
        k3.metric("Avg YoY %", f"{yoy_vals.mean():+.1f}%" if not yoy_vals.empty else "—")
        if not yoy_vals.empty:
            peak_yoy_idx = yoy_vals.abs().idxmax()
            k4.metric("Peak YoY %", f"{yoy_vals[peak_yoy_idx]:+.1f}%")
        else:
            k4.metric("Peak YoY %", "—")

        # Build display table
        yoy_table_rows = []
        for _, row in fc_rows.iterrows():
            cv = row.get(combined_col)
            yoy_table_rows.append({
                "Month": row["date"].strftime("%b %Y"),
                "Forecast P50": f"{int(cv):,}" if pd.notna(cv) else "—",
                "Prior Year Actual": (
                    f"{int(row['yoy_prior']):,}"
                    if "yoy_prior" in row.index and pd.notna(row.get("yoy_prior"))
                    else "—"
                ),
                "YoY Diff": (
                    f"{int(row['yoy_diff']):+,}"
                    if "yoy_diff" in row.index and pd.notna(row.get("yoy_diff"))
                    else "—"
                ),
                "YoY %": (
                    f"{row['yoy_pct']:+.1f}%"
                    if "yoy_pct" in row.index and pd.notna(row.get("yoy_pct"))
                    else "—"
                ),
                "MoM Diff": (
                    f"{int(row['mom_diff']):+,}"
                    if "mom_diff" in row.index and pd.notna(row.get("mom_diff"))
                    else "—"
                ),
                "MoM %": (
                    f"{row['mom_pct']:+.1f}%"
                    if "mom_pct" in row.index and pd.notna(row.get("mom_pct"))
                    else "—"
                ),
            })

        if yoy_table_rows:
            st.dataframe(pd.DataFrame(yoy_table_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No forecast data available for YoY/MoM comparison.")

        st.caption(
            "Seasonality is baked into the forecast so these comparisons represent "
            "genuine growth, not seasonal noise. Prior Year Actual is pulled from "
            "uploaded GA4 history where the date matches (12 months prior)."
        )

    # ── Tab: Revenue Analysis ───────────────────────────────────────
    if r["enable_revenue"]:
        with tabs[tab_idx]:
            tab_idx += 1
            sym = CURRENCY_SYMBOLS.get(r["currency"], "$")
            rev_per_session = r.get("ga4_rev_per_session")
            intent_cvr = r["intent_cvr"]
            base_cvr = r["cvr"]
            base_aov = r["aov"]
            metrics_df = r.get("metrics_df")
            intent_cvr_series = r.get("intent_cvr_series")

            rev_df = forecast_df.reset_index(drop=True).copy()

            if metrics_df is not None and not metrics_df.empty:
                # Dynamic: trend-aware + seasonality-aware per-month revenue
                rev_df["baseline_revenue"] = metrics_df["revenue"].values

                aov_series_vals = metrics_df["aov"].tolist()
                cvr_vals = (
                    intent_cvr_series
                    if intent_cvr_series is not None
                    else metrics_df["cvr"].tolist()
                )
                uplift_traffic = (
                    rev_df["positional_uplift"] + rev_df["new_content_uplift"]
                )
                rev_df["uplift_revenue"] = [
                    round(
                        float(uplift_traffic.iloc[i]) * cvr_vals[i] / 100.0
                        * aov_series_vals[i],
                        2,
                    )
                    for i in range(len(rev_df))
                ]
                revenue_method = "dynamic (GA4 trend + seasonal CVR/AOV per month)"
            else:
                # Fallback: static scalar CVR/AOV
                if rev_per_session and rev_per_session > 0:
                    rev_df["baseline_revenue"] = (
                        rev_df["baseline"] * rev_per_session
                    ).round(2)
                    revenue_method = f"GA4 revenue/session ({sym}{rev_per_session:.2f})"
                else:
                    rev_df["baseline_revenue"] = (
                        rev_df["baseline"] * (base_cvr / 100) * base_aov
                    ).round(2)
                    revenue_method = (
                        f"CVR ({base_cvr:.2f}%) x AOV ({sym}{base_aov:,.2f})"
                    )
                uplift_traffic = (
                    rev_df["positional_uplift"] + rev_df["new_content_uplift"]
                )
                rev_df["uplift_revenue"] = (
                    uplift_traffic * (intent_cvr / 100) * base_aov
                ).round(2)

            rev_df["combined_revenue"] = (
                rev_df["baseline_revenue"] + rev_df["uplift_revenue"]
            )

            total_baseline_rev = rev_df["baseline_revenue"].sum()
            total_uplift_rev = rev_df["uplift_revenue"].sum()
            total_combined_rev = rev_df["combined_revenue"].sum()
            peak_monthly_rev = rev_df["combined_revenue"].max()

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Baseline Revenue", f"{sym}{total_baseline_rev:,.0f}")
            c2.metric("Uplift Revenue", f"{sym}{total_uplift_rev:,.0f}")
            c3.metric("Combined Revenue", f"{sym}{total_combined_rev:,.0f}")
            c4.metric("Peak Monthly", f"{sym}{peak_monthly_rev:,.0f}")

            fig_rev = combined_revenue_chart(rev_df, sym)
            st.plotly_chart(fig_rev, use_container_width=True)

            # Intent breakdown table
            breakdown = r.get("intent_breakdown")
            if isinstance(breakdown, pd.DataFrame) and not breakdown.empty:
                st.divider()
                st.subheader("Revenue by Keyword Intent")
                st.caption(
                    f"Base CVR: {base_cvr:.2f}% — "
                    f"Intent-weighted blended CVR: {intent_cvr:.2f}%"
                )
                st.dataframe(breakdown, use_container_width=True, hide_index=True)

            st.divider()
            st.info(
                f"**How revenue is calculated:**\n\n"
                f"- **Baseline revenue**: {revenue_method}\n"
                f"- **Uplift revenue**: Intent-weighted CVR "
                f"({'per-month trend' if metrics_df is not None else f'{intent_cvr:.2f}%'}) "
                f"x {'per-month AOV' if metrics_df is not None else f'{sym}{base_aov:,.2f}'}\n"
                f"- Commercial/transactional keywords convert at 1.5–2x; "
                f"informational at 0.3x"
            )

    # ── Tab: Export ──────────────────────────────────────────────────
    with tabs[tab_idx]:
        ec1, ec2 = st.columns(2)
        with ec1:
            st.download_button(
                "Download Combined Forecast CSV",
                to_csv(combined_df),
                "combined-forecast.csv",
                "text/csv",
                key="comb_dl_csv",
            )
        with ec2:
            summary = {
                "Baseline (End)": f"{baseline_end:,}",
                "Combined (End)": f"{combined_end:,}",
                "Uplift": f"{uplift_end}%",
            }
            figs = [combined_three_stream_chart(combined_df)]
            html = to_html_report(figs, summary, "Combined Forecast Report")
            st.download_button(
                "Download HTML Report",
                html,
                "combined-report.html",
                "text/html",
                key="comb_dl_html",
            )

st.divider()
st.caption(
    "**Looking for the three-scenario comparison?** "
    "The Strategy page runs Conservative / Moderate / Aggressive in one click and "
    "produces a four-sheet xlsx ready for client presentations. "
    "This deep-dive page is best for analysts tuning a single forecast configuration."
)
