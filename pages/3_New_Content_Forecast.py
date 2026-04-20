import os
import streamlit as st
import pandas as pd

from engine.assumptions import initialise_assumptions, get_assumption, get_provenance
from engine.new_content_engine import run_new_content_forecast
from engine.revenue_engine import add_revenue, keyword_revenue_table, CURRENCY_SYMBOLS
from utils.data_loader import load_keywords
from utils.chart_builder import (
    traffic_projection_chart,
    keyword_schedule_chart,
    scenario_comparison_chart,
    revenue_projection_chart,
)
from utils.export import to_csv, to_html_report, keyword_template_csv
from engine.constants import TIER_COLORS, SITE_PRESETS, CTR_MODELS, FORECAST_SCENARIOS
from engine.ai_engine import get_bifrost_client, cluster_keywords, check_cannibalization, generate_content_roadmap
from utils.sidebar import render_ai_settings

st.header("New Content Forecast")
st.caption("Project traffic from new content targeting keywords you don't yet rank for.")

render_ai_settings()

# ── Assumptions store (read-only on this page) ────────────────────────────────
_nc_store = st.session_state.setdefault("assumptions", {})
initialise_assumptions(_nc_store)

# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.header("Keyword Forecast Settings")

# Site Profile Presets
st.sidebar.subheader("Site Profile")
preset_name = st.sidebar.selectbox(
    "Site Profile Preset",
    list(SITE_PRESETS.keys()),
    key="kw_preset",
    help="Select a preset to auto-fill DA, cadence, and horizon.",
)
preset = SITE_PRESETS[preset_name]

da = st.sidebar.slider("Domain Authority (DA)", 1, 100, preset["da"], key="kw_da")

# Pre-select cadence from roadmap-detected content_cadence when available
_cadence_prov = get_provenance(_nc_store, "content_cadence")
_cadence_default = (
    int(get_assumption(_nc_store, "content_cadence"))
    if _cadence_prov["provenance"] != "defaulted"
    else preset["cadence"]
)
cadence = st.sidebar.number_input("Monthly Content Production", 1, 50, _cadence_default, key="kw_cadence")
if _cadence_prov["provenance"] != "defaulted":
    st.sidebar.caption(f"Pre-set from roadmap ({_cadence_prov['provenance']}). Adjust above to override.")

months = st.sidebar.slider("Forecast Horizon (months)", 6, 36, preset["months"], key="kw_months")
seed = st.sidebar.number_input("Random Seed", value=42, step=1, key="kw_seed")

st.sidebar.divider()

# CTR Model & Forecast Scenario
st.sidebar.subheader("Forecast Model")
ctr_model_name = st.sidebar.selectbox(
    "CTR Model",
    list(CTR_MODELS.keys()),
    key="kw_ctr_model",
    help="Standard = traditional CTR. AI-Adjusted = lower CTR reflecting AI Overviews impact.",
)
scenario_name = st.sidebar.selectbox(
    "Forecast Scenario",
    list(FORECAST_SCENARIOS.keys()),
    index=1,  # Default to Moderate
    key="kw_scenario",
    help="Conservative (0.7x), Moderate (1.0x), or Aggressive (1.3x) traffic multiplier.",
)

ctr_model = CTR_MODELS[ctr_model_name]
traffic_multiplier = FORECAST_SCENARIOS[scenario_name]["traffic_multiplier"]

st.sidebar.divider()

# AI Traffic Adjustment
st.sidebar.subheader("AI Traffic Adjustment")
filter_informational = st.sidebar.checkbox(
    "Filter Informational Keywords",
    key="kw_filter_info",
    help="Reduce impact of informational keywords that are losing CTR to AI Overviews.",
)

exclude_informational = False
informational_ctr_penalty = 0.0

if filter_informational:
    filter_mode = st.sidebar.radio(
        "Filter Mode",
        ["Exclude from forecast entirely", "Apply CTR penalty"],
        key="kw_filter_mode",
    )
    if filter_mode == "Exclude from forecast entirely":
        exclude_informational = True
    else:
        informational_ctr_penalty = st.sidebar.slider(
            "CTR Penalty (%)",
            10, 80, 40,
            key="kw_ctr_penalty",
            help="Reduce informational keyword CTR by this percentage.",
        )

st.sidebar.divider()
st.sidebar.subheader("Revenue Settings")
enable_revenue = st.sidebar.checkbox("Enable Revenue Projection", key="kw_rev")
cvr = st.sidebar.number_input("Conversion Rate (%)", 0.1, 100.0, 2.5, step=0.1, key="kw_cvr", disabled=not enable_revenue)
aov = st.sidebar.number_input("Average Order Value", 1.0, 100000.0, 100.0, step=10.0, key="kw_aov", disabled=not enable_revenue)
currency = st.sidebar.selectbox("Currency", list(CURRENCY_SYMBOLS.keys()), key="kw_cur", disabled=not enable_revenue)

