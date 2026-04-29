"""Cluster-based new content forecast — v5.

Replaces the flat per-post traffic assumption with a clustered, data-driven
estimate. Three stages:

  1. Identify content opportunity keywords from SEMrush:
     informational intent, poor position (>= min_position), non-branded.

  2. Cluster these into topical groups via TF-IDF + K-means.

  3. Forecast capture per cluster:
        capture = median_cluster_volume × adjacent_multiplier × rank_probability(da, kd)
     bounded between capture_floor and capture_ceiling.

When clustering is unavailable (small portfolio, no informational keywords),
fall back to industry-keyed defaults from published benchmark research.

Math discipline — Cable Melbourne validation showed:
  - Per-post capture ceiling 600 prevents over-estimates from large catch-all clusters.
  - Per-post capture floor 50 prevents near-zero estimates from tiny clusters.
  - adjacent_variants_multiplier=3.0 represents target KW + ~3x long-tail variants.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.maturation_curve import maturation_schedule

# Industry-keyed per-post mature monthly session ranges at DA 40-50.
# From published benchmarks (Backlinko, Animalz, Ahrefs studies).
INDUSTRY_PER_POST_RANGES: dict[str, tuple[int, int]] = {
    "b2b_saas":          (200, 500),
    "apparel_fashion":   (150, 400),
    "home_garden":       (300, 800),
    "finance_legal":     (100, 300),
    "health_wellness":   (200, 600),
    "tech_accessories":  (250, 700),
    "ecommerce_general": (200, 500),
    "default":           (200, 500),
}


def cluster_content_opportunities(
    semrush_df: pd.DataFrame,
    brand_classifier=None,
    min_position: int = 21,
    max_position: int = 100,
    intent_filter: str = "informational",
    min_cluster_size: int = 3,
    target_n_clusters: int | None = None,
    max_features: int = 200,
    random_state: int = 42,
) -> pd.DataFrame:
    """Cluster informational, poor-position, non-branded keywords by topic.

    Requires scikit-learn (sklearn). Returns an empty DataFrame when there are
    too few keywords to cluster or sklearn is not installed.

    Returns DataFrame with columns:
        cluster_id, cluster_label (auto-named from centroid tokens),
        keyword_count, total_volume, median_keyword_volume, mean_kd,
        mean_position, top_volume_keyword, member_keywords (list).
    Sorted by total_volume descending.
    """
    try:
        from sklearn.cluster import KMeans
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return pd.DataFrame()

    df = semrush_df.copy()

    if brand_classifier is not None and "keyword" in df.columns:
        df = df[~df["keyword"].apply(brand_classifier)]

    df = df.dropna(subset=["position", "volume"])
    df["position"] = pd.to_numeric(df["position"], errors="coerce")
    df = df[(df["position"] >= min_position) & (df["position"] <= max_position)]

    # Intent filter — accepts either 'intent' or 'keyword_intents' column
    for col in ("intent", "keyword_intents"):
        if col in df.columns and intent_filter:
            df = df[df[col].astype(str).str.contains(intent_filter, case=False, na=False)]
            break

    if len(df) < 20:
        return pd.DataFrame()

    if target_n_clusters is None:
        target_n_clusters = min(20, max(5, len(df) // 30))

    vec = TfidfVectorizer(
        max_features=max_features,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=2,
    )
    try:
        X = vec.fit_transform(df["keyword"].astype(str))
    except ValueError:
        return pd.DataFrame()

    n_clusters = min(target_n_clusters, max(2, X.shape[0] // 3))
    km = KMeans(n_clusters=n_clusters, n_init="auto", random_state=random_state)
    df = df.assign(cluster_id=km.fit_predict(X))

    feature_names = vec.get_feature_names_out()
    rows = []
    for cid in sorted(df["cluster_id"].unique()):
        members = df[df["cluster_id"] == cid]
        if len(members) < min_cluster_size:
            continue

        centroid_idx = km.cluster_centers_[cid].argsort()[-3:][::-1]
        label = " · ".join(feature_names[i] for i in centroid_idx)
        kd_vals = members["kd"].fillna(50) if "kd" in members.columns else pd.Series([50] * len(members))

        rows.append({
            "cluster_id": int(cid),
            "cluster_label": label,
            "keyword_count": len(members),
            "total_volume": int(members["volume"].sum()),
            "median_keyword_volume": float(members["volume"].median()),
            "mean_kd": float(kd_vals.mean()),
            "mean_position": float(members["position"].mean()),
            "top_volume_keyword": str(
                members.sort_values("volume", ascending=False).iloc[0]["keyword"]
            ),
            "member_keywords": members["keyword"].astype(str).tolist(),
        })

    if not rows:
        return pd.DataFrame()

    return (
        pd.DataFrame(rows)
        .sort_values("total_volume", ascending=False)
        .reset_index(drop=True)
    )


def _ranking_probability(da: float, kd: float) -> float:
    """Logistic probability that a new page reaches top-10 within ~9 months."""
    return float(np.clip((da - kd + 50) / 100, 0.05, 0.95))


def forecast_cluster_capture(
    cluster_df: pd.DataFrame,
    da: int,
    capture_ceiling: int = 600,
    capture_floor: int = 50,
    adjacent_variants_multiplier: float = 3.0,
    max_posts_per_cluster: int = 4,
) -> pd.DataFrame:
    """Add per-post capture estimate to each cluster row.

    Capture is anchored to the median keyword volume in the cluster × the
    adjacent variants multiplier × ranking probability. Bounded by
    capture_floor/ceiling to prevent runaway over- or under-estimates.

    Returns cluster_df with additional columns:
        rank_probability, capture_per_post, max_useful_posts.
    """
    out = cluster_df.copy()
    out["rank_probability"] = out["mean_kd"].apply(
        lambda kd: _ranking_probability(da, kd)
    )

    if "median_keyword_volume" not in out.columns:
        out["median_keyword_volume"] = out["total_volume"] / out["keyword_count"].clip(lower=1)

    base = out["median_keyword_volume"] * adjacent_variants_multiplier
    out["capture_per_post"] = (base * out["rank_probability"]).clip(
        lower=capture_floor, upper=capture_ceiling
    ).astype(int)
    out["max_useful_posts"] = max_posts_per_cluster
    return out


def forecast_cluster_traffic_over_horizon(
    cluster_df: pd.DataFrame,
    da: int,
    months: int = 12,
    posts_per_month: int = 2,
    capture_ceiling: int = 600,
    capture_floor: int = 50,
    adjacent_variants_multiplier: float = 3.0,
    max_posts_per_cluster: int = 4,
    maturation_tier: str = "Moderate",
    seasonality: dict | None = None,
    forecast_start_month: int | None = None,
    seed: int = 42,
) -> dict:
    """Distribute post publications across clusters and model traffic maturation.

    Posts are greedily allocated to the cluster with highest remaining marginal
    capture (up to max_posts_per_cluster per cluster). For each scheduled post,
    a rank-probability Bernoulli draw decides whether it achieves traffic, then
    an S-curve maturation schedule accumulates monthly contributions.

    Args:
        cluster_df: Output of cluster_content_opportunities() (rows = clusters).
        da: Estimated domain authority (from da_estimator or user input).
        months: Forecast horizon in months.
        posts_per_month: Posts published per month.
        seasonality: Dict {month_num: {traffic_mod: float}} — applied to output.
        forecast_start_month: Calendar month (1-12) of horizon month 1.
        seed: RNG seed for reproducibility.

    Returns:
        {
            "per_cluster": DataFrame with posts_assigned and m12_traffic columns,
            "monthly_total": np.ndarray of monthly total sessions (length = months),
            "publication_calendar": list of (month_idx, cluster_idx, capture, prob) tuples,
        }
    """
    rng = np.random.default_rng(seed)

    enriched = forecast_cluster_capture(
        cluster_df, da,
        capture_ceiling=capture_ceiling,
        capture_floor=capture_floor,
        adjacent_variants_multiplier=adjacent_variants_multiplier,
        max_posts_per_cluster=max_posts_per_cluster,
    ).sort_values("capture_per_post", ascending=False).reset_index(drop=True)

    total_posts = months * posts_per_month
    monthly_total = np.zeros(months)

    if enriched.empty or enriched["capture_per_post"].sum() <= 0:
        return {
            "per_cluster": pd.DataFrame(),
            "monthly_total": monthly_total,
            "publication_calendar": [],
        }

    # Greedy post allocation
    enriched["posts_assigned"] = 0
    posts_left = total_posts
    while posts_left > 0:
        marginal = enriched.apply(
            lambda r: r["capture_per_post"] if r["posts_assigned"] < r["max_useful_posts"] else 0,
            axis=1,
        )
        if marginal.max() <= 0:
            break
        idx = marginal.idxmax()
        enriched.at[idx, "posts_assigned"] += 1
        posts_left -= 1

    # Build publication calendar: spread each cluster's allocation across months
    cluster_remaining = enriched["posts_assigned"].tolist()
    pub_calendar: list[tuple] = []

    for m in range(months):
        for _ in range(posts_per_month):
            choices = [i for i, n in enumerate(cluster_remaining) if n > 0]
            if not choices:
                break
            idx = max(choices, key=lambda i: cluster_remaining[i])
            cluster_remaining[idx] -= 1
            row = enriched.iloc[idx]
            pub_calendar.append((m, idx, int(row["capture_per_post"]), float(row["rank_probability"])))

    # Monte Carlo: each post either ranks (Bernoulli) then accrues S-curve maturation
    cluster_traffic = {i: np.zeros(months) for i in range(len(enriched))}
    for m_idx, c_idx, capture, prob in pub_calendar:
        if rng.random() < prob:
            schedule = maturation_schedule(maturation_tier, months, m_idx + 1)
            contribution = capture * schedule
            cluster_traffic[c_idx] += contribution
            monthly_total += contribution

    # Seasonality
    if seasonality and forecast_start_month is not None:
        season_mults = np.array([
            1.0 + seasonality.get(((forecast_start_month - 1 + m) % 12) + 1, {}).get("traffic_mod", 0.0)
            for m in range(months)
        ])
        monthly_total *= season_mults
        for i in cluster_traffic:
            cluster_traffic[i] *= season_mults

    enriched["m12_traffic"] = [int(cluster_traffic[i].sum()) for i in range(len(enriched))]

    return {
        "per_cluster": enriched,
        "monthly_total": monthly_total.astype(int),
        "publication_calendar": pub_calendar,
    }


def fallback_per_post_traffic(
    industry_key: str = "default",
) -> tuple[int, int, str]:
    """Return (low, high, rationale) for an industry-keyed per-post fallback.

    Used when clustering is unavailable (too few keywords or sklearn missing).
    """
    lo, hi = INDUSTRY_PER_POST_RANGES.get(
        industry_key, INDUSTRY_PER_POST_RANGES["default"]
    )
    return lo, hi, (
        f"Industry default for '{industry_key}': {lo}–{hi} mature monthly sessions/post "
        f"at DA 40–50 (from public benchmarks). Use clustering when keyword data permits."
    )
