import json
import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from utils.ga4_loader import load_ga4_organic
from utils.keyword_loader import load_keyword_portfolio, split_existing_vs_new
from utils.roadmap_loader import load_roadmap
from utils.chart_builder import _apply_layout
from utils.sidebar import render_ai_settings
from utils.assumptions_panel import render_assumptions_panel, render_assumptions_banner
from engine.assumptions import initialise_assumptions, run_detection, override_assumption, get_assumption, get_provenance
from engine.seasonality_engine import (
    learn_seasonality_from_ga4, blend_learned_and_default_seasonality, DEFAULT_SEASONALITY,
)
from engine.brand_engine import classify_keywords_as_branded
from engine.ai_engine import get_bifrost_client
from engine.roadmap_ai_engine import (
    extract_roadmap_with_ai, extract_roadmap_full_ai, estimate_extraction_tokens,
    ROADMAP_BUNDLE_SCHEMA, detect_roadmap_format, load_roadmap_v2,
)

st.header("Data Upload")
st.caption("Upload GA4 organic traffic, SEMrush keyword exports, and an optional roadmap file. Data flows to all downstream pages.")

render_ai_settings()

# ── Assumptions store ────────────────────────────────────────────────────────
store = st.session_state.setdefault("assumptions", {})
initialise_assumptions(store)

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
        st.session_state["ga4_df"] = ga4_df
        run_detection(store, ga4_df=ga4_df)

        # ── Seasonality Detection ─────────────────────────────────────
        n_months = len(ga4_df)
        learned = learn_seasonality_from_ga4(ga4_df)
        if learned is not None:
            if n_months >= 24:
                blend_weight = 1.0
                source = "learned"
            elif n_months >= 12:
                blend_weight = 0.5
                source = "blended"
            else:
                blend_weight = 0.0
                source = "defaulted"

            blended = blend_learned_and_default_seasonality(learned, DEFAULT_SEASONALITY, blend_weight)
            st.session_state["seasonality"] = blended
            st.session_state["learned_seasonality"] = learned
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

        st.session_state["kw_df"] = kw_df
        st.session_state["kw_existing"] = existing_df
        st.session_state["kw_new"] = new_df

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

        ai_key = st.session_state.get("bifrost_api_key")
        ai_model = st.session_state.get("ai_model", "openai/gpt-4o-mini")

        col_detect, col_save = st.columns(2)
        with col_detect:
            if st.button("Detect Brand Terms (AI)", key="brand_detect_btn", disabled=not ai_key):
                try:
                    from engine.ai_engine import get_bifrost_client, detect_brand_terms
                    client = get_bifrost_client(ai_key)
                    top_kws = kw_df.sort_values("volume", ascending=False)["keyword"].head(100).tolist()
                    result_dict, used_model = detect_brand_terms(client, domain_input, top_kws, ai_model)
                    detected = result_dict.get("brand_terms", [])
                    confidence = result_dict.get("confidence", 0)
                    reasoning = result_dict.get("reasoning", "")
                    st.session_state["detected_brand_terms"] = detected
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
        if "detected_brand_terms" in st.session_state:
            existing_manual = [t.strip() for t in terms_text.split("\n") if t.strip()]
            merged = list(dict.fromkeys(existing_manual + st.session_state["detected_brand_terms"]))
            terms_text = "\n".join(merged)

        with col_save:
            if st.button("Save Brand Terms", key="brand_save_btn"):
                saved_terms = [t.strip() for t in terms_text.split("\n") if t.strip()]
                prov = "AI-detected" if "detected_brand_terms" in st.session_state else "user-overridden"
                override_assumption(store, "brand_terms", saved_terms, prov)
                # Classify keywords
                updated_kw = classify_keywords_as_branded(
                    st.session_state["kw_df"], saved_terms
                )
                st.session_state["kw_df"] = updated_kw
                if "kw_existing" in st.session_state:
                    st.session_state["kw_existing"] = classify_keywords_as_branded(
                        st.session_state["kw_existing"], saved_terms
                    )
                n_branded = updated_kw["is_branded"].sum()
                n_total = len(updated_kw)
                st.success(
                    f"Saved. {n_branded} branded / {n_total} total keywords "
                    f"({n_branded / n_total * 100:.1f}%)."
                )

    elif uploaded_semrush is not None:
        st.error("Could not parse the uploaded SEMrush file. Please check the format.")