st.sidebar.divider()
st.sidebar.subheader("Scenario Comparison")
enable_scenarios = st.sidebar.checkbox("Compare Multiple Cadences", key="kw_scenarios")
cadence_options = st.sidebar.multiselect(
    "Cadences to compare",
    [1, 2, 4, 6, 8, 12],
    default=[2, 4, 8],
    key="kw_cadence_opts",
    disabled=not enable_scenarios,
)

# ── Upload ───────────────────────────────────────────────────────────────────
st.subheader("Upload Keywords CSV")
st.caption("Required columns: keyword, volume, kd — supports CSV, TSV, Excel")

col1, col2, col3 = st.columns([3, 2, 2])
with col1:
    uploaded_file = st.file_uploader("Upload your file", type=["csv", "tsv", "xlsx", "xls"], key="kw_upload")
with col2:
    use_sample = st.checkbox("Use sample data to explore the tool", key="kw_sample")
with col3:
    st.download_button(
        "Download CSV Template",
        keyword_template_csv(),
        "keyword-template.csv",
        "text/csv",
        key="kw_template_dl",
    )

df = None
if uploaded_file is not None:
    df = load_keywords(uploaded_file)
elif use_sample:
    sample_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "sample-keywords.csv")
    df = load_keywords(sample_path)

if df is not None:
    st.markdown(
        f"**{len(df)} keywords loaded** | "
        f"Avg volume: {df['volume'].mean():,.0f} | "
        f"Avg KD: {df['kd'].mean():.0f} | "
        f"Volume range: {df['volume'].min():,} – {df['volume'].max():,}"
    )
    st.dataframe(df.head(10), use_container_width=True, hide_index=True)

# ── Run Forecast ─────────────────────────────────────────────────────────────
if df is not None:
    if st.button("Generate Forecast", type="primary", key="kw_run"):
        with st.spinner("Running keyword forecast..."):
            _roadmap_cp = st.session_state.get("roadmap_content_plan")
            keyword_df, monthly_df = run_new_content_forecast(
                df, da, cadence, months, seed,
                ctr_model=ctr_model,
                traffic_multiplier=traffic_multiplier,
                include_informational=not exclude_informational,
                ai_overview_ctr_penalty=informational_ctr_penalty,
                roadmap_content_plan=_roadmap_cp,
            )

            # Run scenarios if enabled
            scenarios = {}
            if enable_scenarios and cadence_options:
                for c in cadence_options:
                    _, s_monthly = run_new_content_forecast(
                        df, da, c, months, seed,
                        ctr_model=ctr_model,
                        traffic_multiplier=traffic_multiplier,
                        exclude_informational=exclude_informational,
                        informational_ctr_penalty=informational_ctr_penalty,
                    )
                    scenarios[c] = s_monthly

            # Revenue
            if enable_revenue:
                monthly_df = add_revenue(monthly_df, cvr, aov, currency)
                rev_table = keyword_revenue_table(keyword_df, cvr, aov, currency)
            else:
                rev_table = None

            st.session_state["kw_results"] = {
                "keyword_df": keyword_df,
                "monthly_df": monthly_df,
                "scenarios": scenarios,
                "rev_table": rev_table,
                "enable_revenue": enable_revenue,
                "enable_scenarios": enable_scenarios,
                "currency": currency,
                "cvr": cvr,
                "aov": aov,
                "months": months,
                "ctr_model_name": ctr_model_name,
                "scenario_name": scenario_name,
                "exclude_informational": exclude_informational,
                "informational_ctr_penalty": informational_ctr_penalty,
            }

