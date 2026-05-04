import os

import pandas as pd
import streamlit as st

from engine.ai_engine import (
    check_cannibalization,
    cluster_keywords,
    generate_content_roadmap,
    get_bifrost_client,
    get_default_model,
)
from engine.assumptions import get_assumption, get_provenance
from engine.brand_classifier import build_classifier
from engine.constants import CTR_MODELS, FORECAST_SCENARIOS, SITE_PRESETS, TIER_COLORS
from engine.new_content_engine import run_new_content_forecast, run_new_content_forecast_simple
from engine.revenue_engine import CURRENCY_SYMBOLS, add_revenue, keyword_revenue_table
from engine.v5.content_clusters import (
    cluster_content_opportunities,
    fallback_per_post_traffic,
    forecast_cluster_traffic_over_horizon,
)
from utils.chart_builder import (
    keyword_schedule_chart,
    revenue_projection_chart,
    scenario_comparison_chart,
    traffic_projection_chart,
)
from utils.cvr_aov_resolver import resolve_aov, resolve_cvr
from utils.data_loader import load_keywords
from utils.export import keyword_template_csv, to_csv, to_html_report
from utils.metric_cards import KPICard, render_kpi_row
from utils.page_base import setup_page
from utils.roadmap_to_keywords import build_keyword_df_from_roadmap, summarise_roadmap_extraction
from utils.session import (
    BIFROST_API_KEY,
    BIFROST_MODEL,
    KW_DF,
    NC_RESULT,
    ROADMAP_CONTENT_PLAN,
    SCENARIO_RESULTS,
)

_nc_store = setup_page(
    "New Content Forecast",
    "Project traffic from new content targeting keywords you don't yet rank for.",
    show_assumptions_banner=False,
    data_requirements=["kw_df|roadmap_content_plan:optional"],
)

if SCENARIO_RESULTS not in st.session_state:
    st.info(
        "💡 **Want to compare three scenarios at once?** "
        "Use the **Strategy** page to run new content forecasts at three production cadences in one click. "
        "This page is for deep-dive analysis on a single forecast configuration."
    )
else:
    st.success(
        "✅ Three scenarios already run via Strategy. "
        "This page lets you drill into a single forecast configuration in detail. "
        "Download the 3-scenario xlsx from **Deliverables** or the Strategy page."
    )

# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.header("New Content Forecast Settings")

# Site Profile Presets
st.sidebar.subheader("Site Profile")
preset_name = st.sidebar.selectbox(
    "Site Profile Preset",
    list(SITE_PRESETS.keys()),
    key="kw_preset",
    help="Select a preset to auto-fill DA, cadence, and horizon.",
)
preset = SITE_PRESETS[preset_name]

_da_session = st.session_state.get("da")
_da_default = int(_da_session) if _da_session is not None else preset["da"]
da = st.sidebar.slider("Domain Authority (DA)", 1, 100, _da_default, key="kw_da")
if _da_session is not None:
    _da_rationale = st.session_state.get("da_rationale", "")
    st.sidebar.caption(f"Pre-set from Data Upload: DA={_da_session}. {_da_rationale[:80] if _da_rationale else ''}")

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
_cvr_val, _cvr_src, _cvr_lbl = resolve_cvr(_nc_store)
_aov_val, _aov_src, _aov_lbl = resolve_aov(_nc_store)
cvr = st.sidebar.number_input("Conversion Rate (%)", 0.1, 100.0, _cvr_val, step=0.1, key="kw_cvr", disabled=not enable_revenue)
aov = st.sidebar.number_input("Average Order Value", 1.0, 100000.0, _aov_val, step=10.0, key="kw_aov", disabled=not enable_revenue)
if enable_revenue:
    st.sidebar.caption(f"CVR: {_cvr_lbl} · AOV: {_aov_lbl}")
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

# ── Content source ──────────────────────────────────────────────────────────
content_plan = st.session_state.get(ROADMAP_CONTENT_PLAN)
semrush_kw_df = st.session_state.get(KW_DF)

st.subheader("New Content Forecast Source")

