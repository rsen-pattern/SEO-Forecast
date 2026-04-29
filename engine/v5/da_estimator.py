"""Domain Authority estimation from SEMrush ranking data — v5.

The hypothesis: a site consistently ranking top-10 for keywords of KD=N has
effective DA approximately equal to N. The 90th percentile of KD across
non-branded top-10 rankings gives a defensible DA estimate without requiring
an external Moz/Ahrefs lookup.

Important: this estimate measures *capability demonstrated against actual
SERPs*, not a raw authority score. It will typically be lower than the Moz/
Ahrefs DA — use it as a calibration input, not an absolute measure.
"""

from __future__ import annotations

import pandas as pd


def estimate_da_from_rankings(
    semrush_df: pd.DataFrame,
    brand_classifier=None,
    top_position_threshold: int = 10,
    percentile: float = 0.90,
    min_sample: int = 20,
) -> tuple[int | None, str]:
    """Estimate effective domain authority from a SEMrush ranking export.

    Args:
        semrush_df: DataFrame with at least 'keyword', 'position', 'kd'.
        brand_classifier: Optional callable(keyword) -> bool that returns True
            for branded keywords. When provided, branded keywords are excluded.
        top_position_threshold: Positions up to this value count as "top".
        percentile: Which KD percentile to use as the DA estimate. Default 0.90
            balances robustness vs outlier sensitivity.
        min_sample: Minimum qualifying rankings required to make an estimate.

    Returns:
        (da_estimate, rationale_string)
        da_estimate is None when there's insufficient evidence.
    """
    if "position" not in semrush_df.columns or "kd" not in semrush_df.columns:
        return None, "missing position or kd columns in SEMrush export"

    df = semrush_df.copy()

    if brand_classifier is not None and "keyword" in df.columns:
        df = df[~df["keyword"].apply(brand_classifier)]

    df = df.dropna(subset=["position", "kd"])
    df["position"] = pd.to_numeric(df["position"], errors="coerce")
    df["kd"] = pd.to_numeric(df["kd"], errors="coerce")
    df = df.dropna(subset=["position", "kd"])

    top = df[df["position"] <= top_position_threshold]
    if len(top) < min_sample:
        return None, (
            f"insufficient top-{top_position_threshold} non-branded rankings "
            f"to estimate DA ({len(top)} rankings, need ≥{min_sample})"
        )

    da_estimate = int(top["kd"].quantile(percentile))
    median_kd = top["kd"].median()
    p75_kd = top["kd"].quantile(0.75)

    rationale = (
        f"DA ≈ {da_estimate} ({int(percentile * 100)}th percentile KD across "
        f"{len(top):,} top-{top_position_threshold} non-branded rankings; "
        f"median KD: {median_kd:.0f}, p75: {p75_kd:.0f})"
    )
    return da_estimate, rationale


def compare_da_estimate_to_supplied(
    estimated: int | None,
    supplied: int | None,
    tolerance: int = 10,
) -> str:
    """Compare an auto-derived DA against a user-supplied or Moz/Ahrefs value.

    Returns a human-readable string for display in methodology snapshot or UI.
    """
    if estimated is None and supplied is None:
        return "DA not provided and could not be estimated"
    if estimated is None:
        return f"DA={supplied} (user-supplied; could not auto-estimate)"
    if supplied is None:
        return f"DA={estimated} (auto-estimated; no external value to compare)"
    diff = abs(estimated - supplied)
    if diff <= tolerance:
        return (
            f"DA={supplied} (user-supplied); auto-estimate {estimated} "
            f"agrees within ±{tolerance}"
        )
    return (
        f"DA={supplied} (user-supplied); auto-estimate {estimated} differs by "
        f"{diff}. Worth reviewing — check whether the site profile has shifted "
        f"or one source is stale."
    )
