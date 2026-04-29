"""Movement stats v2 — percentile-based with confidence-weighted resolver.

Why this matters: when learning expected ranking movement from SEMrush's
previous_position vs position columns, the mean is dragged by outliers. A few
keywords that jumped 10 positions skew the Easy-tier mean. The 75th percentile
is robust to those outliers and represents what's achievable for the better half
of keywords in a tier — which is what a paid retainer targets.

Combined with a confidence-weighted resolver: when the learned p75 is positive
with adequate sample size, use it; when negative or undersampled, blend with
engine defaults proportionally to confidence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.constants import DIFFICULTY_TIERS

ENGINE_DEFAULTS: dict[str, float] = {
    "Easy": 5.0, "Moderate": 4.0, "Hard": 3.0, "Very Hard": 2.0, "Extreme": 1.0,
}
TIER_ORDER = ["Easy", "Moderate", "Hard", "Very Hard", "Extreme"]


def _classify_difficulty(kd: float) -> str:
    for threshold, label in DIFFICULTY_TIERS:
        if kd <= threshold:
            return label
    return "Extreme"


def learn_movement_from_history_v2(
    kw_df: pd.DataFrame,
    movement_cap: int = 30,
    min_sample_per_tier: int = 10,
) -> dict:
    """Learn per-tier movement statistics with robust (percentile) measures.

    For each difficulty tier returns:
        median_gain, p75_gain, p90_gain, mean_gain (legacy), std_gain,
        sample_size, positive_share (fraction of keywords with gain > 0).

    Args:
        kw_df: DataFrame with 'previous_position', 'position', 'kd'.
        movement_cap: Gains beyond this in either direction are treated as
            SEMrush glitches and excluded. Default 30.
        min_sample_per_tier: Tiers with fewer samples are omitted.
    """
    if "previous_position" not in kw_df.columns or "position" not in kw_df.columns:
        return {}

    df = kw_df.dropna(subset=["previous_position", "position", "kd"]).copy()
    df["movement"] = df["previous_position"].astype(float) - df["position"].astype(float)
    df = df[df["movement"].abs() <= movement_cap]

    stats: dict = {}
    for tier in TIER_ORDER:
        tier_mask = df["kd"].apply(_classify_difficulty) == tier
        gains = df.loc[tier_mask, "movement"]
        if len(gains) < min_sample_per_tier:
            continue
        stats[tier] = {
            "median_gain": float(gains.median()),
            "p75_gain": float(np.percentile(gains, 75)),
            "p90_gain": float(np.percentile(gains, 90)),
            "mean_gain": float(gains.mean()),
            "std_gain": float(gains.std()),
            "sample_size": int(len(gains)),
            "positive_share": float((gains > 0).mean()),
        }
    return stats


def resolve_movement_stats(
    learned: dict | None,
    mode: str = "auto",
    primary_statistic: str = "p75_gain",
    confidence_floor: float = 0.30,
    confidence_ceiling: float = 0.70,
) -> tuple[dict, list[str]]:
    """Decide what gain to use per tier — engine default or learned value.

    Args:
        learned: Output of learn_movement_from_history_v2().
        mode:
            "auto"          — confidence-weighted blend (recommended default).
            "force_engine"  — always use engine defaults.
            "force_learned" — always use learned value (whichever statistic).
        primary_statistic:
            "p75_gain"   — robust upper-half (default).
            "median_gain" — robust central tendency.
            "mean_gain"  — legacy; sensitive to outliers.
        confidence_floor: Below this → engine default.
        confidence_ceiling: Above this → learned value.
            Between floor and ceiling → linear blend.

    Returns:
        (per_tier_gains_dict, decision_reason_strings)
    """
    decisions: list[str] = []

    if mode == "force_engine":
        return ENGINE_DEFAULTS.copy(), ["mode=force_engine → engine defaults across all tiers"]

    if mode == "force_learned":
        if not learned:
            return ENGINE_DEFAULTS.copy(), [
                "mode=force_learned but no learned data → engine defaults"
            ]
        result = {}
        decisions.append("mode=force_learned")
        for tier in TIER_ORDER:
            if tier in learned:
                val = learned[tier][primary_statistic]
                result[tier] = val
                decisions.append(f"  {tier}: {primary_statistic}={val:+.2f} (n={learned[tier]['sample_size']})")
            else:
                result[tier] = ENGINE_DEFAULTS[tier]
                decisions.append(f"  {tier}: engine default ({ENGINE_DEFAULTS[tier]}) — no data")
        return result, decisions

    # "auto" mode
    if not learned:
        return ENGINE_DEFAULTS.copy(), ["no learned data → engine defaults across all tiers"]

    result = {}
    decisions.append(
        f"mode=auto, primary_statistic={primary_statistic}, "
        f"blend range [{confidence_floor:.2f}, {confidence_ceiling:.2f}]"
    )

    for tier in TIER_ORDER:
        default = ENGINE_DEFAULTS[tier]
        if tier not in learned:
            result[tier] = default
            decisions.append(f"  {tier}: engine default ({default}) — no data for this tier")
            continue

        s = learned[tier]
        val = s[primary_statistic]
        n = s["sample_size"]

        # Sample-size confidence: 0 at n=10, 1.0 at n=50+
        n_conf = min(1.0, max(0.0, (n - 10) / 40.0))
        # Direction confidence: 0 if val <= 0, 1.0 when val >= engine default
        dir_conf = 0.0 if val <= 0 else (1.0 if val >= default else val / default)
        confidence = n_conf * dir_conf

        if confidence < confidence_floor:
            result[tier] = default
            decisions.append(
                f"  {tier}: engine default ({default}) — learned {primary_statistic}={val:+.2f} "
                f"(n={n}, conf={confidence:.2f} < floor {confidence_floor:.2f})"
            )
        elif confidence > confidence_ceiling:
            result[tier] = val
            decisions.append(
                f"  {tier}: learned ({val:+.2f}) — high confidence (n={n}, conf={confidence:.2f})"
            )
        else:
            blended = confidence * val + (1 - confidence) * default
            result[tier] = blended
            decisions.append(
                f"  {tier}: blend ({blended:+.2f}) = {int(confidence*100)}% learned "
                f"{primary_statistic}={val:+.2f} + {int((1-confidence)*100)}% default {default} (n={n})"
            )

    return result, decisions


def estimate_target_position_v2(
    current_pos: int,
    kd: float,
    effort: str,
    resolved_gains: dict,
) -> int:
    """Estimate target position using pre-resolved gains from resolve_movement_stats().

    Drop-in replacement for engine.positional_engine.estimate_target_position
    when using v5 movement stats.
    """
    effort_factors = {"light": 0.5, "moderate": 1.0, "aggressive": 1.5}
    tier = _classify_difficulty(float(kd))
    base_gain = resolved_gains.get(tier, ENGINE_DEFAULTS[tier])
    gain = round(base_gain * effort_factors.get(effort, 1.0))
    return max(1, int(current_pos) - int(gain))
