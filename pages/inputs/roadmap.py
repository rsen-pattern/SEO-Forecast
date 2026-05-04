import json

import pandas as pd
import streamlit as st

from engine.ai_engine import get_bifrost_client, get_default_model
from engine.assumptions import run_detection
from engine.roadmap_ai_engine import (
    ROADMAP_BUNDLE_SCHEMA,
    compute_cache_key,
    estimate_extraction_tokens,
    load_roadmap_v2,
)
from utils.assumptions_panel import render_assumptions_banner
from utils.page_base import setup_page
from utils.roadmap_loader import load_roadmap
from utils.session import (
    BIFROST_API_KEY,
    BIFROST_MODEL,
    GA4_DF,
    KW_DF,
    KW_EXISTING,
    ROADMAP_AI_CACHE,
    ROADMAP_BUNDLE,
    ROADMAP_BUNDLES,
    ROADMAP_CONTENT_PLAN,
    ROADMAP_CONTENT_PLANS,
    ROADMAP_DATA,
    ROADMAP_FILE_EXT,
    ROADMAP_RAW_BYTES,
    ROADMAP_USED_MODEL,
)

store = setup_page(
    "Roadmap",
    "Upload your SEO roadmap to extract per-focus effort levels, monthly hours, and content cadence.",
    show_assumptions_banner=False,
)

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
                        st.warning(
                            "This usually means the file structure differs from expected. "
                            "Try re-uploading after correcting the highlighted rows, "
                            "or fall back to the legacy loader below."
                        )
                        try:
                            _legacy = load_roadmap(_raw_bytes)
                            if _legacy:
                                run_detection(store, roadmap_data=_legacy)
                                st.session_state[ROADMAP_DATA] = _legacy
                                st.info("Legacy fallback succeeded with reduced fidelity (3 scalars only).")
                        except Exception as _e2:
                            st.error(f"Legacy fallback also failed: {_e2}")
                    except Exception as _e:
                        import traceback
                        st.error(f"Roadmap ingestion failed: {_e}")
                        with st.expander("Show error details"):
                            st.code(traceback.format_exc(), language="text")
                        st.warning("Falling back to legacy loader — extraction will be limited to three scalars.")
                        try:
                            _legacy = load_roadmap(_raw_bytes)
                            if _legacy:
                                run_detection(store, roadmap_data=_legacy)
                                st.session_state[ROADMAP_DATA] = _legacy
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

            # ── Strategy at a glance ─────────────────────────────────
            _strategy_summary = _bundle.get("strategy_summary", "")
            _primary_domain = _bundle.get("primary_domain", "")
            _loc_domains = _bundle.get("localisation_domains", [])
            _client_name = _bundle.get("client_metadata", {}).get("client_name", "")

            if _strategy_summary or _primary_domain:
                with st.container(border=True):
                    st.markdown("**Strategy at a Glance**")
                    if _client_name:
                        st.caption(f"Client: {_client_name}")
                    if _strategy_summary:
                        st.markdown(_strategy_summary)
                    _domain_cols = st.columns(2)
                    with _domain_cols[0]:
                        if _primary_domain:
                            st.markdown(f"**Primary domain:** `{_primary_domain}`")
                    with _domain_cols[1]:
                        if _loc_domains:
                            _loc_list = ", ".join(f"`{d}`" for d in _loc_domains)
                            st.markdown(f"**Localisation:** {_loc_list}")

            # ── Validation warnings (tiered — not errors) ───────────
            _warnings = _bundle.get("validation_warnings", [])
            if _warnings:
                with st.expander(f"⚠ {len(_warnings)} data-quality warning(s)", expanded=False):
                    for _w in _warnings:
                        st.markdown(f"- {_w}")

            # KPI cards
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Items Detected", _ss.get("total_tasks_detected", "—"))
            _launches = _ss.get("content_launches_detected", 0)
            if _launches:
                k1.caption(f"({_launches} content launches)")
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

