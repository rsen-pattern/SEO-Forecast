"""Centralised assumptions store for the SEO forecasting engine.

All forecast parameters that can be detected from data or overridden by the
user live here. Downstream engines read from the store instead of hardcoding
defaults — making the assumptions visible, auditable, and changeable in one
place.

Provenance tracking:
  "defaulted"  — using the built-in default, no data or user input yet
  "detected"   — value was inferred from uploaded data (GA4, roadmap, etc.)
  "overridden" — user has explicitly set this value via the assumptions panel

Usage (in a Streamlit page):
    from engine.assumptions import initialise_assumptions, get_assumption, run_detection
    store = st.session_state.setdefault("assumptions", {})
    initialise_assumptions(store)
    run_detection(store, ga4_df=ga4_df)
    cvr = get_assumption(store, "blended_cr_pct")

Usage (in tests):
    store = {}
    initialise_assumptions(store)
    assert get_assumption(store, "blended_cr_pct") == 2.5
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Provenance = Literal["defaulted", "detected", "overridden"]

_VAL = "value"
_PROV = "provenance"
_SRC = "source"


@dataclass
class Assumption:
    """Metadata definition for a single forecast assumption."""
    key: str
    label: str
    default: Any
    unit: str = ""
    min_val: float | None = None
    max_val: float | None = None


ASSUMPTIONS: dict[str, Assumption] = {
    "blended_cr_pct": Assumption(
        key="blended_cr_pct",
        label="Blended Conversion Rate",
        default=2.5,
        unit="%",
        min_val=0.0,
        max_val=100.0,
    ),
    "aov": Assumption(
        key="aov",
        label="Average Order Value",
        default=100.0,
        unit="$",
        min_val=0.0,
        max_val=None,
    ),
    "currency": Assumption(
        key="currency",
        label="Currency",
        default="USD",
        unit="",
    ),
    "effort_level": Assumption(
        key="effort_level",
        label="Effort Level",
        default="moderate",
        unit="",
    ),
    "content_cadence": Assumption(
        key="content_cadence",
        label="Content Cadence",
        default=4,
        unit="posts/month",
        min_val=1,
        max_val=100,
    ),
    "maintenance_coverage": Assumption(
        key="maintenance_coverage",
        label="Maintenance Coverage",
        default=0.0,
        unit="",
        min_val=0.0,
        max_val=1.0,
    ),
    "aio_monthly_growth": Assumption(
        key="aio_monthly_growth",
        label="AIO Monthly Growth Rate",
        default=0.025,
        unit="",
        min_val=0.0,
        max_val=1.0,
    ),
    "aio_ctr_penalty_informational": Assumption(
        key="aio_ctr_penalty_informational",
        label="AIO CTR Penalty (Informational)",
        default=0.45,
        unit="",
        min_val=0.0,
        max_val=1.0,
    ),
    "decay_rate_top3": Assumption(
        key="decay_rate_top3",
        label="Annual Decay Rate (Top 3)",
        default=0.08,
        unit="",
        min_val=0.0,
        max_val=1.0,
    ),
    "decay_rate_top10": Assumption(
        key="decay_rate_top10",
        label="Annual Decay Rate (Top 10)",
        default=0.12,
        unit="",
        min_val=0.0,
        max_val=1.0,
    ),
    "brand_terms": Assumption(
        key="brand_terms",
        label="Brand Terms",
        default=[],
        unit="",
    ),
    "exclude_brand_from_forecasts": Assumption(
        key="exclude_brand_from_forecasts",
        label="Exclude Brand from Forecasts",
        default=True,
        unit="",
    ),
    "seasonality_source": Assumption(
        key="seasonality_source",
        label="Seasonality Source",
        default="defaulted",
        unit="",
    ),
    "seasonality_blend_weight": Assumption(
        key="seasonality_blend_weight",
        label="Seasonality Blend Weight",
        default=0.0,
        unit="",
        min_val=0.0,
        max_val=1.0,
    ),
    # ── Per-focus effort levels (from AI roadmap extraction) ──────────────────
    "content_effort_level": Assumption(key="content_effort_level", label="Content Effort Level", default="moderate"),
    "technical_effort_level": Assumption(key="technical_effort_level", label="Technical Effort Level", default="moderate"),
    "on_page_effort_level": Assumption(key="on_page_effort_level", label="On-Page Effort Level", default="moderate"),
    "off_page_effort_level": Assumption(key="off_page_effort_level", label="Off-Page Effort Level", default="moderate"),
    "local_effort_level": Assumption(key="local_effort_level", label="Local Effort Level", default="moderate"),
    "analytics_effort_level": Assumption(key="analytics_effort_level", label="Analytics Effort Level", default="moderate"),
    "strategy_effort_level": Assumption(key="strategy_effort_level", label="Strategy Effort Level", default="moderate"),
    # ── Per-focus monthly hours ────────────────────────────────────────────────
    "content_monthly_hours": Assumption(key="content_monthly_hours", label="Content Monthly Hours", default=0.0, unit="hrs/month", min_val=0.0),
    "technical_monthly_hours": Assumption(key="technical_monthly_hours", label="Technical Monthly Hours", default=0.0, unit="hrs/month", min_val=0.0),
    "on_page_monthly_hours": Assumption(key="on_page_monthly_hours", label="On-Page Monthly Hours", default=0.0, unit="hrs/month", min_val=0.0),
    "off_page_monthly_hours": Assumption(key="off_page_monthly_hours", label="Off-Page Monthly Hours", default=0.0, unit="hrs/month", min_val=0.0),
    "local_monthly_hours": Assumption(key="local_monthly_hours", label="Local Monthly Hours", default=0.0, unit="hrs/month", min_val=0.0),
    "analytics_monthly_hours": Assumption(key="analytics_monthly_hours", label="Analytics Monthly Hours", default=0.0, unit="hrs/month", min_val=0.0),
    "strategy_monthly_hours": Assumption(key="strategy_monthly_hours", label="Strategy Monthly Hours", default=0.0, unit="hrs/month", min_val=0.0),
    # ── Portfolio-level derived ────────────────────────────────────────────────
    "total_monthly_hours": Assumption(key="total_monthly_hours", label="Total Monthly Hours", default=0.0, unit="hrs/month", min_val=0.0),
    "positional_effort_level": Assumption(key="positional_effort_level", label="Positional Effort Level", default="moderate"),
    "timeline_months_covered": Assumption(key="timeline_months_covered", label="Roadmap Timeline (months)", default=12, unit="months", min_val=1.0),
    "strategy_restart_month": Assumption(key="strategy_restart_month", label="Strategy Restart Month", default=None),
    "industry": Assumption(key="industry", label="Industry", default="Unknown"),
    "retainer_aud_monthly": Assumption(key="retainer_aud_monthly", label="Monthly Retainer (AUD)", default=0.0, unit="AUD/month", min_val=0.0),
    "client_name": Assumption(key="client_name", label="Client Name", default=""),
}


# ── Core store API ────────────────────────────────────────────────────────────


def initialise_assumptions(store: dict, force: bool = False) -> None:
    """Populate store with defaults. No-op if already initialised (unless force=True)."""
    if not force and store.get("_initialised"):
        return
    for key, assumption in ASSUMPTIONS.items():
        if force or key not in store:
            store[key] = {
                _VAL: assumption.default,
                _PROV: "defaulted",
                _SRC: "built-in default",
            }
    store["_initialised"] = True


def get_assumption(store: dict, key: str) -> Any:
    """Return the current value for an assumption key."""
    if key not in ASSUMPTIONS:
        raise KeyError(f"Unknown assumption: {key!r}")
    entry = store.get(key)
    if entry is None:
        return ASSUMPTIONS[key].default
    return entry.get(_VAL, ASSUMPTIONS[key].default)


def get_provenance(store: dict, key: str) -> dict:
    """Return full provenance record for an assumption."""
    if key not in ASSUMPTIONS:
        raise KeyError(f"Unknown assumption: {key!r}")
    entry = store.get(key, {})
    meta = ASSUMPTIONS[key]
    return {
        "key": key,
        "label": meta.label,
        "value": entry.get(_VAL, meta.default),
        "provenance": entry.get(_PROV, "defaulted"),
        "source": entry.get(_SRC, "built-in default"),
        "unit": meta.unit,
    }


def override_assumption(
    store: dict,
    key: str,
    value: Any,
    source: str = "user override",
) -> None:
    """Explicitly set an assumption value, marking it as overridden."""
    if key not in ASSUMPTIONS:
        raise KeyError(f"Unknown assumption: {key!r}")
    store[key] = {_VAL: value, _PROV: "overridden", _SRC: source}


def clear_override(store: dict, key: str) -> None:
    """Revert an overridden assumption to its built-in default.

    Detected values are not restored — a fresh run_detection() call is needed.
    """
    if key not in ASSUMPTIONS:
        raise KeyError(f"Unknown assumption: {key!r}")
    if store.get(key, {}).get(_PROV) != "overridden":
        return
    store[key] = {
        _VAL: ASSUMPTIONS[key].default,
        _PROV: "defaulted",
        _SRC: "built-in default",
    }


def assumptions_summary(store: dict) -> list[dict]:
    """Return provenance records for all assumptions in registry order."""
    return [get_provenance(store, key) for key in ASSUMPTIONS]


# ── Detection layer ───────────────────────────────────────────────────────────


def run_detection(
    store: dict,
    ga4_df=None,
    kw_df=None,
    roadmap_data: dict | None = None,
) -> list[str]:
    """Detect assumption values from available data. Returns list of updated keys.

    Only updates assumptions that are "defaulted" or "detected" — never
    overwrites "overridden" values set by the user.
    """
    detected: list[str] = []

    if ga4_df is not None:
        detected.extend(_detect_from_ga4(store, ga4_df))

    if kw_df is not None:
        detected.extend(_detect_from_keywords(store, kw_df))

    if roadmap_data is not None:
        detected.extend(_detect_from_roadmap(store, roadmap_data))

    return detected


def _set_detected(store: dict, key: str, value: Any, source: str) -> None:
    """Update store with a detected value unless the user has overridden it."""
    if store.get(key, {}).get(_PROV) == "overridden":
        return
    store[key] = {_VAL: value, _PROV: "detected", _SRC: source}


def _detect_from_ga4(store: dict, ga4_df) -> list[str]:
    detected: list[str] = []

    traffic_col = "traffic" if "traffic" in ga4_df.columns else None
    txn_col = "transactions" if "transactions" in ga4_df.columns else None
    aov_col = "aov" if "aov" in ga4_df.columns else None

    if traffic_col and txn_col:
        total_traffic = float(ga4_df[traffic_col].sum())
        if total_traffic > 0:
            total_txn = float(ga4_df[txn_col].sum())
            cr_pct = round(total_txn / total_traffic * 100, 2)
            _set_detected(store, "blended_cr_pct", cr_pct, "GA4 sessions + transactions")
            detected.append("blended_cr_pct")

    if aov_col:
        valid = ga4_df[aov_col].dropna()
        if len(valid) > 0:
            aov = round(float(valid.mean()), 2)
            _set_detected(store, "aov", aov, "GA4 average order value")
            detected.append("aov")

    return detected


def _detect_from_keywords(store: dict, kw_df) -> list[str]:
    return []


_EFFORT_ORDER: dict[str, int] = {"light": 0, "moderate": 1, "aggressive": 2}
_EFFORT_NAMES: list[str] = ["light", "moderate", "aggressive"]
_FOCUS_KEYS: list[str] = ["content", "technical", "on_page", "off_page", "local", "analytics", "strategy"]


def recompute_rollups(store: dict) -> None:
    """After per-focus keys change, recompute the three backward-compat rollup keys.

    Only runs when at least one per-focus key is in detected/overridden state —
    avoids overwriting defaults when no roadmap has been ingested.
    """
    has_detected = any(
        store.get(f"{f}_monthly_hours", {}).get(_PROV) in ("detected", "overridden")
        or store.get(f"{f}_effort_level", {}).get(_PROV) in ("detected", "overridden")
        for f in _FOCUS_KEYS
    )
    if not has_detected:
        return

    # effort_level = max of content, on_page, off_page (the three forecast-relevant focuses)
    rollup_foci = ("content", "on_page", "off_page")
    effort_vals = [get_assumption(store, f"{f}_effort_level") for f in rollup_foci]
    max_idx = max(_EFFORT_ORDER.get(str(v), 1) for v in effort_vals)
    _set_detected(store, "effort_level", _EFFORT_NAMES[max_idx], "computed from per-focus effort")

    # positional_effort_level = max of on_page, off_page
    pos_vals = [get_assumption(store, "on_page_effort_level"), get_assumption(store, "off_page_effort_level")]
    pos_idx = max(_EFFORT_ORDER.get(str(v), 1) for v in pos_vals)
    _set_detected(store, "positional_effort_level", _EFFORT_NAMES[pos_idx], "computed from on_page + off_page effort")

    # content_cadence = round(content_monthly_hours / 10), min 1
    # Only recompute if content hours were actually detected
    content_hours = float(get_assumption(store, "content_monthly_hours"))
    if content_hours > 0:
        _set_detected(store, "content_cadence", max(1, round(content_hours / 10)), "computed from content_monthly_hours")

    # total_monthly_hours = sum of per-focus hours
    total = sum(float(get_assumption(store, f"{f}_monthly_hours")) for f in _FOCUS_KEYS)
    _set_detected(store, "total_monthly_hours", round(total, 1), "sum of per-focus hours")

    # maintenance_coverage = min(1.0, (on_page + technical) / 20) — 20h/month = full coverage
    on_page_hrs = float(get_assumption(store, "on_page_monthly_hours"))
    technical_hrs = float(get_assumption(store, "technical_monthly_hours"))
    coverage = min(1.0, (on_page_hrs + technical_hrs) / 20.0)
    _set_detected(store, "maintenance_coverage", round(coverage, 2), "computed from on_page + technical hours")


def _detect_from_roadmap(store: dict, roadmap_data: dict) -> list[str]:
    """Process roadmap data into assumption keys.

    Dispatches to the appropriate handler based on schema_version / structure:
    - schema_version "2.x" → _detect_from_bundle_v2 (Pattern native + AI bundles)
    - has 'per_focus' key (v1 AI bundle) → _detect_from_roadmap_bundle
    - flat legacy dict → direct scalar mapping
    """
    if not isinstance(roadmap_data, dict):
        return []

    schema = str(roadmap_data.get("schema_version", ""))
    if schema.startswith("2."):
        detected = _detect_from_bundle_v2(store, roadmap_data)
    elif "per_focus" in roadmap_data:
        detected = _detect_from_roadmap_bundle(store, roadmap_data)
    else:
        detected: list[str] = []
        mapping = {
            "effort_level": "roadmap import",
            "content_cadence": "roadmap import",
            "maintenance_coverage": "roadmap import",
        }
        for key, source in mapping.items():
            if key in roadmap_data and roadmap_data[key] is not None:
                _set_detected(store, key, roadmap_data[key], source)
                detected.append(key)

    recompute_rollups(store)
    return detected


def _detect_from_bundle_v2(store: dict, bundle: dict) -> list[str]:
    """Flatten a v2 roadmap bundle (Pattern native or hybrid) into assumption keys."""
    detected: list[str] = []

    # Client metadata
    meta = bundle.get("client_metadata", {})
    for key in ("client_name", "industry", "retainer_aud_monthly"):
        val = meta.get(key)
        if val not in (None, "", 0, 0.0):
            _set_detected(store, key, val, "roadmap extraction")
            detected.append(key)

    # Per-focus effort + hours (shared with v1 logic)
    detected.extend(_extract_per_focus_keys(store, bundle))

    # Timeline
    timeline = bundle.get("timeline", {})
    months = timeline.get("months_covered")
    if isinstance(months, (int, float)) and months > 0:
        _set_detected(store, "timeline_months_covered", int(months), "roadmap extraction")
        detected.append("timeline_months_covered")
    restart = timeline.get("strategy_restart_month")
    if restart is not None:
        _set_detected(store, "strategy_restart_month", restart, "roadmap extraction")
        detected.append("strategy_restart_month")

    return detected


def _detect_from_roadmap_bundle(store: dict, bundle: dict) -> list[str]:
    """Flatten a v1 AI-extracted bundle into per-focus assumption keys."""
    detected: list[str] = list(_extract_per_focus_keys(store, bundle))

    # v1 timeline has months_covered directly
    timeline = bundle.get("timeline", {})
    months_covered = timeline.get("months_covered")
    if isinstance(months_covered, (int, float)) and months_covered > 0:
        _set_detected(store, "timeline_months_covered", int(months_covered), "AI roadmap extraction")
        detected.append("timeline_months_covered")

    return detected


def _extract_per_focus_keys(store: dict, bundle: dict) -> list[str]:
    """Shared helper: extract per-focus effort + hours from any bundle version."""
    detected: list[str] = []
    per_focus = bundle.get("per_focus", {})
    source = "roadmap extraction"

    for focus_key in _FOCUS_KEYS:
        focus_data = per_focus.get(focus_key, {})
        effort = focus_data.get("effort_level")
        hours = focus_data.get("monthly_hours")

        if effort in ("light", "moderate", "aggressive"):
            _set_detected(store, f"{focus_key}_effort_level", effort, source)
            detected.append(f"{focus_key}_effort_level")

        if isinstance(hours, (int, float)) and hours >= 0:
            _set_detected(store, f"{focus_key}_monthly_hours", float(hours), source)
            detected.append(f"{focus_key}_monthly_hours")

    return detected
