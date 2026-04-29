"""Brand classifier — v5.

Three-stage workshop:
  1. build_classifier(config) — the runtime classifier callable
  2. suggest_branded_candidates(semrush_df) — surface likely-brand keywords
     so the user can confirm before locking in the config
  3. detect_collisions(semrush_df, word_boundary_term) — surface category
     collisions for ambiguous word-boundary terms (e.g. "cable" → "cable knit")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class BrandConfig:
    """Configuration for brand classification.

    substring_terms: matched anywhere in the keyword (case-insensitive).
    word_boundary_terms: matched as whole words only.
    excluded_followers: tokens that, when adjacent to a word_boundary_term,
        cancel the brand match (handles "cable knit", "cable car", etc.).
    """
    substring_terms: list[str] = field(default_factory=list)
    word_boundary_terms: list[str] = field(default_factory=list)
    excluded_followers: list[str] = field(default_factory=list)


def build_classifier(config: BrandConfig):
    """Return a callable(keyword: str) -> bool that returns True when branded."""
    sub_pat = (
        re.compile("|".join(re.escape(t) for t in config.substring_terms), re.IGNORECASE)
        if config.substring_terms else None
    )

    def is_branded(keyword: str) -> bool:
        s = str(keyword).lower()
        if sub_pat and sub_pat.search(s):
            return True
        for term in config.word_boundary_terms:
            if re.search(rf"\b{re.escape(term.lower())}\b", s):
                for follower in config.excluded_followers:
                    if re.search(
                        rf"\b{re.escape(term.lower())}\s+{re.escape(follower.lower())}\b"
                        rf"|\b{re.escape(follower.lower())}\s+{re.escape(term.lower())}\b",
                        s,
                    ):
                        return False
                return True
        return False

    return is_branded


def suggest_branded_candidates(
    semrush_df: pd.DataFrame,
    top_n_by_volume: int = 100,
    min_volume: int = 100,
) -> pd.DataFrame:
    """Surface keywords that look like brand searches, ranked by likelihood score.

    Heuristics (each contributes to brand_score 0–1):
      - Position 1 with KD < 30 (high CTR + low difficulty = brand pattern)
      - Single-word or two-word keyword (compact brand names)
      - URL contains the keyword as a path fragment
      - High CTR proxy (current_traffic / volume > 0.15)

    Returns DataFrame sorted by brand_score descending. The caller shows this
    to the user, who confirms which rows to classify as branded.
    """
    df = semrush_df.copy()
    if "volume" not in df.columns or "position" not in df.columns:
        return pd.DataFrame()

    df = df.dropna(subset=["volume", "position"])
    df = df[df["volume"] >= min_volume].sort_values("volume", ascending=False).head(top_n_by_volume)
    if df.empty:
        return pd.DataFrame()

    score = pd.Series(0.0, index=df.index)

    if "kd" in df.columns:
        kd_filled = df["kd"].fillna(50)
        score += ((df["position"] == 1) & (kd_filled < 30)).astype(float) * 0.4

    word_count = df["keyword"].astype(str).str.split().str.len()
    score += (word_count <= 2).astype(float) * 0.2

    if "url" in df.columns:
        def _kw_in_url(row) -> bool:
            url = str(row.get("url", "")).lower()
            kw = str(row["keyword"]).lower()
            tokens = [t for t in re.split(r"\W+", kw) if len(t) >= 4]
            return any(t in url for t in tokens) if tokens else False
        score += df.apply(_kw_in_url, axis=1).astype(float) * 0.2

    if "current_traffic" in df.columns:
        ctr_proxy = df["current_traffic"].fillna(0) / df["volume"].clip(lower=1)
        score += (ctr_proxy > 0.15).astype(float) * 0.2

    out = df.assign(brand_score=score.round(2))[[
        c for c in ["keyword", "volume", "position", "kd", "url", "brand_score"]
        if c in df.columns or c == "brand_score"
    ]].sort_values("brand_score", ascending=False)

    def _suggest(row) -> str:
        kw_str = str(row["keyword"]).lower()
        if row["brand_score"] >= 0.6:
            return "word_boundary" if len(kw_str.split()) == 1 else "substring"
        if row["brand_score"] >= 0.3:
            return "review"
        return "unlikely"

    out["suggested_classification"] = out.apply(_suggest, axis=1)
    return out.reset_index(drop=True)


def detect_collisions(
    semrush_df: pd.DataFrame,
    word_boundary_term: str,
    min_follower_count: int = 3,
    min_volume_share: float = 0.01,
) -> pd.DataFrame:
    """For a candidate word-boundary term, surface category-collision tokens.

    Scans adjacent tokens (before and after the term in each keyword) and
    returns those that appear in >= min_follower_count keywords with enough
    combined volume to matter.

    Returns DataFrame sorted by collision_score descending, with example
    keywords per collision token.
    """
    if "keyword" not in semrush_df.columns or "volume" not in semrush_df.columns:
        return pd.DataFrame()

    term_lower = word_boundary_term.lower()
    matched = semrush_df[
        semrush_df["keyword"].astype(str).str.contains(
            rf"\b{re.escape(term_lower)}\b", case=False, na=False
        )
    ].copy()
    if matched.empty:
        return pd.DataFrame()

    total_volume = matched["volume"].sum()
    pat_after = re.compile(rf"\b{re.escape(term_lower)}\s+(\w+)", re.IGNORECASE)
    pat_before = re.compile(rf"(\w+)\s+\b{re.escape(term_lower)}\b", re.IGNORECASE)

    follower_volumes: dict[str, float] = {}
    follower_examples: dict[str, list[str]] = {}

    for _, row in matched.iterrows():
        kw = str(row["keyword"]).lower()
        vol = row["volume"]
        for pat in (pat_after, pat_before):
            for m in pat.finditer(kw):
                follower = m.group(1).lower()
                if follower == term_lower or len(follower) < 2:
                    continue
                follower_volumes[follower] = follower_volumes.get(follower, 0) + vol
                if follower not in follower_examples:
                    follower_examples[follower] = []
                if len(follower_examples[follower]) < 3:
                    follower_examples[follower].append(str(row["keyword"]))

    rows = []
    for follower, vol in follower_volumes.items():
        n_kws = sum(
            1 for kw in matched["keyword"].astype(str)
            if re.search(
                rf"\b{re.escape(term_lower)}\s+{re.escape(follower)}\b"
                rf"|\b{re.escape(follower)}\s+{re.escape(term_lower)}\b",
                kw, re.IGNORECASE,
            )
        )
        vol_share = vol / total_volume if total_volume else 0
        if n_kws < min_follower_count or vol_share < min_volume_share:
            continue
        rows.append({
            "follower": follower,
            "kw_count": n_kws,
            "total_volume": int(vol),
            "volume_share": round(vol_share, 3),
            "collision_score": round(n_kws * vol_share, 3),
            "examples": follower_examples[follower],
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("collision_score", ascending=False).reset_index(drop=True)