# ── Per-Scenario Roadmap Overrides (optional) ─────────────────────────────────
st.divider()
with st.expander("Per-Scenario Roadmap Overrides (optional)", expanded=False):
    st.caption(
        "Upload a separate roadmap file for Conservative, Moderate, and/or Aggressive scenarios. "
        "Leave a slot empty to inherit from the Primary Roadmap above. "
        "When all three are provided, each scenario's preset is pre-filled directly from its own bundle."
    )

    _scenario_names = ("Conservative", "Moderate", "Aggressive")
    _per_bundles: dict = st.session_state.get(ROADMAP_BUNDLES, {})
    _per_plans: dict = st.session_state.get(ROADMAP_CONTENT_PLANS, {})

    _scen_cols = st.columns(3)
    for _col, _sname in zip(_scen_cols, _scenario_names, strict=True):
        with _col:
            st.markdown(f"**{_sname}**")
            _existing = _per_bundles.get(_sname)
            if _existing:
                _ss2 = _existing.get("source_summary", {})
                st.caption(
                    f"✅ {_ss2.get('total_tasks_detected', '?')} tasks, "
                    f"{len(_ss2.get('focus_areas_detected', []))} focus areas"
                )
            else:
                st.caption("Inheriting from Primary Roadmap")

            _up = st.file_uploader(
                f"Roadmap for {_sname}",
                type=["csv", "xlsx", "xls", "tsv"],
                key=f"roadmap_upload_{_sname.lower()}",
                label_visibility="collapsed",
            )

            if _up is not None:
                _rb = _up.read()
                _re = _up.name.rsplit(".", 1)[-1] if "." in _up.name else "csv"
                _ck_s = compute_cache_key(_rb, None, _ai_model)
                if _ck_s in _roadmap_cache:
                    _sb = _roadmap_cache[_ck_s]["bundle"]
                else:
                    if _ai_available:
                        with st.spinner(f"Extracting {_sname} roadmap…"):
                            try:
                                _sb, _sm = load_roadmap_v2(
                                    _ai_client, _rb, f"roadmap.{_re}", model=_ai_model,
                                )
                                _roadmap_cache[_ck_s] = {"bundle": _sb, "model": _sm}
                            except Exception as _se:
                                st.error(f"Extraction failed: {_se}")
                                _sb = None
                    else:
                        st.warning("Bi Frost not available — cannot extract this roadmap.")
                        _sb = None

                if _sb is not None:
                    _per_bundles[_sname] = _sb
                    _per_plans[_sname] = _sb.get("content_plan", [])
                    st.session_state[ROADMAP_BUNDLES] = _per_bundles
                    st.session_state[ROADMAP_CONTENT_PLANS] = _per_plans
                    # Moderate bundle drives the assumptions store (primary)
                    if _sname == "Moderate":
                        run_detection(store, roadmap_data=_sb)
                    st.rerun()

            if _existing and st.button(f"Clear {_sname}", key=f"roadmap_clear_{_sname.lower()}"):
                _per_bundles.pop(_sname, None)
                _per_plans.pop(_sname, None)
                st.session_state[ROADMAP_BUNDLES] = _per_bundles
                st.session_state[ROADMAP_CONTENT_PLANS] = _per_plans
                st.rerun()

    _loaded_count = sum(1 for s in _scenario_names if s in _per_bundles)
    if _loaded_count == 3:
        st.success("✓ All three scenario roadmaps loaded — Strategy will use per-scenario presets and content plans.")
    elif _loaded_count > 0:
        _missing = [s for s in _scenario_names if s not in _per_bundles]
        st.info(
            f"{_loaded_count}/3 scenario roadmaps loaded. "
            f"**{', '.join(_missing)}** will inherit from the Primary Roadmap."
        )

    if _loaded_count > 0 and st.button("Clear all per-scenario roadmaps", key="roadmap_clear_all_per_scenario"):
        st.session_state.pop(ROADMAP_BUNDLES, None)
        st.session_state.pop(ROADMAP_CONTENT_PLANS, None)
        st.rerun()

# ── Data Status ───────────────────────────────────────────────────────────────
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
        st.success(f"Keywords: {len(kw)} loaded ({len(st.session_state.get(KW_EXISTING, []))} ranking)")
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

# ── Assumptions Banner ────────────────────────────────────────────────────────
st.divider()
render_assumptions_banner(store)
