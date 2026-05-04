"""Centralised registry of Streamlit session-state keys.

All pages should import constants from this module instead of using
string literals. This gives IDEs autocomplete and catches typos at
edit time.
"""
from __future__ import annotations

from typing import TypedDict, cast

import pandas as pd

# ── Data keys ─────────────────────────────────────────────────────────
GA4_DF = "ga4_df"
KW_DF = "kw_df"
KW_EXISTING = "kw_existing"
KW_NEW = "kw_new"

# ── Forecast result keys ──────────────────────────────────────────────
POS_RESULT = "pos_result"
NC_RESULT = "nc_result"              # was: kw_results
HIST_RESULTS = "hist_results"
COMB_RESULTS = "comb_results"
CONTENT_ROADMAP = "content_roadmap"

# ── Assumptions & seasonality ─────────────────────────────────────────
ASSUMPTIONS = "assumptions"
SEASONALITY = "seasonality"
LEARNED_SEASONALITY = "learned_seasonality"

# ── Roadmap ingestion ─────────────────────────────────────────────────
ROADMAP_BUNDLE = "roadmap_bundle"
ROADMAP_DATA = "roadmap_data"
ROADMAP_CONTENT_PLAN = "roadmap_content_plan"
ROADMAP_RAW_BYTES = "roadmap_raw_bytes"
ROADMAP_FILE_EXT = "roadmap_file_ext"
ROADMAP_AI_CACHE = "roadmap_ai_cache"
ROADMAP_USED_MODEL = "roadmap_used_model"

# Per-scenario roadmap overrides (dict keyed by scenario name)
ROADMAP_BUNDLES = "roadmap_bundles"           # {"Conservative": bundle, ...}
ROADMAP_CONTENT_PLANS = "roadmap_content_plans"  # {"Conservative": plan, ...}

# ── Historical forecast ───────────────────────────────────────────────
HIST_N_MONTHS = "hist_n_months"

# ── Strategy / scenarios ─────────────────────────────────────────────
SCENARIO_PRESETS = "scenario_presets"
SCENARIO_RESULTS = "scenario_results"
SCENARIO_PRESETS_EDITED = "scenario_presets_edited"

# ── Brand ─────────────────────────────────────────────────────────────
DETECTED_BRAND_TERMS = "detected_brand_terms"

# ── AI / Bi Frost ─────────────────────────────────────────────────────
BIFROST_API_KEY = "bifrost_api_key"
BIFROST_MODEL = "bifrost_model"       # canonical; `ai_model` was a stale alias
SESSION_COST_AUD = "session_cost_aud"


class AppState(TypedDict, total=False):
    """Type hint for st.session_state. Use via `cast(AppState, st.session_state)`."""
    ga4_df: pd.DataFrame
    kw_df: pd.DataFrame
    kw_existing: pd.DataFrame
    kw_new: pd.DataFrame
    pos_result: dict
    nc_result: dict
    hist_results: dict
    comb_results: dict
    content_roadmap: list
    assumptions: dict
    seasonality: dict
    learned_seasonality: dict
    roadmap_bundle: dict
    roadmap_data: dict
    roadmap_content_plan: list
    roadmap_raw_bytes: bytes
    roadmap_file_ext: str
    roadmap_ai_cache: dict
    roadmap_used_model: str
    roadmap_bundles: dict
    roadmap_content_plans: dict
    hist_n_months: int
    scenario_presets: dict
    scenario_results: dict
    scenario_presets_edited: dict
    detected_brand_terms: list
    bifrost_api_key: str
    bifrost_model: str
    session_cost_aud: float


def state() -> AppState:
    """Type-cast wrapper around st.session_state for IDE autocomplete.

    Usage:
        from utils.session import state, KW_DF
        ga4 = state().get(KW_DF)

    Note: does not enforce types at runtime — purely for editor support.
    """
    import streamlit as st
    return cast(AppState, st.session_state)