# ── Results ──────────────────────────────────────────────────────────────────
if "kw_results" in st.session_state:
    r = st.session_state["kw_results"]
    keyword_df = r["keyword_df"]
    monthly_df = r["monthly_df"]

    tab_names = ["\U0001f4ca Traffic Projection", "\U0001f4cb Keyword Schedule"]
    if r["enable_revenue"]:
        tab_names.append("\U0001f4b0 Revenue Analysis")
    if r["enable_scenarios"] and r["scenarios"]:
        tab_names.append("\U0001f504 Scenario Comparison")
    tab_names.append("\U0001f916 AI Insights")
    tab_names.append("\U0001f4e5 Export")

    tabs = st.tabs(tab_names)
    tab_idx = 0

    # ── Tab: Traffic Projection ──────────────────────────────────────────
    with tabs[tab_idx]:
        tab_idx += 1

        # KPI cards
        total_visits = monthly_df["traffic"].sum()
        peak_traffic = monthly_df["traffic"].max()
        peak_month = int(monthly_df.loc[monthly_df["traffic"].idxmax(), "month"])
        n_ranking = keyword_df["will_rank"].sum()
        n_total = len(keyword_df)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Projected Visits", f"{total_visits:,}")
        c2.metric("Peak Monthly Traffic", f"{peak_traffic:,}")
        c3.metric("Month of Peak", f"Month {peak_month}")
        c4.metric("Keywords Ranking", f"{n_ranking} / {n_total}")

        fig = traffic_projection_chart(monthly_df)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Projected monthly organic traffic based on keyword targeting, DA, and content cadence.")

    # ── Tab: Keyword Schedule ────────────────────────────────────────────
    with tabs[tab_idx]:
        tab_idx += 1

        # Show informational exclusion callout
        n_excluded = keyword_df.attrs.get("n_excluded_informational", 0)
        if n_excluded > 0:
            st.info(f"**{n_excluded} informational keywords** were excluded from this forecast.")

        display_cols = [
            "rank", "keyword", "volume", "kd", "tier", "intent", "efficiency_score",
            "publish_month", "expected_position", "ctr", "estimated_monthly_traffic",
            "time_to_rank", "traffic_starts_month",
        ]
        display_df = keyword_df[display_cols].copy()
        display_df["efficiency_score"] = display_df["efficiency_score"].round(1)

        st.dataframe(
            display_df.style.apply(
                lambda row: [
                    f"background-color: {TIER_COLORS.get(row['tier'], '')}20"
                    if col == "tier" else ""
                    for col in row.index
                ],
                axis=1,
            ),
            use_container_width=True,
            hide_index=True,
            height=500,
        )

        fig_kw = keyword_schedule_chart(keyword_df)
        st.plotly_chart(fig_kw, use_container_width=True)
        st.caption("Top keywords by estimated monthly traffic, coloured by difficulty tier.")

        # Wasted slots callout
        unlikely = keyword_df[~keyword_df["will_rank"]].head(5)
        if not unlikely.empty:
            st.info("💡 **Keywords unlikely to rank at this DA** — consider deferring these or raising DA:")
            for _, row in unlikely.iterrows():
                st.markdown(f"- **{row['keyword']}** (KD: {row['kd']}, Volume: {row['volume']:,})")

    # ── Tab: Revenue Analysis ────────────────────────────────────────────
    if r["enable_revenue"]:
        with tabs[tab_idx]:
            tab_idx += 1
            sym = CURRENCY_SYMBOLS.get(r["currency"], "$")

            peak_rev = monthly_df["revenue"].max()
            total_rev = monthly_df["revenue"].sum()
            total_leads = monthly_df["leads"].sum()
            avg_monthly_rev = monthly_df["revenue"].mean()

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Peak Monthly Revenue", f"{sym}{peak_rev:,.2f}")
            c2.metric("Avg Monthly Revenue", f"{sym}{avg_monthly_rev:,.2f}")
            c3.metric("Total Revenue (Period)", f"{sym}{total_rev:,.2f}")
            c4.metric("Total Leads", f"{total_leads:,}")

            if r["rev_table"] is not None and not r["rev_table"].empty:
                st.subheader("Per-Keyword Revenue Breakdown")
                st.dataframe(r["rev_table"], use_container_width=True, hide_index=True)

            fig_rev = revenue_projection_chart(monthly_df, sym)
            st.plotly_chart(fig_rev, use_container_width=True)
            st.caption("Monthly revenue projection based on traffic, conversion rate, and average order value.")

    # ── Tab: Scenario Comparison ─────────────────────────────────────────
    if r["enable_scenarios"] and r["scenarios"]:
        with tabs[tab_idx]:
            tab_idx += 1

            # Comparison table
            rows = []
            for c_val, s_df in sorted(r["scenarios"].items()):
                kw_covered = min(len(keyword_df), c_val * months)
                peak_m = int(s_df.loc[s_df["traffic"].idxmax(), "month"]) if s_df["traffic"].max() > 0 else "-"
                rows.append({
                    "Cadence (posts/mo)": c_val,
                    "Keywords Covered": kw_covered,
                    "Peak Month": peak_m,
                    "Peak Traffic": f"{s_df['traffic'].max():,}",
                    "Total Visits": f"{s_df['traffic'].sum():,}",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            fig_sc = scenario_comparison_chart(r["scenarios"])
            st.plotly_chart(fig_sc, use_container_width=True)
            st.caption("Traffic projection under different content production cadences.")

    # ── Tab: AI Insights ────────────────────────────────────────────────
    with tabs[tab_idx]:
        tab_idx += 1

        client = get_bifrost_client(st.session_state.get("bifrost_api_key"))
        ai_model = st.session_state.get("bifrost_model", "openai/gpt-4o-mini")

        if client is None:
            st.info("Set your Bi Frost API key in the sidebar (AI Settings) to enable AI-powered insights.")
        else:
            ai_col1, ai_col2 = st.columns(2)

            with ai_col1:
                st.subheader("Keyword Clusters")
                if st.button("Generate Clusters", key="kw_cluster_btn"):
                    with st.spinner("Clustering keywords..."):
                        try:
                            result, used_model = cluster_keywords(client, keyword_df["keyword"].tolist(), ai_model)
                            if used_model != ai_model:
                                st.info(f"Fell back to {used_model} — selected model was unavailable")
                            clusters = result.get("clusters", [])
                            for cluster in clusters:
                                with st.expander(f"**{cluster.get('name', 'Cluster')}** — {cluster.get('suggested_title', '')}"):
                                    for kw in cluster.get("keywords", []):
                                        st.markdown(f"- {kw}")
                        except Exception as e:
                            st.error(f"Clustering failed: {e}")

            with ai_col2:
                st.subheader("Cannibalization Check")
                existing_urls = st.text_area(
                    "Paste existing URLs (one per line)",
                    height=150,
                    key="kw_existing_urls",
                    placeholder="https://example.com/seo-guide\nhttps://example.com/keyword-research",
                )
                if st.button("Check Cannibalization", key="kw_cannibal_btn"):
                    urls = [u.strip() for u in existing_urls.strip().split("\n") if u.strip()]
                    if not urls:
                        st.warning("Paste at least one existing URL to check against.")
                    else:
                        with st.spinner("Checking cannibalization..."):
                            try:
                                results, used_model = check_cannibalization(
                                    client, keyword_df["keyword"].tolist(), urls, ai_model
                                )
                                if used_model != ai_model:
                                    st.info(f"Fell back to {used_model} — selected model was unavailable")
                                risk_colors = {"high": "#EF4444", "medium": "#F97316", "low": "#EAB308", "none": "#22C55E"}
                                risk_df = pd.DataFrame(results)
                                st.dataframe(
                                    risk_df.style.apply(
                                        lambda row: [
                                            f"background-color: {risk_colors.get(row.get('risk', ''), '')}20"
                                            if col == "risk" else ""
                                            for col in row.index
                                        ],
                                        axis=1,
                                    ),
                                    use_container_width=True,
                                    hide_index=True,
                                )
                            except Exception as e:
                                st.error(f"Cannibalization check failed: {e}")

            st.divider()
            st.subheader("AI Content Roadmap")
            if st.button("Generate Roadmap", key="kw_roadmap_btn"):
                with st.spinner("Generating content roadmap..."):
                    try:
                        months_val = r.get("months", 12)
                        roadmap, used_model = generate_content_roadmap(client, keyword_df, months_val, ai_model)
                        if used_model != ai_model:
                            st.info(f"Fell back to {used_model} — selected model was unavailable")
                        for month_plan in roadmap:
                            month_num = month_plan.get("month", "?")
                            pieces = month_plan.get("content_pieces", [])
                            with st.expander(f"**Month {month_num}** — {len(pieces)} content pieces"):
                                for piece in pieces:
                                    priority = piece.get("priority", "medium")
                                    icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")
                                    st.markdown(
                                        f"{icon} **{piece.get('title', 'Untitled')}** "
                                        f"(~{piece.get('estimated_traffic', 0):,} visits/mo)"
                                    )
                                    kws = piece.get("target_keywords", [])
                                    if kws:
                                        st.caption(f"Keywords: {', '.join(kws)}")
                                    notes = piece.get("notes")
                                    if notes:
                                        st.caption(f"Note: {notes}")
                    except Exception as e:
                        st.error(f"Roadmap generation failed: {e}")

    # ── Tab: Export ──────────────────────────────────────────────────────
    with tabs[tab_idx]:
        ec1, ec2, ec3 = st.columns(3)

        with ec1:
            st.download_button(
                "Download Keyword Forecast CSV",
                to_csv(keyword_df),
                "keyword-forecast.csv",
                "text/csv",
            )
        with ec2:
            st.download_button(
                "Download Monthly Projection CSV",
                to_csv(monthly_df),
                "monthly-projection.csv",
                "text/csv",
            )
        with ec3:
            summary = {
                "Total Visits": f"{monthly_df['traffic'].sum():,}",
                "Peak Traffic": f"{monthly_df['traffic'].max():,}",
                "Keywords Ranking": f"{keyword_df['will_rank'].sum()} / {len(keyword_df)}",
            }
            figs = [traffic_projection_chart(monthly_df), keyword_schedule_chart(keyword_df)]
            html = to_html_report(figs, summary, "Keyword Forecast Report")
            st.download_button(
                "Download HTML Report",
                html,
                "forecast-report.html",
                "text/html",
            )
