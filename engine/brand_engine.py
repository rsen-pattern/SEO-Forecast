"""Brand keyword classification engine.

Classifies keywords as branded vs. non-branded using a list of brand terms.
Uses broad-match (case-insensitive substring) so entering "helen" flags all
keywords containing that word regardless of what follows.
"""

from __future__ import annotations

import pandas as pd


def classify_keywords_as_branded(
    kw_df: pd.DataFrame,
    brand_terms: list[str],
) -> pd.DataFrame:
    """Add an 'is_branded' boolean column to kw_df.

    Matching is broad-match: case-insensitive substring. Each brand term is
    checked independently so entering "helen" flags "helen kaminski bags".

    Args:
        kw_df: DataFrame with a 'keyword' column.
        brand_terms: List of brand term strings.

    Returns:
        Copy of kw_df with new 'is_branded' column.
    """
    df = kw_df.copy()
    if not brand_terms or "keyword" not in df.columns:
        df["is_branded"] = False
        return df

    normalised = [t.strip().lower() for t in brand_terms if t.strip()]

    def _is_branded(keyword: str) -> bool:
        kw = keyword.lower()
        return any(term in kw for term in normalised)

    df["is_branded"] = df["keyword"].apply(_is_branded)
    return df


def extract_domain_from_semrush(kw_df: pd.DataFrame) -> str | None:
    """Pick the most common URL host from the SEMrush export.

    Returns None if no URL-like column is present or all values are empty.
    """
    from urllib.parse import urlparse

    url_col = next(
        (c for c in kw_df.columns if c.lower() in ("url", "page", "landing page")),
        None,
    )
    if url_col is None:
        return None

    hosts = kw_df[url_col].dropna().apply(lambda u: urlparse(str(u)).netloc)
    hosts = hosts[hosts != ""]
    if hosts.empty:
        return None
    return hosts.value_counts().index[0]


def split_branded_vs_non_branded(
    kw_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a keyword DataFrame into (branded_df, non_branded_df).

    Requires 'is_branded' column to already be present; call
    classify_keywords_as_branded first if it's missing.
    """
    if "is_branded" not in kw_df.columns:
        raise ValueError("kw_df missing 'is_branded' column — run classify_keywords_as_branded first.")
    branded = kw_df[kw_df["is_branded"]].reset_index(drop=True)
    non_branded = kw_df[~kw_df["is_branded"]].reset_index(drop=True)
    return branded, non_branded
