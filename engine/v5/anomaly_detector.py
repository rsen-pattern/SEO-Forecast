"""YoY anomaly detection for baseline source-year months — v5.

When using YoY-replay baseline, each forecast month inherits its traffic value
from the same calendar month one year prior. If that source month was anomalous
(one-off promo, outage, algo penalty), the anomaly silently propagates into the
forecast.

Two-stage detection:
  1. Year-over-year: compare T-1 source month against T-2 same month.
     >40% deviation flags it. Stronger signal because it isolates seasonality.
  2. Surrounding-window fallback: when T-2 not available, compare against
     the local N-month median (>2σ flags it). Lower confidence.

Critical startup-period safeguard: if T-2 itself looks like a startup-period
month (T-2 < startup_ratio_threshold × surrounding T-2 months), the YoY check
is skipped — otherwise T-1 is falsely flagged as a "spike" against an
artificially low T-2.
"""

from __future__ import annotations

import pandas as pd


def detect_baseline_anomalies(
    ga4_df: pd.DataFrame,
    baseline_lookup: dict,
    yoy_threshold: float = 0.40,
    surrounding_window: int = 3,
    surrounding_z_threshold: float = 2.0,
    startup_ratio_threshold: float = 0.4,
) -> list[dict]:
    """Flag baseline source months that look anomalous.

    Args:
        ga4_df: DataFrame with 'date' and 'traffic' columns, monthly.
        baseline_lookup: Dict[pd.Timestamp, dict] from yoy_baseline(), where
            each value has at least 'traffic' and 'source' keys.
        yoy_threshold: Fraction deviation from T-2 that triggers a YoY flag.
            Default 0.40 (±40%).
        surrounding_window: Months each side for the fallback window.
        surrounding_z_threshold: Z-score threshold for surroundng-window flag.
        startup_ratio_threshold: T-2 / neighbor ratio below which T-2 is
            considered a startup-period month (suppresses YoY flag).

    Returns:
        List of flag dicts. Each dict contains:
            forecast_month, source_month, source_value, comparison_basis,
            flag_type, suggested_replacement, rationale.
            YoY flags also include: comparison_month, comparison_value, ratio.
            Surrounding flags also include: context_median, context_std, z_score.
    """
    flags: list[dict] = []
    df = (
        ga4_df.dropna(subset=["date", "traffic"])
        .copy()
        .assign(date=lambda d: pd.to_datetime(d["date"]).dt.to_period("M").dt.to_timestamp())
        .sort_values("date")
        .reset_index(drop=True)
    )

    for forecast_date, baseline in baseline_lookup.items():
        source_str = baseline.get("source", "")
        if not source_str.startswith("GA4"):
            continue

        # Parse source month from source string like "GA4 Jul-25 actual"
        try:
            month_token = source_str.split()[1]
            source_month = pd.to_datetime(month_token, format="%b-%y")
            source_month = pd.Timestamp(year=source_month.year, month=source_month.month, day=1)
        except (IndexError, ValueError):
            continue

        source_rows = df[df["date"] == source_month]
        if source_rows.empty:
            continue

        source_value = int(source_rows.iloc[0]["traffic"])
        source_idx = source_rows.index[0]

        # ── Stage 1: YoY (T-1 vs T-2) ────────────────────────────────────────
        prior_year = pd.Timestamp(year=source_month.year - 1, month=source_month.month, day=1)
        prior_rows = df[df["date"] == prior_year]

        if not prior_rows.empty:
            prior_value = int(prior_rows.iloc[0]["traffic"])
            prior_idx = prior_rows.index[0]

            if prior_value > 0:
                # Startup-period guard: check if T-2 is abnormally low
                t2_window_lo = max(0, prior_idx - surrounding_window)
                t2_window_hi = min(len(df), prior_idx + surrounding_window + 1)
                t2_neighbors = df.iloc[t2_window_lo:t2_window_hi]
                t2_others = t2_neighbors[t2_neighbors["date"] != prior_year]["traffic"]

                t2_is_startup = (
                    len(t2_others) >= 3
                    and t2_others.median() > 0
                    and prior_value < startup_ratio_threshold * t2_others.median()
                )

                if not t2_is_startup:
                    ratio = source_value / prior_value
                    if abs(ratio - 1.0) > yoy_threshold:
                        direction = "spike" if ratio > 1 else "dip"
                        flags.append({
                            "forecast_month": forecast_date,
                            "source_month": source_month,
                            "source_value": source_value,
                            "comparison_basis": "yoy",
                            "comparison_month": prior_year,
                            "comparison_value": prior_value,
                            "ratio": round(ratio, 3),
                            "flag_type": f"yoy_{direction}",
                            "suggested_replacement": prior_value,
                            "rationale": (
                                f"{source_month.strftime('%b %Y')} = {source_value:,} sessions, "
                                f"vs {prior_year.strftime('%b %Y')} = {prior_value:,} "
                                f"({(ratio - 1) * 100:+.0f}%). Possible one-off "
                                f"{'windfall' if direction == 'spike' else 'event (campaign gap, outage, algo)'}."
                            ),
                        })
                    continue  # YoY check ran (passed or flagged) — no double-check

        # ── Stage 2: Surrounding-window fallback ──────────────────────────────
        win_lo = max(0, source_idx - surrounding_window)
        win_hi = min(len(df), source_idx + surrounding_window + 1)
        window_others = df.iloc[win_lo:win_hi]
        window_others = window_others[window_others["date"] != source_month]["traffic"]

        if len(window_others) >= 3:
            median = window_others.median()
            std = window_others.std()
            if std > 0 and abs(source_value - median) > surrounding_z_threshold * std:
                z = (source_value - median) / std
                direction = "spike" if source_value > median else "dip"
                flags.append({
                    "forecast_month": forecast_date,
                    "source_month": source_month,
                    "source_value": source_value,
                    "comparison_basis": "surrounding",
                    "context_median": int(median),
                    "context_std": int(std),
                    "z_score": round(z, 2),
                    "flag_type": "surrounding_outlier",
                    "suggested_replacement": int(median),
                    "rationale": (
                        f"{source_month.strftime('%b %Y')} = {source_value:,}, "
                        f"vs surrounding-{surrounding_window * 2}mo median = {median:,.0f} "
                        f"(σ={std:,.0f}, z={z:+.1f}). T-2 same-month not available or looked "
                        f"like startup period — using local context. Lower confidence than YoY flag."
                    ),
                })

    return flags


def apply_overrides(
    baseline_lookup: dict,
    overrides: dict,
) -> dict:
    """Apply user-chosen replacements to baseline values.

    Args:
        baseline_lookup: Original baseline dict from yoy_baseline().
        overrides: Dict[forecast_date -> "accept" | int].
            "accept": keep the original value unchanged.
            int: replace the traffic value with this number.

    Returns:
        New baseline_lookup with overrides applied.
    """
    out = {}
    for fdate, baseline in baseline_lookup.items():
        if fdate not in overrides or overrides[fdate] == "accept":
            out[fdate] = baseline
            continue

        choice = overrides[fdate]
        if isinstance(choice, (int, float)):
            out[fdate] = {
                **baseline,
                "traffic": int(choice),
                "source": f"{baseline['source']} (overridden to {int(choice):,})",
            }
        else:
            out[fdate] = baseline
    return out
