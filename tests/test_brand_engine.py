"""Tests for engine/brand_engine.py — Task 2."""

import pandas as pd
import pytest

from engine.brand_engine import (
    classify_keywords_as_branded,
    extract_domain_from_semrush,
    split_branded_vs_non_branded,
)


class TestClassifyKeywordsAsBranded:
    @pytest.fixture
    def sample_df(self):
        return pd.DataFrame({
            "keyword": [
                "nike running shoes",
                "best running shoes",
                "nike air max",
                "running tips",
                "sportswear online",
                "cable melbourne shop",
                "cable",
            ],
            "volume": [5000, 3000, 4000, 2000, 1500, 800, 600],
            "kd": [40, 30, 45, 20, 35, 25, 20],
        })

    def test_substring_matching(self, sample_df):
        result = classify_keywords_as_branded(sample_df, ["nike"])
        assert result.loc[result["keyword"] == "nike running shoes", "is_branded"].iloc[0]
        assert result.loc[result["keyword"] == "nike air max", "is_branded"].iloc[0]
        assert not result.loc[result["keyword"] == "best running shoes", "is_branded"].iloc[0]

    def test_broad_match_substring(self, sample_df):
        # Broad match: "cable" matches any keyword containing "cable" as a substring
        df = pd.DataFrame({"keyword": ["cable car", "cablecar", "cable melbourne", "excable shoes"]})
        result = classify_keywords_as_branded(df, ["cable"])
        assert result.loc[result["keyword"] == "cable car", "is_branded"].iloc[0]
        assert result.loc[result["keyword"] == "cablecar", "is_branded"].iloc[0]
        assert result.loc[result["keyword"] == "cable melbourne", "is_branded"].iloc[0]
        assert result.loc[result["keyword"] == "excable shoes", "is_branded"].iloc[0]

    def test_case_insensitive(self):
        df = pd.DataFrame({"keyword": ["Nike Shoes", "NIKE Air", "adidas"]})
        result = classify_keywords_as_branded(df, ["nike"])
        assert result.loc[result["keyword"] == "Nike Shoes", "is_branded"].iloc[0]
        assert result.loc[result["keyword"] == "NIKE Air", "is_branded"].iloc[0]
        assert not result.loc[result["keyword"] == "adidas", "is_branded"].iloc[0]

    def test_empty_brand_terms(self, sample_df):
        result = classify_keywords_as_branded(sample_df, [])
        assert not result["is_branded"].any()
        assert len(result) == len(sample_df)

    def test_classify_empty_brand_terms_returns_all_false(self, sample_df):
        result = classify_keywords_as_branded(sample_df, [])
        assert (~result["is_branded"]).all()

    def test_classify_broad_match_substring(self):
        # Broad match: all three contain "cable" as a substring — all should be branded
        df = pd.DataFrame({"keyword": ["excable shoes", "cable shoes", "cablecar"]})
        result = classify_keywords_as_branded(df, ["cable"])
        assert result.loc[result["keyword"] == "excable shoes", "is_branded"].iloc[0]
        assert result.loc[result["keyword"] == "cable shoes", "is_branded"].iloc[0]
        assert result.loc[result["keyword"] == "cablecar", "is_branded"].iloc[0]

    def test_classify_case_insensitive(self):
        df = pd.DataFrame({"keyword": ["Nike Shoes", "NIKE Air", "adidas"]})
        result = classify_keywords_as_branded(df, ["nike"])
        assert result.loc[result["keyword"] == "Nike Shoes", "is_branded"].iloc[0]
        assert result.loc[result["keyword"] == "NIKE Air", "is_branded"].iloc[0]
        assert not result.loc[result["keyword"] == "adidas", "is_branded"].iloc[0]

    def test_classify_multiple_brand_terms(self, sample_df):
        result = classify_keywords_as_branded(sample_df, ["nike", "cable"])
        branded = result[result["is_branded"]]["keyword"].tolist()
        assert "nike running shoes" in branded
        assert "cable melbourne shop" in branded

    def test_multiple_brand_terms(self, sample_df):
        result = classify_keywords_as_branded(sample_df, ["nike", "cable"])
        branded = result[result["is_branded"]]["keyword"].tolist()
        assert "nike running shoes" in branded
        assert "cable melbourne shop" in branded

    def test_preserves_original_columns(self, sample_df):
        result = classify_keywords_as_branded(sample_df, ["nike"])
        assert "volume" in result.columns
        assert "kd" in result.columns
        assert len(result) == len(sample_df)

    def test_no_keyword_column(self):
        df = pd.DataFrame({"search_term": ["nike shoes"], "volume": [100]})
        result = classify_keywords_as_branded(df, ["nike"])
        assert not result["is_branded"].any()


class TestSplitBrandedVsNonBranded:
    def test_split_counts(self):
        df = pd.DataFrame({
            "keyword": ["brand kw", "generic kw", "brand product"],
            "is_branded": [True, False, True],
        })
        branded, non_branded = split_branded_vs_non_branded(df)
        assert len(branded) == 2
        assert len(non_branded) == 1

    def test_split_preserves_total_count(self):
        df = pd.DataFrame({
            "keyword": ["brand kw", "generic kw", "brand product", "other"],
            "is_branded": [True, False, True, False],
        })
        branded, non_branded = split_branded_vs_non_branded(df)
        assert len(branded) + len(non_branded) == len(df)

    def test_all_non_branded(self):
        df = pd.DataFrame({
            "keyword": ["generic a", "generic b"],
            "is_branded": [False, False],
        })
        branded, non_branded = split_branded_vs_non_branded(df)
        assert len(branded) == 0
        assert len(non_branded) == 2

    def test_missing_is_branded_raises(self):
        df = pd.DataFrame({"keyword": ["test"]})
        with pytest.raises(ValueError, match="is_branded"):
            split_branded_vs_non_branded(df)


class TestExtractDomainFromSemrush:
    def test_extract_domain_picks_most_common(self):
        df = pd.DataFrame({
            "keyword": ["kw1", "kw2", "kw3", "kw4"],
            "url": [
                "https://example.com/page1",
                "https://example.com/page2",
                "https://example.com/page3",
                "https://other.com/page1",
            ],
        })
        assert extract_domain_from_semrush(df) == "example.com"

    def test_extract_domain_returns_none_when_no_url_column(self):
        df = pd.DataFrame({"keyword": ["kw1", "kw2"], "volume": [100, 200]})
        assert extract_domain_from_semrush(df) is None

    def test_extract_domain_returns_none_when_empty_urls(self):
        df = pd.DataFrame({"url": [None, None, ""]})
        assert extract_domain_from_semrush(df) is None