_semrush_available = st.session_state.get(KW_DF) is not None
content_source = st.radio(
    "What data should drive the forecast?",
    [
        "Auto-cluster from SEMrush (recommended)" if _semrush_available
        else "Auto-cluster from SEMrush (upload SEMrush data first)",
        "Deterministic per-post stream (no keyword data needed)",
        "Keyword gap analysis (upload a CSV of target keywords not yet ranking)",
    ],
    index=0 if _semrush_available else 1,
    key="nc_source",
    help=(
        "**Auto-cluster**: clusters your SEMrush portfolio into topical groups and "
        "sizes per-post capture from median keyword volume × ranking probability. "
        "Requires SEMrush data uploaded on Data Upload page.\n\n"
        "**Deterministic**: flat per-post estimate with S-curve maturation. "
        "Use when you have a content cadence but no keyword gap analysis.\n\n"
        "**Gap analysis**: upload a CSV of target keywords you don't yet rank for."
    ),
)

use_cluster = content_source.startswith("Auto-cluster") and _semrush_available
use_deterministic = content_source.startswith("Deterministic")

if use_cluster:
    st.info(
        "SEMrush keywords are clustered into topical groups. "
        "Per-post capture = median keyword volume × 3 adjacent variants × ranking probability (DA vs KD), "
        "capped 50–600 sessions. Posts are allocated greedily to highest-capture clusters."
    )
    with st.expander("How capture is calculated"):
        st.markdown(
            "Per-post capture = `median keyword volume × 3 (adjacent variants) × ranking probability`. "
            "Floor at 50, ceiling at 600 sessions/post. "
            "Ranking probability = `(DA - mean KD + 50) / 100`, clipped to [0.05, 0.95]. "
            "Posts allocated greedily to highest capture-per-post clusters, capped at "
            "4 posts per cluster (diminishing returns)."
        )

elif use_deterministic:
    st.info(
        "Each post is assumed to capture a target volume of long-tail organic sessions "
        "with a set probability of ranking, following an S-curve maturation schedule. "
        "No keyword data is required."
    )
    det_col1, det_col2, det_col3 = st.columns(3)
    with det_col1:
        det_n_posts = st.number_input("Total posts published over horizon", 1, 200, 25, key="nc_det_posts")
        det_ppm = st.number_input("Posts per month", 1, 12, 2, key="nc_det_ppm")
    with det_col2:
        det_per_post = st.number_input(
            "Mature sessions per post", 50, 5000, 400, step=50, key="nc_det_per_post",
            help="Estimated mature monthly organic sessions for a well-ranking long-tail post. "
                 "Typical range: 200–800 for apparel/lifestyle brands at DA 40-60.",
        )
        det_rank_prob = st.slider(
            "Probability each post ranks meaningfully", 0.1, 0.95, 0.55, key="nc_det_prob",
            help="Fraction of posts that achieve meaningful organic traffic within the horizon.",
        )
    with det_col3:
        det_maturation_tier = st.selectbox(
            "Maturation tier", ["Easy", "Moderate", "Hard", "Very Hard", "Extreme"],
            index=1, key="nc_det_tier",
            help="Controls the S-curve ramp speed (Easy = fast, Extreme = slow).",
        )

st.divider()

# ── Upload (gap-analysis / deterministic override) ────────────────────────────
if use_cluster:
    # Cluster path uses SEMrush from session state — no upload needed
    _semrush_kw_count = len(semrush_kw_df) if semrush_kw_df is not None else 0
    st.info(f"Using SEMrush portfolio from Data Upload ({_semrush_kw_count:,} keywords).")
elif use_deterministic:
    st.subheader("Optional: Override with keyword data")
    st.caption("If you have a gap analysis, upload it here to use keyword-level inputs instead.")
else:
    st.subheader("Upload Gap-Analysis Keywords CSV")
    st.caption("Required columns: keyword, volume, kd — this should be keywords you do NOT currently rank for")

source_options = ["Manual upload (CSV / Excel)"]
if content_plan:
    source_options.insert(0, f"Roadmap content plan ({len(content_plan)} pieces)")

df = None
source = None

if not use_cluster:
    source = st.radio(
        "Keyword source",
        source_options,
        key="nc_kw_source",
        horizontal=True,
        help="When a roadmap is uploaded, its content plan can drive the forecast directly.",
    )

