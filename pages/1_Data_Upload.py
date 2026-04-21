import json
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.ai_engine import get_bifrost_client, get_default_model
from engine.assumptions import (
    get_assumption,
    override_assumption,
    run_detection,
)
from engine.brand_engine import classify_keywords_as_branded
from engine.roadmap_ai_engine import (
    ROADMAP_BUNDLE_SCHEMA,
    compute_cache_key,
    estimate_extraction_tokens,
    load_roadmap_v2,
)
from engine.seasonality_engine import (
    DEFAULT_SEASONALITY,
    learn_seasonality_from_ga4,
    seasonality_for_portfolio,
)
from utils.assumptions_panel import render_assumptions_banner, render_assumptions_panel
from utils.chart_builder import _apply_layout
from utils.ga4_loader import load_ga4_organic
from utils.keyword_loader import load_keyword_portfolio, split_existing_vs_new
from utils.page_base import setup_page
from utils.roadmap_loader import load_roadmap
from utils.session import (
    BIFROST_API_KEY,
    BIFROST_MODEL,
    DETECTED_BRAND_TERMS,
    GA4_DF,
    KW_DF,
    KW_EXISTING,
    KW_NEW,
    LEARNED_SEASONALITY,
    ROADMAP_AI_CACHE,
    ROADMAP_BUNDLE,
    ROADMAP_CONTENT_PLAN,
    ROADMAP_DATA,
    ROADMAP_FILE_EXT,
    ROADMAP_RAW_BYTES,
    ROADMAP_USED_MODEL,
    SEASONALITY,
)

store = setup_page(
    "Data Upload",
    "Upload GA4 organic traffic, SEMrush keyword exports, and an optional roadmap file. Data flows to all downstream pages.",
    show_assumptions_banner=False,
)

# ── Tabs ─────────────────────────────────────────────────────────────────────
tab_ga4, tab_semrush, tab_roadmap = st.tabs([
    "📊 GA4 Organic Traffic",
    "🔑 SEMrush Keywords",
    "🗺️ Roadmap",
])

# ── GA4 Tab ──────────────────────────────────────────────────────────────────
with tab_ga4:
    uploaded_ga4 = st.file_uploader(
        "Upload GA4 organic export",
        type=["xlsx", "xls"],
        key="ga4_upload",
    )
    use_ga4_sample = st.checkbox("Use sample data (Cable Melbourne)", key="ga4_sample")

    ga4_df = None
    if uploaded_ga4 is not None:
        ga4_df = load_ga4_organic(uploaded_ga4)
    elif use_ga4_sample:
        sample_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "sample-ga4-organic.xlsx"
        )
        ga4_df = load_ga4_organic(sample_path)

    if ga4_df is not None:
        st.session_state[GA4_DF] = ga4_df
        run_detection(store, ga4_df=ga4_df)

        # ── Seasonality Detection ─────────────────────────────────────
        seasonality_dict, season_meta = seasonality_for_portfolio(ga4_df)
        source = season_meta["source"]
        blend_weight = season_meta["blend_weight"]
        n_months = season_meta["months_available"]

        st.session_state[SEASONALITY] = seasonality_dict
        st.session_state[LEARNED_SEASONALITY] = learn_seasonality_from_ga4(ga4_df)
        override_assumption(store, "seasonality_source", source, f"GA4 data ({n_months} months)")
        override_assumption(store, "seasonality_blend_weight", blend_weight, f"GA4 data ({n_months} months)")

        date_min = ga4_df["date"].min()
        date_max = ga4_df["date"].max()
        st.success(
            f"{len(ga4_df)} months loaded ({date_min.strftime('%b %Y')} – {date_max.strftime('%b %Y')})"
        )

        # KPI cards
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Months", f"{len(ga4_df)}")
        c2.metric("Latest Traffic", f"{ga4_df['traffic'].iloc[-1]:,}")
        c3.metric("Avg Traffic", f"{ga4_df['traffic'].mean():,.0f}")
        c4.metric("Date Range", f"{date_min.strftime('%b %Y')} – {date_max.strftime('%b %Y')}")

        # Revenue / transactions KPIs if present
        has_revenue = "revenue" in ga4_df.columns
        has_transactions = "transactions" in ga4_df.columns
        if has_revenue or has_transactions:
            extra_cols = st.columns(4)
            col_idx = 0
            if has_revenue:
                extra_cols[col_idx].metric(
                    "Total Revenue", f"${ga4_df['revenue'].sum():,.2f}"
                )
                col_idx += 1
                extra_cols[col_idx].metric(
                    "Avg Monthly Revenue", f"${ga4_df['revenue'].mean():,.2f}"
                )
                col_idx += 1
            if has_transactions:
                extra_cols[col_idx].metric(
                    "Total Transactions", f"{ga4_df['transactions'].sum():,}"
                )
                col_idx += 1

        # Traffic line chart
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=ga4_df["date"],
                y=ga4_df["traffic"],
                mode="lines+markers",
                name="Organic Traffic",
                line=dict(color="#2563EB", width=3),
                hovertemplate="%{x|%b %Y}<br>Traffic: %{y:,.0f}<extra></extra>",
            )
        )
        fig = _apply_layout(fig, "Monthly Organic Traffic", "Date", "Sessions")
        st.plotly_chart(fig, use_container_width=True)

    elif uploaded_ga4 is not None:
        st.error("Could not parse the uploaded GA4 file. Please check the format.")