# ── Roadmap Tab ───────────────────────────────────────────────────────────────
with tab_roadmap:
    st.markdown(
        "Upload your SEO roadmap to extract **per-focus-area effort levels**, "
        "**monthly hours**, and **content cadence** — giving the forecast engines "
        "richer signal than a single effort-level scalar."
    )
    st.caption(
        "Pattern native multi-sheet SOW workbooks are parsed deterministically (no AI cost). "
        "Generic files use AI extraction via Bi Frost."
    )

    _ai_client = get_bifrost_client(st.session_state.get("bifrost_api_key"))
    _ai_model = st.session_state.get("ai_model", "openai/gpt-4o-mini")
    _ai_available = _ai_client is not None
    _roadmap_cache = st.session_state.setdefault("roadmap_ai_cache", {})

    uploaded_roadmap = st.file_uploader(
        "Upload roadmap file",
        type=["csv", "xlsx", "xls", "tsv"],
        key="roadmap_upload",
    )

    if uploaded_roadmap is not None:
        _raw_bytes = uploaded_roadmap.read()
        _ext = uploaded_roadmap.name.rsplit(".", 1)[-1] if "." in uploaded_roadmap.name else "csv"
        _filename = uploaded_roadmap.name
        st.session_state["roadmap_raw_bytes"] = _raw_bytes
        st.session_state["roadmap_file_ext"] = _ext
        st.session_state["roadmap_filename"] = _filename

    _raw_bytes = st.session_state.get("roadmap_raw_bytes")

    if _raw_bytes is not None:
        _ext = st.session_state.get("roadmap_file_ext", "csv")
        _filename = st.session_state.get("roadmap_filename", f"roadmap.{_ext}")

        # ── Format detection banner ─────────────────────────────────────────
        _fmt = detect_roadmap_format(_raw_bytes, _ext)
        _fmt_labels = {
            "pattern_native": ("Pattern Native SOW", "success"),
            "task_table": ("Task Table", "info"),
            "param_table": ("Parameter Table", "info"),
            "unknown": ("Unknown Format", "warning"),
        }
        _fmt_label, _fmt_sev = _fmt_labels.get(_fmt, ("Unknown", "warning"))

        col_fmt, col_btn = st.columns([3, 1])
        with col_fmt:
            if _fmt_sev == "success":
                st.success(f"Format detected: **{_fmt_label}** — deterministic extraction, no AI cost.")
            elif _fmt_sev == "info":
                st.info(f"Format detected: **{_fmt_label}**")
            else:
                st.warning(f"Format: **{_fmt_label}** — full AI extraction will be used.")

        _is_native = _fmt == "pattern_native"
        _needs_ai = not _is_native

        with col_btn:
            if _is_native:
                _do_extract = st.button("Parse Roadmap", key="roadmap_ai_extract", type="primary")
            else:
                _do_extract = st.button(
                    "Extract with AI" if _ai_available else "Extract (legacy)",
                    key="roadmap_ai_extract",
                    type="primary",
                )

        if _do_extract or "roadmap_bundle" not in st.session_state:
            with st.spinner("Extracting roadmap structure…"):
                try:
                    if _is_native:
                        from engine.roadmap_ai_engine import parse_pattern_native, enrich_bundle_with_ai
                        _bundle, _raw_task_descs = parse_pattern_native(_raw_bytes)
                        _used_model = "deterministic"
                        if _ai_available:
                            try:
                                _bundle, _used_model = enrich_bundle_with_ai(
                                    _ai_client, _bundle, _raw_task_descs, _ai_model
                                )
                            except Exception:
                                pass
                    elif _ai_available:
                        _bundle, _used_model = extract_roadmap_full_ai(
                            _ai_client,
                            _raw_bytes,
                            _ext,
                            model=_ai_model,
                            cache=_roadmap_cache,
                        )
                    else:
                        _legacy = load_roadmap(_raw_bytes)
                        if _legacy:
                            run_detection(store, roadmap_data=_legacy)
                            st.session_state["roadmap_data"] = _legacy
                            _dkeys = [k for k in ("content_cadence", "effort_level", "maintenance_coverage") if k in _legacy]
                            st.success(f"Roadmap loaded (legacy). Detected: {', '.join(_dkeys)}.")
                            _disp = [{"Parameter": k.replace("_", " ").title(), "Value": _legacy[k]} for k in _dkeys]
                            st.table(pd.DataFrame(_disp))
                        else:
                            st.warning("No recognisable parameters found in roadmap file.")
                        _bundle = None
                        _used_model = "legacy"

                    if _bundle:
                        st.session_state["roadmap_bundle"] = _bundle
                        st.session_state["roadmap_used_model"] = _used_model
                        # Store content plan for New Content page
                        if _bundle.get("content_plan"):
                            st.session_state["roadmap_content_plan"] = _bundle["content_plan"]

                except Exception as _e:
                    st.error(f"Extraction failed: {_e}. Falling back to legacy loader.")
                    try:
                        _legacy = load_roadmap(_raw_bytes)
                        if _legacy:
                            run_detection(store, roadmap_data=_legacy)
                            st.session_state["roadmap_data"] = _legacy
                            st.warning("Legacy extraction used.")
                    except Exception as _e2:
                        st.error(f"Legacy fallback also failed: {_e2}")

        _bundle = st.session_state.get("roadmap_bundle")
        if _bundle:
            _ss = _bundle.get("source_summary", {})
            _used_model_label = st.session_state.get("roadmap_used_model", _ai_model)
            _schema_ver = _bundle.get("schema_version", "1.0")

            # Confidence banner
            _conf = _ss.get("parsing_confidence", 0.9)
            if _conf < 0.7:
                st.warning(
                    f"Parsing confidence is low ({_conf:.0%}). Review the extraction carefully "
                    "before applying to assumptions."
                )

            # Client metadata (v2 bundles)
            _meta = _bundle.get("client_metadata", {})
            if _meta.get("client_name"):
                _client_cols = st.columns(3)
                _client_cols[0].metric("Client", _meta.get("client_name", "—"))
                _client_cols[1].metric("Industry", _meta.get("industry", "—"))
                _retainer = _meta.get("retainer_aud_monthly", 0)
                if _retainer:
                    _client_cols[2].metric("Monthly Retainer", f"${_retainer:,.0f}")

            # KPI cards
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Tasks Detected", _ss.get("total_tasks_detected", "—"))
            k2.metric("Focus Areas", len(_ss.get("focus_areas_detected", [])))
            k3.metric("Timeline", f"{_ss.get('timeline_months_covered', '—')} months")
            k4.metric("Confidence", f"{_conf:.0%}")
            _extract_label = "deterministic" if _used_model_label == "deterministic" else f"via {_used_model_label}"
            st.caption(f"Extracted {_extract_label} · Schema v{_schema_ver}")

            # Recommendations
            _recs = _bundle.get("recommendations", [])
            if _recs:
                st.subheader("Recommendations")
                for _r in _recs:
                    _sev = _r.get("severity", "info")
                    _msg = _r.get("message", "")
                    if _sev == "critical":
                        st.error(_msg)
                    elif _sev == "warning":
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

            # Content plan preview (v2 bundles)
            _content_plan = _bundle.get("content_plan", [])
            if _content_plan:
                st.subheader("Content Plan")
                st.caption(f"{len(_content_plan)} URL(s) in content plan — first 20 shown.")
                _cp_preview = _content_plan[:20]
                st.dataframe(pd.DataFrame(_cp_preview), use_container_width=True, hide_index=True)
                st.info(
                    "Content plan will be passed to the New Content Forecast page — "
                    "keywords matching these URLs get roadmap-specific publish months."
                )

            # Strategy duration + industry override widgets
            st.divider()
            st.subheader("Strategy Overrides")
            _ov_cols = st.columns(3)
            with _ov_cols[0]:
                _timeline_val = _bundle.get("timeline", {}).get("months_covered", 12)
                _timeline_override = st.number_input(
                    "Strategy duration (months)",
                    min_value=1, max_value=36,
                    value=int(_timeline_val),
                    key="roadmap_timeline_override",
                )
            with _ov_cols[1]:
                _restart_val = _bundle.get("timeline", {}).get("strategy_restart_month")
                _restart_override = st.number_input(
                    "Strategy restart month",
                    min_value=1, max_value=36,
                    value=int(_restart_val) if _restart_val else 1,
                    key="roadmap_restart_override",
                )
            with _ov_cols[2]:
                _industry_val = _bundle.get("client_metadata", {}).get("industry", "Unknown")
                _industry_override = st.text_input(
                    "Industry (for seasonality prior)",
                    value=_industry_val,
                    key="roadmap_industry_override",
                )

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

            # Token estimate for transparency (AI path only)
            if _needs_ai and _ai_available:
                _schema_str = json.dumps(ROADMAP_BUNDLE_SCHEMA)
                _est_tokens = estimate_extraction_tokens(
                    roadmap_md=str(_raw_bytes[:4000]),
                    correction_ctx=_nl_correction or "",
                    schema_str=_schema_str,
                )
                st.caption(f"AI call cost: ~{_est_tokens:,} tokens estimated")

            col_reext, col_apply = st.columns(2)
            with col_reext:
                if st.button("Re-extract", key="roadmap_reextract"):
                    if _nl_correction.strip():
                        with st.spinner("Re-running extraction with correction…"):
                            try:
                                if _is_native and _ai_available:
                                    from engine.roadmap_ai_engine import parse_pattern_native, enrich_bundle_with_ai
                                    _nb2, _rdescs2 = parse_pattern_native(_raw_bytes)
                                    _new_bundle, _new_model = enrich_bundle_with_ai(
                                        _ai_client, _nb2, _rdescs2, _ai_model
                                    )
                                else:
                                    _new_bundle, _new_model = extract_roadmap_full_ai(
                                        _ai_client, _raw_bytes, _ext,
                                        nl_correction=_nl_correction.strip(),
                                        previous_extraction=_bundle,
                                        model=_ai_model,
                                        cache=_roadmap_cache,
                                    )
                                st.session_state["roadmap_bundle"] = _new_bundle
                                st.session_state["roadmap_used_model"] = _new_model
                                if _new_bundle.get("content_plan"):
                                    st.session_state["roadmap_content_plan"] = _new_bundle["content_plan"]
                                st.rerun()
                            except Exception as _e:
                                st.error(f"Re-extraction failed: {_e}")
                    else:
                        try:
                            _edited = json.loads(_json_edit)
                            st.session_state["roadmap_bundle"] = _edited
                            if _edited.get("content_plan"):
                                st.session_state["roadmap_content_plan"] = _edited["content_plan"]
                            st.rerun()
                        except json.JSONDecodeError as _je:
                            st.error(f"JSON parse error: {_je}. Fix the JSON or enter a natural-language correction.")

            with col_apply:
                if st.button("Apply to assumptions", key="roadmap_apply", type="primary"):
                    try:
                        _to_apply = json.loads(_json_edit)
                    except json.JSONDecodeError:
                        _to_apply = _bundle

                    # Apply strategy overrides from widgets before detection
                    if "timeline" not in _to_apply:
                        _to_apply["timeline"] = {}
                    _to_apply["timeline"]["months_covered"] = _timeline_override
                    _to_apply["timeline"]["strategy_restart_month"] = _restart_override
                    if "client_metadata" not in _to_apply:
                        _to_apply["client_metadata"] = {}
                    _to_apply["client_metadata"]["industry"] = _industry_override

                    run_detection(store, roadmap_data=_to_apply)
                    st.session_state["roadmap_data"] = _to_apply
                    if _to_apply.get("content_plan"):
                        st.session_state["roadmap_content_plan"] = _to_apply["content_plan"]

                    # Apply industry seasonality bias if industry is set
                    _ind = _industry_override.strip()
                    if _ind and _ind.lower() not in ("unknown", ""):
                        from engine.seasonality_engine import apply_industry_bias
                        _current_season = st.session_state.get("seasonality", DEFAULT_SEASONALITY)
                        _biased = apply_industry_bias(_current_season, _ind, bias_weight=0.3)
                        st.session_state["seasonality"] = _biased
                        st.info(f"Seasonality adjusted for industry: {_ind}")

                    st.success("Roadmap assumptions applied.")

    elif "roadmap_bundle" in st.session_state:
        _prev = st.session_state["roadmap_bundle"].get("source_summary", {})
        st.info(
            f"Roadmap from previous upload: {_prev.get('total_tasks_detected', '—')} tasks, "
            f"{len(_prev.get('focus_areas_detected', []))} focus areas, "
            f"confidence {_prev.get('parsing_confidence', 0):.0%}. "
            "Upload a new file to re-extract."
        )
    elif "roadmap_data" in st.session_state:
        _rd = st.session_state["roadmap_data"]
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

