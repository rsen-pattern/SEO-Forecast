"""Extract channel-level conversion rate and AOV from a Pattern GA4 export.

Pattern's GA4 export includes a 'Session default channel group' column which
lets us separate Organic Search from blended traffic. This is more accurate
than applying a fixed multiplier to the blended CR.

Handles the FY-day-bug in the Revenue sheet (Pattern's export sometimes encodes
fiscal year as the day field — e.g. 2026-04-23 actually means FY23 April).
"""

from __future__ import annotations

import pandas as pd

ORGANIC_CHANNELS = {"Organic Search"}
ORGANIC_BROAD_CHANNELS = {"Organic Search", "Organic Shopping", "Organic Video"}


def _fix_fy_dates(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    """Detect and correct Pattern's FY-day-bug.

    The Revenue sheet encodes FY as the day field:
        2026-04-23 → April of FY23 → calendar month April 2022 (if month >= 7)
                                    or April 2023 (if month < 7, post-Jan).
    Detection: if all day values are in [15, 35], it's almost certainly FY-encoded.
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    days = df[date_col].dt.day.dropna().unique()
    if len(days) > 0 and all(15 <= int(d) <= 35 for d in days):
        months_arr = df[date_col].dt.month
        fy_year_arr = 2000 + df[date_col].dt.day
        cal_year = fy_year_arr.copy()
        # AU financial year: FY24 = Jul 2023 – Jun 2024
        cal_year[months_arr >= 7] = fy_year_arr[months_arr >= 7] - 1
        df[date_col] = pd.to_datetime({
            "year": cal_year, "month": months_arr, "day": 1
        })
    return df


def extract_organic_metrics(
    ga4_xlsx_path: str,
    organic_channels: set | None = None,
) -> dict:
    """Pull Organic-Search-specific monthly metrics from a Pattern GA4 export.

    Expected sheets: 'Sessions', 'Transactions', 'Revenue'
    Each sheet must have 'Year month' and 'Session default channel group' columns.

    Returns:
        sessions:      DataFrame[date, sessions] — organic monthly sessions
        transactions:  DataFrame[date, transactions] — organic monthly transactions
        revenue:       DataFrame[date, revenue] — organic monthly revenue
        cr_organic:    float — overall organic search CR over the period
        cr_blended:    float — all-channel CR — for comparison only
        aov_organic:   float — weighted average organic AOV
        aov_blended:   float — weighted average blended AOV
        cr_ratio:      float — cr_organic / cr_blended
        cr_by_month:   DataFrame[date, cr_organic, cr_blended, cr_ratio]
        warnings:      list[str] — issues found during extraction
    """
    if organic_channels is None:
        organic_channels = ORGANIC_CHANNELS

    warnings: list[str] = []

    try:
        xl = pd.ExcelFile(ga4_xlsx_path)
    except Exception as e:
        return {"warnings": [f"Could not open GA4 file: {e}"]}

    def _load_sheet(sheet_name: str, value_col: str) -> pd.DataFrame | None:
        if sheet_name not in xl.sheet_names:
            warnings.append(f"Sheet '{sheet_name}' not found in GA4 export.")
            return None
        df = xl.parse(sheet_name)
        df = df.rename(columns={
            "Year month": "date",
            "Session default channel group": "channel",
            value_col: "value",
        })
        if "date" not in df.columns or "channel" not in df.columns or "value" not in df.columns:
            warnings.append(
                f"Sheet '{sheet_name}' missing expected columns. "
                f"Found: {list(df.columns)}"
            )
            return None
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(
            df["value"].astype(str).str.replace(r"[$,\s]", "", regex=True)
                       .replace({"-": "0", "": "0"}),
            errors="coerce",
        ).fillna(0)
        return df

    ses_raw = _load_sheet("Sessions", "Sessions")
    txn_raw = _load_sheet("Transactions", "Transactions")
    rev_raw = _load_sheet("Revenue", "Total revenue")

    if ses_raw is None:
        return {"warnings": warnings}

    # Apply FY-day-bug fix to Revenue sheet
    if rev_raw is not None:
        rev_raw = _fix_fy_dates(rev_raw, "date")

    def _split_channels(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        organic = df[df["channel"].isin(organic_channels)].copy()
        total = df.copy()
        return organic, total

    def _monthly(df: pd.DataFrame) -> pd.DataFrame:
        return df.groupby("date", as_index=False)["value"].sum()

    ses_org, ses_tot = _split_channels(ses_raw)
    ses_org_m = _monthly(ses_org).rename(columns={"value": "sessions"})
    ses_tot_m = _monthly(ses_tot).rename(columns={"value": "sessions_total"})

    if ses_org_m.empty:
        available = sorted(ses_raw["channel"].dropna().unique())
        warnings.append(
            f"No sessions found for channels {organic_channels}. "
            f"Available channels: {available}"
        )
        return {"warnings": warnings}

    txn_org_m = pd.DataFrame(columns=["date", "transactions"])
    txn_tot_m = pd.DataFrame(columns=["date", "transactions_total"])
    if txn_raw is not None:
        txn_org, txn_tot = _split_channels(txn_raw)
        txn_org_m = _monthly(txn_org).rename(columns={"value": "transactions"})
        txn_tot_m = _monthly(txn_tot).rename(columns={"value": "transactions_total"})

    rev_org_m = pd.DataFrame(columns=["date", "revenue"])
    rev_tot_m = pd.DataFrame(columns=["date", "revenue_total"])
    if rev_raw is not None:
        rev_org, rev_tot = _split_channels(rev_raw)
        rev_org_m = _monthly(rev_org).rename(columns={"value": "revenue"})
        rev_tot_m = _monthly(rev_tot).rename(columns={"value": "revenue_total"})

    # Aggregate totals
    total_ses_org = ses_org_m["sessions"].sum()
    total_txn_org = txn_org_m["transactions"].sum() if "transactions" in txn_org_m else 0
    total_rev_org = rev_org_m["revenue"].sum() if "revenue" in rev_org_m else 0

    total_ses_all = ses_tot_m["sessions_total"].sum()
    total_txn_all = txn_tot_m["transactions_total"].sum() if "transactions_total" in txn_tot_m else 0
    total_rev_all = rev_tot_m["revenue_total"].sum() if "revenue_total" in rev_tot_m else 0

    cr_organic = total_txn_org / total_ses_org if total_ses_org > 0 else 0.0
    cr_blended = total_txn_all / total_ses_all if total_ses_all > 0 else 0.0
    aov_organic = total_rev_org / total_txn_org if total_txn_org > 0 else 0.0
    aov_blended = total_rev_all / total_txn_all if total_txn_all > 0 else 0.0
    cr_ratio = cr_organic / cr_blended if cr_blended > 0 else 0.0

    # Validate CR ratio
    if 0 < cr_ratio < 0.4:
        warnings.append(
            f"Organic CR is much lower than blended (ratio {cr_ratio:.2f}). "
            f"Expected non-branded organic to convert at 0.5–0.85× blended."
        )
    elif cr_ratio > 1.2:
        warnings.append(
            f"Organic CR is higher than blended (ratio {cr_ratio:.2f}). "
            f"Could indicate brand-search-heavy organic traffic — verify."
        )

    # Per-month CR
    cr_by_month = (
        ses_org_m.merge(txn_org_m, on="date", how="left")
        .merge(ses_tot_m, on="date", how="left")
        .merge(txn_tot_m, on="date", how="left")
    )
    cr_by_month["cr_organic"] = (
        cr_by_month["transactions"].fillna(0) / cr_by_month["sessions"].clip(lower=1)
    )
    cr_by_month["cr_blended"] = (
        cr_by_month.get("transactions_total", pd.Series(0.0, index=cr_by_month.index)).fillna(0)
        / cr_by_month.get("sessions_total", pd.Series(1.0, index=cr_by_month.index)).clip(lower=1)
    )
    cr_by_month["cr_ratio"] = cr_by_month["cr_organic"] / cr_by_month["cr_blended"].clip(lower=1e-9)

    return {
        "sessions": ses_org_m,
        "transactions": txn_org_m,
        "revenue": rev_org_m,
        "cr_organic": float(cr_organic),
        "cr_blended": float(cr_blended),
        "aov_organic": float(aov_organic),
        "aov_blended": float(aov_blended),
        "cr_ratio": float(cr_ratio),
        "cr_by_month": cr_by_month,
        "warnings": warnings,
    }


def summarize_for_methodology(metrics: dict) -> str:
    """One-paragraph summary for the methodology snapshot."""
    if not metrics.get("cr_organic"):
        return f"Channel-level CR extraction failed: {'; '.join(metrics.get('warnings', []))}"
    return (
        f"Organic Search CR: {metrics['cr_organic'] * 100:.3f}% "
        f"(vs blended {metrics['cr_blended'] * 100:.3f}%; "
        f"ratio {metrics['cr_ratio']:.2f}). "
        f"Organic AOV: ${metrics['aov_organic']:.2f} "
        f"(vs blended ${metrics['aov_blended']:.2f}). "
        f"Forecast revenue layer uses Organic Search values directly — "
        f"no multiplier needed."
    )