# ── SEMrush Tab ──────────────────────────────────────────────────────────────
with tab_semrush:
    uploaded_semrush = st.file_uploader(
        "Upload SEMrush organic positions export",
        type=["csv", "tsv", "xlsx", "xls"],
        key="semrush_upload",
    )
    use_semrush_sample = st.checkbox("Use sample data (Cable Melbourne)", key="semrush_sample")

    kw_df = None
    if uploaded_semrush is not None:
        kw_df = load_keyword_portfolio(uploaded_semrush)
    elif use_semrush_sample:
        sample_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "sample-semrush-export.xlsx"
        )
        kw_df = load_keyword_portfolio(sample_path)

    if kw_df is not None:
        existing_df, new_df = split_existing_vs_new(kw_df)

        st.session_state[KW_DF] = kw_df
        st.session_state[KW_EXISTING] = existing_df
        st.session_state[KW_NEW] = new_df

        # KPI cards
        avg_pos = existing_df["position"].mean() if not existing_df.empty else 0
        aio_count = int(kw_df["has_aio"].sum()) if "has_aio" in kw_df.columns else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Keywords", f"{len(kw_df):,}")
        c2.metric("Currently Ranking", f"{len(existing_df):,}")
        c3.metric("Avg Position", f"{avg_pos:.1f}")
        c4.metric("AIO-Affected", f"{aio_count:,}")

        # Position distribution bar chart
        if not existing_df.empty and "position" in existing_df.columns:
            buckets = [
                ("1-3", 1, 3),
                ("4-10", 4, 10),
                ("11-20", 11, 20),
                ("21-50", 21, 50),
                ("51-100", 51, 100),
            ]
            bucket_labels = []
            bucket_counts = []
            for label, lo, hi in buckets:
                count = int(
                    ((existing_df["position"] >= lo) & (existing_df["position"] <= hi)).sum()
                )
                bucket_labels.append(label)
                bucket_counts.append(count)

            fig_pos = go.Figure()
            fig_pos.add_trace(
                go.Bar(
                    x=bucket_labels,
                    y=bucket_counts,
                    marker_color="#2563EB",
                    hovertemplate="Position %{x}<br>Keywords: %{y}<extra></extra>",
                )
            )
            fig_pos = _apply_layout(
                fig_pos, "Position Distribution", "Position Bucket", "Keyword Count"
            )
            st.plotly_chart(fig_pos, use_container_width=True)

        # Show first 20 rows
        st.subheader("Keyword Preview")
        st.dataframe(kw_df.head(20), use_container_width=True, hide_index=True)

        # ── Brand Classification ──────────────────────────────────────
        st.divider()
        st.subheader("Brand Classification")

        # Detect domain from URL column if present
        detected_domain = ""
        url_cols = [c for c in kw_df.columns if "url" in c.lower() or "page" in c.lower()]
        if url_cols:
            sample_urls = kw_df[url_cols[0]].dropna().head(50)
            from urllib.parse import urlparse
            domains = [urlparse(str(u)).netloc for u in sample_urls if u]
            domains = [d for d in domains if d]
            if domains:
                from collections import Counter
                detected_domain = Counter(domains).most_common(1)[0][0]

        domain_input = st.text_input(
            "Domain", value=detected_domain, key="brand_domain",
            help="Used to give the AI context for brand detection.",
        )

        current_terms = get_assumption(store, "brand_terms") or []
        terms_text = st.text_area(
            "Brand terms (one per line)",
            value="\n".join(current_terms),
            key="brand_terms_area",
            height=100,
            help="Add or edit brand terms. The AI can auto-detect them.",
        )

        ai_key = st.session_state.get(BIFROST_API_KEY)
        ai_model = st.session_state.get(BIFROST_MODEL, get_default_model())

        col_detect, col_save = st.columns(2)
        with col_detect:
            if st.button("Detect Brand Terms (AI)", key="brand_detect_btn", disabled=not ai_key):
                try:
                    from engine.ai_engine import detect_brand_terms, get_bifrost_client
                    client = get_bifrost_client(ai_key)
                    top_kws = kw_df.sort_values("volume", ascending=False)["keyword"].head(100).tolist()
                    result_dict, used_model = detect_brand_terms(client, domain_input, top_kws, ai_model)
                    detected = result_dict.get("brand_terms", [])
                    confidence = result_dict.get("confidence", 0)
                    reasoning = result_dict.get("reasoning", "")
                    st.session_state[DETECTED_BRAND_TERMS] = detected
                    st.success(
                        f"Detected {len(detected)} brand terms "
                        f"(confidence: {confidence:.0%}) via {used_model}.\n\n"
                        f"_{reasoning}_"
                    )
                except Exception as e:
                    st.error(f"Brand detection failed: {e}")
            elif not ai_key:
                st.caption("Add your Bi Frost API key in the AI Settings panel to enable auto-detection.")

        # Merge auto-detected with manual
        if DETECTED_BRAND_TERMS in st.session_state:
            existing_manual = [t.strip() for t in terms_text.split("\n") if t.strip()]
            merged = list(dict.fromkeys(existing_manual + st.session_state[DETECTED_BRAND_TERMS]))
            terms_text = "\n".join(merged)

        with col_save:
            if st.button("Save Brand Terms", key="brand_save_btn"):
                saved_terms = [t.strip() for t in terms_text.split("\n") if t.strip()]
                prov = "AI-detected" if DETECTED_BRAND_TERMS in st.session_state else "user-overridden"
                override_assumption(store, "brand_terms", saved_terms, prov)
                # Classify keywords
                updated_kw = classify_keywords_as_branded(
                    st.session_state[KW_DF], saved_terms
                )
                st.session_state[KW_DF] = updated_kw
                if KW_EXISTING in st.session_state:
                    st.session_state[KW_EXISTING] = classify_keywords_as_branded(
                        st.session_state[KW_EXISTING], saved_terms
                    )
                n_branded = updated_kw["is_branded"].sum()
                n_total = len(updated_kw)
                st.success(
                    f"Saved. {n_branded} branded / {n_total} total keywords "
                    f"({n_branded / n_total * 100:.1f}%)."
                )

        # View matched branded keywords
        _saved_kw = st.session_state.get(KW_DF)
        if _saved_kw is not None and "is_branded" in _saved_kw.columns:
            _branded_kws = _saved_kw[_saved_kw["is_branded"]][["keyword", "volume", "position"]].copy()
            _branded_kws = _branded_kws.sort_values("volume", ascending=False).reset_index(drop=True)
            if not _branded_kws.empty:
                with st.expander(f"View {len(_branded_kws)} branded keywords (broad match)", expanded=False):
                    st.caption(
                        "These keywords will be **excluded from forecasts** when "
                        "'Exclude branded keywords' is enabled. Edit the terms above and "
                        "re-save to adjust the match."
                    )
                    st.dataframe(
                        _branded_kws.rename(columns={"keyword": "Keyword", "volume": "Volume", "position": "Position"}),
                        use_container_width=True,
                        hide_index=True,
                        height=min(400, 36 + 35 * len(_branded_kws)),
                    )

    elif uploaded_semrush is not None:
        st.error("Could not parse the uploaded SEMrush file. Please check the format.")