if not use_cluster and source is not None and source.startswith("Roadmap"):
    df = build_keyword_df_from_roadmap(content_plan, semrush_kw_df=semrush_kw_df)
    if df.empty:
        st.warning(
            "Roadmap content plan has no keywords with usable volume data. "
            "Either add target keywords to the content plan, or fall back to manual upload."
        )
    else:
        summary = summarise_roadmap_extraction(content_plan, df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Content pieces", summary["n_content_pieces"])
        c2.metric("Target keywords", summary["n_keywords_total"])
        c3.metric("With SEMrush volume", summary["n_keywords_with_semrush"])
        c4.metric("Using defaults", summary["n_keywords_default"])

        if summary["n_keywords_default"] > 0:
            st.caption(
                f"{summary['n_keywords_default']} keywords don't appear in your SEMrush export — "
                "they use default volume (200) and KD (35). Consider running a SEMrush keyword "
                "research export for your full target set to improve accuracy."
            )

        with st.expander("Preview the extracted keyword set", expanded=False):
            display_cols = ["keyword", "volume", "kd", "_content_url", "_content_type", "_publish_month"]
            available = [c for c in display_cols if c in df.columns]
            st.dataframe(df[available].head(50), use_container_width=True, hide_index=True)

elif not use_cluster:
    # ── Upload ────────────────────────────────────────────────────────────────
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
can_run_kw = df is not None
can_run_det = use_deterministic
can_run_cluster = use_cluster and semrush_kw_df is not None

if can_run_det or can_run_kw or can_run_cluster:
    _roadmap_plan = (
        content_plan
        if (not use_deterministic and not use_cluster and source is not None and source.startswith("Roadmap"))
        else None
    )
    if _roadmap_plan:
        st.info(f"Roadmap content plan active: {len(_roadmap_plan)} URL(s) will drive publish-month assignment.")

    if st.button("Generate Forecast", type="primary", key="kw_run"):
        with st.spinner("Running new content forecast..."):
            if use_cluster:
                # ── Auto-cluster path ─────────────────────────────────────
                _brand_config = st.session_state.get("brand_config")
                _brand_fn = build_classifier(_brand_config) if _brand_config is not None else None
                _seasonality = st.session_state.get("seasonality")
                _fsm = st.session_state.get("forecast_start_month")

                clusters = cluster_content_opportunities(
                    semrush_kw_df,
                    brand_classifier=_brand_fn,
                )

                if clusters.empty:
                    _industry = st.session_state.get("industry_key", "default")
                    _lo, _hi, _rationale = fallback_per_post_traffic(_industry)
                    st.warning(
                        f"Clustering produced no results (too few informational keywords in "
                        f"positions 21-100, or scikit-learn not installed). {_rationale}"
                    )
                    _avg_per_post = (_lo + _hi) // 2
                    monthly_arr = run_new_content_forecast_simple(
                        n_posts_total=cadence * months,
                        months=months,
                        posts_per_month=cadence,
                        per_post_longtail_traffic=_avg_per_post,
                        rank_probability=0.55,
                        seasonality=_seasonality,
                        forecast_start_month=_fsm,
                        seed=int(seed),
                    )
                    keyword_df = pd.DataFrame()
                    monthly_df = pd.DataFrame({"month": range(1, months + 1), "traffic": monthly_arr})
                    cluster_forecast = None
                else:
                    cluster_forecast = forecast_cluster_traffic_over_horizon(
                        clusters,
                        da=da,
                        months=months,
                        posts_per_month=cadence,
                        seasonality=_seasonality,
                        forecast_start_month=_fsm,
                        seed=int(seed),
                    )
                    monthly_arr = cluster_forecast["monthly_total"]
                    monthly_df = pd.DataFrame({
                        "month": range(1, months + 1),
                        "traffic": monthly_arr,
                    })
                    keyword_df = pd.DataFrame()
                    st.session_state["content_clusters"] = cluster_forecast["per_cluster"]

                scenarios = {}

            elif use_deterministic and df is None:
                # ── Pure deterministic stream ─────────────────────────────
                _seasonality = st.session_state.get("seasonality")
                _fsm = st.session_state.get("forecast_start_month")
                monthly_arr = run_new_content_forecast_simple(
                    n_posts_total=det_n_posts,
                    months=months,
                    posts_per_month=det_ppm,
                    per_post_longtail_traffic=det_per_post,
                    rank_probability=det_rank_prob,
                    maturation_tier=det_maturation_tier,
                    seasonality=_seasonality,
                    forecast_start_month=_fsm,
                    seed=int(seed),
                )
                monthly_df = pd.DataFrame({"month": range(1, months + 1), "traffic": monthly_arr})
                keyword_df = pd.DataFrame()
                scenarios = {}
            else:
                # ── Gap-analysis keyword path ─────────────────────────────
                keyword_df, monthly_df = run_new_content_forecast(
                    df, da, cadence, months, seed,
                    ctr_model=ctr_model,
                    traffic_multiplier=traffic_multiplier,
                    include_informational=not exclude_informational,
                    ai_overview_ctr_penalty=informational_ctr_penalty,
                    roadmap_content_plan=_roadmap_plan,
                )

                # Run scenarios if enabled
                scenarios = {}
                if enable_scenarios and cadence_options:
                    for c in cadence_options:
                        _, s_monthly = run_new_content_forecast(
                            df, da, c, months, seed,
                            ctr_model=ctr_model,
                            traffic_multiplier=traffic_multiplier,
                            include_informational=not exclude_informational,
                            ai_overview_ctr_penalty=informational_ctr_penalty,
                        )
                        scenarios[c] = s_monthly

            # Revenue
            if enable_revenue:
                monthly_df = add_revenue(monthly_df, cvr, aov, currency)
                rev_table = keyword_revenue_table(keyword_df, cvr, aov, currency)
            else:
                rev_table = None

            st.session_state[NC_RESULT] = {
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
                "use_cluster": use_cluster,
            }

# ── Results ──────────────────────────────────────────────────────────────────
if NC_RESULT in st.session_state:
    r = st.session_state[NC_RESULT]
    keyword_df = r["keyword_df"]
    monthly_df = r["monthly_df"]

    _has_clusters = r.get("use_cluster") and st.session_state.get("content_clusters") is not None
    tab_names = ["\U0001f4ca Traffic Projection"]
    if _has_clusters:
        tab_names.append("\U0001f9e9 Content Clusters")
    else:
        tab_names.append("\U0001f4cb Keyword Schedule")
    if r["enable_revenue"]:
        tab_names.append("\U0001f4b0 Revenue Analysis")
    if r["enable_scenarios"] and r["scenarios"]:
        tab_names.append("\U0001f504 Scenario Comparison")
    if not _has_clusters:
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
        _has_kw_cols = not keyword_df.empty and "will_rank" in keyword_df.columns
        n_ranking = int(keyword_df["will_rank"].sum()) if _has_kw_cols else 0
        n_total = len(keyword_df)

        fourth_card = (
            KPICard("Forecast Horizon", f"{r.get('months', 12)} months")
            if _has_clusters
            else KPICard("Keywords Ranking", f"{n_ranking} / {n_total}")
        )
        render_kpi_row([
            KPICard("Total Projected Visits", f"{total_visits:,}"),
            KPICard("Peak Monthly Traffic", f"{peak_traffic:,}"),
            KPICard("Month of Peak", f"Month {peak_month}"),
            fourth_card,
        ])

        fig = traffic_projection_chart(monthly_df)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Projected monthly organic traffic based on keyword targeting, DA, and content cadence.")

    # ── Tab: Content Clusters OR Keyword Schedule ────────────────────────
    with tabs[tab_idx]:
        tab_idx += 1

        if _has_clusters:
            # ── Clusters table ───────────────────────────────────────────
            _per_cluster = st.session_state["content_clusters"]
            st.subheader("Content opportunity clusters")
            st.caption(
                "Auto-clustered from SEMrush non-branded informational keywords "
                "in positions 21-100. Posts allocated to highest-capture clusters first."
            )

            _display_cols = [
                c for c in [
                    "cluster_label", "keyword_count", "total_volume",
                    "median_keyword_volume", "mean_kd", "rank_probability",
                    "capture_per_post", "posts_assigned", "m12_traffic",
                ]
                if c in _per_cluster.columns
            ]
            _display_df = _per_cluster[_display_cols].copy()
            _display_df.columns = [
                {"cluster_label": "Cluster", "keyword_count": "Keywords",
                 "total_volume": "Total volume", "median_keyword_volume": "Median vol",
                 "mean_kd": "Mean KD", "rank_probability": "Rank prob",
                 "capture_per_post": "Capture/post", "posts_assigned": "Posts",
                 "m12_traffic": "M12 traffic"}.get(c, c)
                for c in _display_cols
            ]
            st.dataframe(_display_df, use_container_width=True, hide_index=True)

            # Per-cluster drill-down
            _cluster_labels = _per_cluster["cluster_label"].tolist()
            _selected = st.selectbox("Cluster details", options=_cluster_labels, key="nc_cluster_sel")
            if _selected:
                _sel = _per_cluster[_per_cluster["cluster_label"] == _selected].iloc[0]
                st.write(
                    f"**{int(_sel['keyword_count'])} keywords**, "
                    f"total volume {int(_sel['total_volume']):,}/mo"
                )
                if "top_volume_keyword" in _sel:
                    st.write(f"Top keyword by volume: **{_sel['top_volume_keyword']}**")
                if "member_keywords" in _sel:
                    st.dataframe(
                        pd.DataFrame({"keyword": _sel["member_keywords"][:50]}),
                        use_container_width=True, hide_index=True,
                    )

        else:
            # ── Keyword Schedule ─────────────────────────────────────────
            n_excluded = keyword_df.attrs.get("n_excluded_informational", 0)
            if n_excluded > 0:
                st.info(f"**{n_excluded} informational keywords** were excluded from this forecast.")

            _DISPLAY_COLS = [
                "rank", "keyword", "volume", "kd", "tier", "intent", "efficiency_score",
                "publish_month", "expected_position", "ctr", "estimated_monthly_traffic",
                "time_to_rank", "traffic_starts_month",
            ]
            _has_kw_schedule = not keyword_df.empty and "keyword" in keyword_df.columns

            if not _has_kw_schedule:
                st.info(
                    "Keyword-level schedule is not available for this forecast mode. "
                    "Upload a SEMrush keyword export or use the Roadmap content plan to "
                    "enable per-keyword breakdown."
                )
            else:
                display_cols = [c for c in _DISPLAY_COLS if c in keyword_df.columns]
                display_df = keyword_df[display_cols].copy()
                if "efficiency_score" in display_df.columns:
                    display_df["efficiency_score"] = display_df["efficiency_score"].round(1)

                st.dataframe(
                    display_df.style.apply(
                        lambda row: [
                            f"background-color: {TIER_COLORS.get(row['tier'], '')}20"
                            if col == "tier" else ""
                            for col in row.index
                        ],
                        axis=1,
                    ) if "tier" in display_df.columns else display_df,
                    use_container_width=True,
                    hide_index=True,
                    height=500,
                )

                fig_kw = keyword_schedule_chart(keyword_df)
                st.plotly_chart(fig_kw, use_container_width=True)
                st.caption("Top keywords by estimated monthly traffic, coloured by difficulty tier.")

                if "will_rank" in keyword_df.columns:
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

    # ── Tab: AI Insights (keyword mode only) ────────────────────────────
    if not _has_clusters:
        with tabs[tab_idx]:
            tab_idx += 1

            client = get_bifrost_client(st.session_state.get(BIFROST_API_KEY))
            ai_model = st.session_state.get(BIFROST_MODEL, get_default_model())

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
            _has_kw_export = not keyword_df.empty and "will_rank" in keyword_df.columns
            summary = {
                "Total Visits": f"{monthly_df['traffic'].sum():,}",
                "Peak Traffic": f"{monthly_df['traffic'].max():,}",
                "Keywords Ranking": (
                    f"{int(keyword_df['will_rank'].sum())} / {len(keyword_df)}"
                    if _has_kw_export else "—"
                ),
            }
            figs = [traffic_projection_chart(monthly_df)]
            if _has_kw_export:
                figs.append(keyword_schedule_chart(keyword_df))
            html = to_html_report(figs, summary, "Keyword Forecast Report")
            st.download_button(
                "Download HTML Report",
                html,
                "forecast-report.html",
                "text/html",
            )

st.divider()
st.caption(
    "**Looking for the three-scenario comparison?** "
    "The Strategy page runs Conservative / Moderate / Aggressive in one click and "
    "produces a four-sheet xlsx ready for client presentations. "
    "This deep-dive page is best for analysts tuning a single forecast configuration."
)