seasonality = st.session_state.get("seasonality", DEFAULT_SEASONALITY)
learned_seasonality = st.session_state.get("learned_seasonality")
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
    if "ga4_df" in st.session_state:
        ga4 = st.session_state["ga4_df"]
        st.success(f"GA4: {len(ga4)} months loaded ({ga4['date'].min().strftime('%b %Y')} – {ga4['date'].max().strftime('%b %Y')})")
    else:
        st.info("GA4: Not loaded")
with col2:
    if "kw_df" in st.session_state:
        kw = st.session_state["kw_df"]
        st.success(f"Keywords: {len(kw)} loaded ({len(st.session_state.get('kw_existing', []))} ranking)")
    else:
        st.info("Keywords: Not loaded")
with col3:
    if "roadmap_bundle" in st.session_state:
        _rb_ss = st.session_state["roadmap_bundle"].get("source_summary", {})
        _rb_fmt = st.session_state["roadmap_bundle"].get("source_format", "ai_extracted")
        _rb_fmt_label = "Pattern Native" if _rb_fmt == "pattern_native" else "AI extracted"
        _cp_count = len(st.session_state.get("roadmap_content_plan", []))
        _cp_str = f" + {_cp_count} content URLs" if _cp_count else ""
        st.success(f"Roadmap: {_rb_ss.get('total_tasks_detected', '?')} tasks ({_rb_fmt_label}){_cp_str}")
    elif "roadmap_data" in st.session_state:
        st.success("Roadmap: loaded (legacy)")
    else:
        st.info("Roadmap: Not loaded")

# ── Assumptions Panel ─────────────────────────────────────────────────────────
st.divider()
render_assumptions_banner(store)
render_assumptions_panel(store)