# ── Roadmap Tab ───────────────────────────────────────────────────────────────
with tab_roadmap:
    st.markdown(
        "Upload your SEO roadmap to extract **per-focus-area effort levels**, "
        "**monthly hours**, and **content cadence** using AI — giving the forecast engines "
        "richer signal than a single effort-level scalar."
    )
    st.caption("Accepts xlsx or CSV files. AI extraction requires Bi Frost API access; falls back to legacy scalar detection if unavailable.")

    _ai_client = get_bifrost_client(st.session_state.get(BIFROST_API_KEY))
    _ai_model = st.session_state.get(BIFROST_MODEL, get_default_model())
    _ai_available = _ai_client is not None
    _roadmap_cache = st.session_state.setdefault(ROADMAP_AI_CACHE, {})

    uploaded_roadmap = st.file_uploader(
        "Upload roadmap file",
        type=["csv", "xlsx", "xls", "tsv"],
        key="roadmap_upload",
    )

    if uploaded_roadmap is not None:
        _raw_bytes = uploaded_roadmap.read()
        _ext = uploaded_roadmap.name.rsplit(".", 1)[-1] if "." in uploaded_roadmap.name else "csv"
        st.session_state[ROADMAP_RAW_BYTES] = _raw_bytes
        st.session_state[ROADMAP_FILE_EXT] = _ext

    _raw_bytes = st.session_state.get(ROADMAP_RAW_BYTES)

    if _raw_bytes is not None:
        if _ai_available:
            # ── AI extraction flow ─────────────────────────────────────────
            _ext = st.session_state.get(ROADMAP_FILE_EXT, "csv")

            col_ext, col_btn = st.columns([3, 1])
            with col_btn:
                _do_extract = st.button("Extract with AI", key="roadmap_ai_extract", type="primary")

            if _do_extract or ROADMAP_BUNDLE not in st.session_state:
                _ck = compute_cache_key(_raw_bytes, None, _ai_model)
                if not _do_extract and _ck in _roadmap_cache:
                    _bundle = _roadmap_cache[_ck]["bundle"]
                    _used_model = _roadmap_cache[_ck]["model"]
                    st.session_state[ROADMAP_BUNDLE] = _bundle
                    st.session_state[ROADMAP_CONTENT_PLAN] = _bundle.get("content_plan", [])
                    st.session_state[ROADMAP_USED_MODEL] = _used_model
                else:
                    with st.spinner("Ingesting roadmap…"):
                        try:
                            _fname = f"roadmap.{_ext}"
                            _bundle, _used_model = load_roadmap_v2(
                                _ai_client, _raw_bytes, _fname, model=_ai_model,
                            )
                            _roadmap_cache[_ck] = {"bundle": _bundle, "model": _used_model}
                            st.session_state[ROADMAP_BUNDLE] = _bundle
                            st.session_state[ROADMAP_CONTENT_PLAN] = _bundle.get("content_plan", [])
                            st.session_state[ROADMAP_USED_MODEL] = _used_model
                        except ValueError as _ve:
                            st.error("Roadmap parsed but failed validation:")
                            st.code(str(_ve), language="text")
                            st.warning("Fix the issues above, then re-upload the roadmap.")
                            try:
                                _legacy = load_roadmap(_raw_bytes)
                                if _legacy:
                                    run_detection(store, roadmap_data=_legacy)
                                    st.session_state[ROADMAP_DATA] = _legacy
                                    st.warning("Legacy extraction used — upload AI key for rich extraction.")
                            except Exception:
                                pass
                        except Exception as _e:
                            import traceback
                            st.error(f"Roadmap ingestion failed: {_e}")
                            with st.expander("Show error details"):
                                st.code(traceback.format_exc(), language="text")
                            try:
                                _legacy = load_roadmap(_raw_bytes)
                                if _legacy:
                                    run_detection(store, roadmap_data=_legacy)
                                    st.session_state[ROADMAP_DATA] = _legacy
                                    st.warning("Legacy extraction used — upload AI key for rich extraction.")
                            except Exception as _e2:
                                st.error(f"Legacy fallback also failed: {_e2}")

            _bundle = st.session_state.get(ROADMAP_BUNDLE)
            if _bundle:
                _ss = _bundle.get("source_summary", {})
                _used_model_label = st.session_state.get(ROADMAP_USED_MODEL, _ai_model)

                # Confidence banner
                _conf = _ss.get("parsing_confidence", 0.9)
                if _conf < 0.7:
                    st.warning(
                        f"Parsing confidence is low ({_conf:.0%}). Review the extraction carefully "
                        "before applying to assumptions."
                    )

                # KPI cards
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Tasks Detected", _ss.get("total_tasks_detected", "—"))
                k2.metric("Focus Areas", len(_ss.get("focus_areas_detected", [])))
                k3.metric("Timeline", f"{_ss.get('timeline_months_covered', '—')} months")
                k4.metric("Confidence", f"{_conf:.0%}")
                st.caption(f"Extracted via {_used_model_label}")

                # Recommendations
                _recs = _bundle.get("recommendations", [])
                if _recs:
                    st.subheader("Recommendations")
                    for _r in _recs:
                        _sev = _r.get("severity", "info")
                        _msg = _r.get("message", "")
                        if _sev == "warning":
                            st.warning(_msg)
                        else:
                            st.info(_msg)

                # Gaps
                _gaps = _bundle.get("gaps", [])
                if _gaps:
                    with st.expander(f"{len(_gaps)} gap(s) detected"):
                        for _g in _gaps:
                            st.markdown(f"- **{_g.get('focus_area', '?')}**: {_g.get('note', '')}")

                # Per-focus breakdown table
                st.subheader("Per-Focus Breakdown")
                _focus_rows = []
                for _fk in ("content", "technical", "on_page", "off_page", "local", "analytics", "strategy"):
                    _fd = _bundle.get("per_focus", {}).get(_fk, {})
                    _focus_rows.append({
                        "Focus Area": _fk.replace("_", " ").title(),
                        "Effort Level": _fd.get("effort_level", "—"),
                        "Monthly Hours": _fd.get("monthly_hours", 0.0),
                        "Cadence": _fd.get("cadence", 0),
                        "Tasks": _fd.get("task_count", 0),
                    })
                st.dataframe(pd.DataFrame(_focus_rows), use_container_width=True, hide_index=True)

                st.divider()
                st.subheader("Correct the Extraction")

                _nl_correction = st.text_area(
                    "Natural language correction",
                    placeholder="e.g., 'Technical audit is quarterly not bi-annual, and content production is 20 hours not 10'",
                    key="roadmap_nl_correction",
                    height=80,
                )
                _json_edit = st.text_area(
                    "JSON editor (edit directly, then click Re-extract or Apply)",
                    value=json.dumps(_bundle, indent=2),
                    key="roadmap_json_edit",
                    height=280,
                )

                # Token estimate for transparency
                _schema_str = json.dumps(ROADMAP_BUNDLE_SCHEMA)
                _est_tokens = estimate_extraction_tokens(
                    roadmap_md=str(_raw_bytes[:4000]),
                    correction_ctx=_nl_correction or "",
                    schema_str=_schema_str,
                )
                st.caption(f"AI call cost: ~{_est_tokens:,} tokens estimated")

                col_reext, col_apply = st.columns(2)
                with col_reext:
                    if st.button("Re-extract (AI)", key="roadmap_reextract"):
                        if _nl_correction.strip():
                            _ck2 = compute_cache_key(_raw_bytes, _nl_correction.strip(), _ai_model)
                            if _ck2 in _roadmap_cache:
                                _new_bundle = _roadmap_cache[_ck2]["bundle"]
                                _new_model = _roadmap_cache[_ck2]["model"]
                                st.session_state[ROADMAP_BUNDLE] = _new_bundle
                                st.session_state[ROADMAP_CONTENT_PLAN] = _new_bundle.get("content_plan", [])
                                st.session_state[ROADMAP_USED_MODEL] = _new_model
                                st.rerun()
                            else:
                                with st.spinner("Re-running ingestion with correction…"):
                                    try:
                                        _fname = f"roadmap.{_ext}"
                                        _new_bundle, _new_model = load_roadmap_v2(
                                            _ai_client, _raw_bytes, _fname,
                                            nl_correction=_nl_correction.strip(),
                                            previous_bundle=_bundle,
                                            model=_ai_model,
                                        )
                                        _roadmap_cache[_ck2] = {"bundle": _new_bundle, "model": _new_model}
                                        st.session_state[ROADMAP_BUNDLE] = _new_bundle
                                        st.session_state[ROADMAP_CONTENT_PLAN] = _new_bundle.get("content_plan", [])
                                        st.session_state[ROADMAP_USED_MODEL] = _new_model
                                        st.rerun()
                                    except Exception as _e:
                                        st.error(f"Re-extraction failed: {_e}")
                        else:
                            # No NL — try to parse JSON editor
                            try:
                                _edited = json.loads(_json_edit)
                                st.session_state[ROADMAP_BUNDLE] = _edited
                                st.rerun()
                            except json.JSONDecodeError as _je:
                                st.error(f"JSON parse error: {_je}. Fix the JSON or enter a natural-language correction.")

                with col_apply:
                    if st.button("Apply to assumptions", key="roadmap_apply", type="primary"):
                        try:
                            _to_apply = json.loads(_json_edit)
                        except json.JSONDecodeError:
                            _to_apply = _bundle
                        run_detection(store, roadmap_data=_to_apply)
                        st.session_state[ROADMAP_DATA] = _to_apply
                        st.session_state[ROADMAP_BUNDLE] = _to_apply
                        st.session_state[ROADMAP_CONTENT_PLAN] = _to_apply.get("content_plan", [])
                        st.success("Roadmap assumptions applied.")

        else:
            # ── No AI key — legacy fallback ────────────────────────────────
            st.warning(
                "Bi Frost API key not configured — using legacy scalar extraction. "
                "Set up your API key in the sidebar to enable rich per-focus-area extraction."
            )
            try:
                _legacy = load_roadmap(_raw_bytes)
                if _legacy:
                    run_detection(store, roadmap_data=_legacy)
                    st.session_state[ROADMAP_DATA] = _legacy
                    _dkeys = [k for k in ("content_cadence", "effort_level", "maintenance_coverage") if k in _legacy]
                    st.success(f"Roadmap loaded (legacy). Detected: {', '.join(_dkeys)}.")
                    _disp = [{"Parameter": k.replace("_", " ").title(), "Value": _legacy[k]} for k in _dkeys]
                    st.table(pd.DataFrame(_disp))
                else:
                    st.warning("Roadmap file parsed but no recognisable parameters found. Check column names.")
            except Exception as _e:
                st.error(f"Could not parse roadmap: {_e}")

    elif ROADMAP_BUNDLE in st.session_state:
        _prev = st.session_state[ROADMAP_BUNDLE].get("source_summary", {})
        st.info(
            f"Roadmap from previous upload: {_prev.get('total_tasks_detected', '—')} tasks, "
            f"{len(_prev.get('focus_areas_detected', []))} focus areas, "
            f"confidence {_prev.get('parsing_confidence', 0):.0%}. "
            "Upload a new file to re-extract."
        )
    elif ROADMAP_DATA in st.session_state:
        _rd = st.session_state[ROADMAP_DATA]
        st.info(
            f"Roadmap from previous upload: cadence={_rd.get('content_cadence', '—')}, "
            f"effort={_rd.get('effort_level', '—')}, "
            f"maintenance={_rd.get('maintenance_coverage', '—')}"
        )

# ── Seasonality Tuning ────────────────────────────────────────────────────────
st.divider()
st.subheader("Seasonality Tuning")
st.caption(
    "Monthly modifiers applied to all forecast streams. "
    "Auto-detected from GA4 when ≥12 months available; "
    "falls back to AU retail defaults otherwise."
)

seasonality = st.session_state.get(SEASONALITY, DEFAULT_SEASONALITY)
learned_seasonality = st.session_state.get(LEARNED_SEASONALITY)
source = get_assumption(store, "seasonality_source")
blend_weight = get_assumption(store, "seasonality_blend_weight")

if source == "learned":
    st.success(f"Seasonality fully learned from GA4 data (blend weight: {blend_weight:.0%}).")
elif source == "blended":
    st.info(f"Seasonality blended: {blend_weight:.0%} GA4 data + {1-blend_weight:.0%} AU retail defaults.")
else:
    st.info("Seasonality using AU retail defaults (upload ≥12 months of GA4 data to learn from your data).")

if learned_seasonality:
    with st.expander("Compare learned vs. AU retail defaults"):
        import plotly.graph_objects as go_s
        months_labels = [seasonality[m]["label"].split(" ")[0] for m in range(1, 13)]
        learned_vals = [learned_seasonality[m]["traffic_mod"] * 100 for m in range(1, 13)]
        default_vals = [DEFAULT_SEASONALITY[m]["traffic_mod"] * 100 for m in range(1, 13)]
        fig_s = go_s.Figure()
        fig_s.add_trace(go_s.Bar(name="Learned from GA4", x=months_labels, y=learned_vals, marker_color="#2563EB"))
        fig_s.add_trace(go_s.Bar(name="AU Retail Default", x=months_labels, y=default_vals, marker_color="#9CA3AF"))
        fig_s = _apply_layout(fig_s, "Seasonality: Learned vs Default", "Month", "Traffic Modifier (%)")
        st.plotly_chart(fig_s, use_container_width=True)

# ── Data Status Footer ────────────────────────────────────────────────────────
st.divider()
st.subheader("Data Status")
col1, col2, col3 = st.columns(3)
with col1:
    if GA4_DF in st.session_state:
        ga4 = st.session_state[GA4_DF]
        st.success(f"GA4: {len(ga4)} months loaded ({ga4['date'].min().strftime('%b %Y')} – {ga4['date'].max().strftime('%b %Y')})")
    else:
        st.info("GA4: Not loaded")
with col2:
    if KW_DF in st.session_state:
        kw = st.session_state[KW_DF]
        st.success(f"Keywords: {len(kw)} loaded ({len(st.session_state.get('kw_existing', []))} ranking)")
    else:
        st.info("Keywords: Not loaded")
with col3:
    if ROADMAP_BUNDLE in st.session_state:
        _rb_ss = st.session_state[ROADMAP_BUNDLE].get("source_summary", {})
        st.success(f"Roadmap: {_rb_ss.get('total_tasks_detected', '?')} tasks (AI extracted)")
    elif ROADMAP_DATA in st.session_state:
        st.success("Roadmap: loaded (legacy)")
    else:
        st.info("Roadmap: Not loaded")

# ── Assumptions Panel ─────────────────────────────────────────────────────────
st.divider()
render_assumptions_banner(store)
render_assumptions_panel(store)
