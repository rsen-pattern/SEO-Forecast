# Dead-Code Scan Report

**Branch:** `chore/dead-code-cleanup`  
**Date:** 2026-05-04  
**Tools used:** `vulture 2.x`, `ripgrep`, manual inspection  
**Note:** Vulture was run with `--min-confidence 60` and its attribute-level findings for openpyxl objects (`.font`, `.alignment`, etc.) are systematically false positives — openpyxl's write-only style API uses attribute assignment that static analysis cannot trace. Those are excluded below.

---

## Files with no inbound imports

| File | Evidence | Confidence | Action |
|------|----------|------------|--------|
| `exporters/pattern_template.py` | Only reference is in the module's own docstring example (line 16). No import anywhere else in the codebase. | **High** | Delete — superseded by `utils/forecast_grid.py` |
| `scripts/run_forecast_e2e.py` | Not imported anywhere; is a standalone CLI script. Also imports `utils.data_loader.load_semrush_keywords` which does **not exist** in that module (would crash at runtime). | **High** | Keep if CLI is wanted, but fix the broken import; otherwise delete |
| `engine/v5/__init__.py` | Empty file (`"""v5 engine modules..."""` only). Needed for Python package discovery. | **High** | Keep (package marker) |
| `exporters/__init__.py` | Empty. Needed for package discovery. | **High** | Keep (package marker) |

---

## Functions / classes with no callers (within the repo)

### Confirmed dead (no callers in pages, engine, or utils — tests-only or zero)

| Symbol | File | Line | Evidence | Confidence | Action |
|--------|------|------|----------|------------|--------|
| `run_positional_forecast()` | `engine/positional_engine.py` | 151 | Only called in tests. Pages and `scenario_engine.py` all use `run_positional_forecast_mc`. Predates v3 Monte Carlo rewrite. | **High** | Delete; update tests that call it |
| `combined_forecast_chart()` | `utils/chart_builder.py` | 178 | Zero callers. Superseded by `combined_three_stream_chart()`. | **High** | Delete |
| `combined_scenario_chart()` | `utils/chart_builder.py` | 253 | Zero callers. Similar to above. | **High** | Delete |
| `populate_template()` | `exporters/pattern_template.py` | 144 | Zero callers outside the file. See Files section. | **High** | Delete with file |
| `add_top_keywords_sheet()` | `exporters/keyword_sheets.py` | 74 | Only referenced in the file's own docstring example (lines 10–12). Zero runtime callers. | **High** | Delete |
| `add_keyword_movement_summary()` | `exporters/keyword_sheets.py` | 164 | Same as above — docstring example only. | **High** | Delete |
| `_call_bifrost()` | `engine/ai_engine.py` | 100 | Defined but never called. `utils/bifrost.py` provides the live path. | **High** | Delete |
| `add_dynamic_revenue()` | `engine/revenue_engine.py` | 54 | Called only in tests (`test_engines.py:853,860`). No production callers. | **Medium** | Investigate — keep if the test intent is still live; otherwise delete both |
| `calculate_yoy()` | `engine/historical_engine.py` | 491 | Zero callers anywhere. `calculate_growth_rates()` is the live equivalent used by the Strategy page. | **High** | Delete |
| `methodology_snapshot_to_human_readable()` | `engine/snapshot_engine.py` | 431 | Called only in `tests/test_upgrade.py:483` and `scripts/run_forecast_e2e.py`. No page uses it. | **Medium** | Keep — still surfaced via CLI script and upgrade test |
| `apply_seasonality()` | `engine/seasonality_engine.py` | 32 | Only called in tests. Engines use per-stream seasonality parameters directly (v4 change). | **Medium** | Delete; retire the 3 tests that call it |
| `build_campaign_list()` | `engine/seasonality_engine.py` | 397 | Only called in `test_engines.py:885–888`. | **Medium** | Delete with test |

### Tests-only (API still exported, no production caller)

| Symbol | File | Line | Evidence | Confidence | Action |
|--------|------|------|----------|------------|--------|
| `learn_movement_from_history()` | `engine/positional_engine.py` | 34 | Only called in 4 `test_engines.py` tests. `learn_movement_from_history_v2()` (line 65) is the live version called by the positional page. | **Medium** | Keep — v2 documents intent; add deprecation note pointing to v2 |
| `apply_attention_curve()` | `engine/positional_engine.py` | 217 | Only called in `test_engines.py:1171`. Used internally by `run_positional_forecast_mc` indirectly, but the standalone function is not called externally. | **Low** | Keep (internal helper used by MC loop; vulture false-positive here) |
| `project_decayed_baseline()` | `engine/decay_engine.py` | 121 | Only called in `test_engines.py:1382`. No production caller. | **Medium** | Delete with test |
| `apply_industry_bias()` | `engine/seasonality_engine.py` | 332 | Only in `test_engines.py:2077`. CLAUDE.md mentions it as live API. | **Low** | Keep — documented in CLAUDE.md; low risk |
| `get_fallback_chain()` | `engine/ai_engine.py` | 42 | Called in 2 tests. `utils/bifrost.py` has `_get_fallback_chain()` (private) as the live version. | **Medium** | Keep — tests legitimately verify the public API; or redirect to bifrost |

---

## Session state keys defined in utils/session.py but never read

The following constants are defined but their string values are never used as raw string literals in the codebase. They are **all imported by name** and used via the constant, which is correct — this section is therefore **all false positives** from the literal scan. Confirmed live via import-count analysis:

| Key | Confirmed live via |
|-----|-------------------|
| `BIFROST_API_KEY` | `utils/sidebar.py`, `pages/inputs/roadmap.py`, etc. |
| `BIFROST_MODEL` | `pages/inputs/roadmap.py`, `utils/sidebar.py` |
| `DETECTED_BRAND_TERMS` | `pages/inputs/semrush.py` |
| `HIST_N_MONTHS` | `pages/forecasts/historical.py` |
| `LEARNED_SEASONALITY` | `pages/inputs/ga4.py` |
| `ROADMAP_AI_CACHE` | `pages/inputs/roadmap.py` |
| `ROADMAP_BUNDLES` | `pages/inputs/roadmap.py`, `pages/forecasts/strategy.py` |
| `ROADMAP_CONTENT_PLANS` | same |
| `ROADMAP_DATA` | `pages/inputs/roadmap.py`, `semrush.py`, `ga4.py` |
| `ROADMAP_FILE_EXT` | `pages/inputs/roadmap.py` |
| `ROADMAP_RAW_BYTES` | same |
| `ROADMAP_USED_MODEL` | same |
| `SCENARIO_PRESETS` | `strategy.py`, `deliverables.py` |
| `SCENARIO_PRESETS_EDITED` | same |
| `SCENARIO_RESULTS` | same |
| `SESSION_COST_AUD` | `utils/sidebar.py` |

**One genuine orphan:**

| Key | File | Line | Evidence | Confidence | Action |
|-----|------|------|----------|------------|--------|
| `KW_NEW` | `utils/session.py` | 17 | Imported by `pages/inputs/semrush.py` but semrush.py only **writes** `st.session_state[KW_NEW]`. No page ever **reads** `KW_NEW`. The key was the predecessor to filtering by position. | **Medium** | Investigate — if no page reads it, remove from session.py and the semrush.py write |

---

## Prompts in `prompts/` with no caller in `engine/`

All prompts are loaded via `engine/ai_engine.py::_load_prompt(name)` — a dynamic string lookup. Static analysis cannot trace these. Manual cross-reference:

| Prompt file | Called by | Status |
|-------------|-----------|--------|
| `prompts/detect_brand.txt` | `engine/ai_engine.py` (via feature `"detect_brand"`) | **Live** |
| `prompts/cluster_keywords.txt` | `engine/ai_engine.py` (via feature `"cluster_keywords"`) | **Live** |
| `prompts/check_cannibalization.txt` | `engine/ai_engine.py` (via feature `"check_cannibalization"`) | **Live** |
| `prompts/content_roadmap.txt` | `engine/ai_engine.py::generate_content_roadmap()` (line 200) | **Live** |
| `prompts/transform_data.txt` | `engine/ai_engine.py` (via feature `"transform_data"`) | **Live** |
| `prompts/extract_roadmap.txt` | `engine/roadmap_ai_engine.py` | **Live** |
| `prompts/extract_roadmap_full.txt` | `engine/roadmap_ai_engine.py` | **Live** |
| `prompts/enrich_roadmap.txt` | `engine/roadmap_ai_engine.py` | **Live** |
| `prompts/summarise_roadmap_strategy.txt` | `engine/roadmap_ai_engine.py` | **Live** |

**No orphan prompts found.**

---

## Streamlit pages not referenced by `app.py`'s `st.navigation`

All 11 page files are registered in `app.py`. No orphan page files exist. (The regex parse above confirmed this.)

---

## Tests testing removed code

| Test file | Lines | What it tests | Status | Action |
|-----------|-------|---------------|--------|--------|
| `tests/test_brand_engine.py` | all | `engine/brand_engine.py` — the v2-era keyword brand classifier | **Suspect** — `brand_engine.py` is only referenced by this test. `engine/brand_classifier.py` (v4 shim → v5) is the live path used by pages. | Delete if `brand_engine.py` is deleted |
| `tests/test_engines.py` | 1049–1066 | `run_positional_forecast()` (v2 legacy) | **Orphan** if `run_positional_forecast` is deleted | Delete those test cases |
| `tests/test_engines.py` | 869–888 | `apply_seasonality()`, `build_campaign_list()` | Orphan if those functions are deleted | Delete alongside |
| `tests/test_engines.py` | 1382–1389 | `project_decayed_baseline()` | Orphan if deleted | Delete alongside |

---

## Deprecated / legacy paths — confirmed status

### `utils/roadmap_loader.py` — legacy scalar loader

**Status: Live (needed)**

Called by:
- `pages/inputs/roadmap.py:16` — AI extraction error fallback
- `engine/roadmap_ai_engine.py:477` — `parse_param_table` / `parse_task_table` inside `load_roadmap_v2` dispatch
- `tests/test_roadmap_loader.py` — CI test coverage

The docstring correctly labels it legacy, but the fallback path and `load_roadmap_v2` dispatcher both depend on it. **Do not delete.**

---

### `engine/brand_engine.py` — v2 brand classifier

**Status: Dead (only test coverage, no production caller)**

No page or engine imports `brand_engine.py`. The live path is:
- `engine/brand_classifier.py` → delegates to `engine/v5/brand_classifier.py`
- `pages/inputs/semrush.py` and `pages/forecasts/new_content.py` import from `engine.brand_classifier`

`test_brand_engine.py` is the only caller. Safe to delete both `engine/brand_engine.py` and `tests/test_brand_engine.py`.

**Action: Delete both.**

---

### `engine/historical_engine.run_historical_forecast` — v3 legacy

**Status: Live (still used by Historical page)**

`pages/forecasts/historical.py:356` calls `run_historical_forecast()` when the user has selected method-specific overrides via the UI. `run_historical_forecast_v4` is the default path; the v3 function remains the fallback for the manual method selector.

**Action: Keep — add a deprecation comment noting v4 is preferred.**

---

### `exporters/pattern_template.py`

**Status: Dead**

The only reference is in the module's own docstring example (line 16). No page, engine, utils, or test imports it. The deliverables flow now uses `utils/forecast_grid.py`.

**Action: Delete.**

---

### `assets/Forecast System.html` — not present in current tree

Scanned `assets/` directory — contains only:
- `sample-ga4-organic.xlsx`
- `sample-keywords.csv`
- `sample-semrush-export.xlsx`
- `sample-traffic.csv`

No `.html` file exists. References to `Forecast System.html` and `10_Forecast_Dashboard.py` mentioned in the brief were from a prior version and have already been removed from the repo. **Nothing to action.**

---

## Additional findings (not in original scope)

### `utils/design_tokens.py` — unused colour constants

| Constant | Line | Evidence | Confidence | Action |
|----------|------|----------|------------|--------|
| `PRIMARY_DARK` | 13 | Zero callers across codebase | **High** | Delete |
| `INTENT_INFORMATIONAL` | 29 | Zero callers (Strategy page hardcodes `INTENT_COLORS` dict inline) | **High** | Delete |
| `INTENT_COMMERCIAL` | 30 | Same | **High** | Delete |
| `INTENT_TRANSACTIONAL` | 31 | Same | **High** | Delete |
| `INTENT_NAVIGATIONAL` | 32 | Same | **High** | Delete |
| `SCENARIO_CONSERVATIVE` | 35 | Zero callers (Strategy page uses inline dict) | **High** | Delete |
| `SCENARIO_MODERATE` | 36 | Same | **High** | Delete |
| `SCENARIO_AGGRESSIVE` | 37 | Same | **High** | Delete |

> **Note:** These colours should be consolidated into `design_tokens.py` and the inline dicts in Strategy/New Content pages should import from there. That's a refactor, not a deletion — log separately.

---

### `engine/assumptions.py` — `Provenance` type alias

| Symbol | Line | Evidence | Confidence | Action |
|--------|------|----------|------------|--------|
| `Provenance = Literal[...]` | 31 | Defined but never imported outside the module. Used only in internal docstring commentary. | **Medium** | Keep — useful for documentation; annotate exported functions with it |

---

### `engine/ai_engine.py` — `_call_bifrost()` dead private function

`_call_bifrost()` (line 100) is a private function never called. The live path is `utils/bifrost.py::call()`. This appears to be an unmigrated stub from before `utils/bifrost.py` was extracted.

**Action: Delete.**

---

### `scripts/run_forecast_e2e.py` — broken import

Line 61: `from utils.data_loader import load_semrush_keywords` — this function does **not exist** in `utils/data_loader.py` (which has `load_keywords` and `load_traffic`). The script would crash on import. The correct import is likely `from utils.keyword_loader import load_keyword_portfolio`.

**Action: Fix import before any CLI use; or delete if the CLI script is not maintained.**

---

## Summary table

| Item | File | Confidence | Recommended action |
|------|------|------------|--------------------|
| `exporters/pattern_template.py` | whole file | High | Delete |
| `engine/brand_engine.py` | whole file | High | Delete |
| `tests/test_brand_engine.py` | whole file | High | Delete (with brand_engine) |
| `engine/positional_engine.run_positional_forecast` | :151 | High | Delete; update tests |
| `utils/chart_builder.combined_forecast_chart` | :178 | High | Delete |
| `utils/chart_builder.combined_scenario_chart` | :253 | High | Delete |
| `exporters/keyword_sheets.add_top_keywords_sheet` | :74 | High | Delete |
| `exporters/keyword_sheets.add_keyword_movement_summary` | :164 | High | Delete |
| `engine/ai_engine._call_bifrost` | :100 | High | Delete |
| `engine/historical_engine.calculate_yoy` | :491 | High | Delete |
| `engine/seasonality_engine.apply_seasonality` | :32 | Medium | Delete; retire 2 tests |
| `engine/seasonality_engine.build_campaign_list` | :397 | Medium | Delete; retire 1 test |
| `engine/decay_engine.project_decayed_baseline` | :121 | Medium | Delete; retire test |
| `engine/revenue_engine.add_dynamic_revenue` | :54 | Medium | Investigate; likely delete |
| `utils/design_tokens.PRIMARY_DARK` | :13 | High | Delete |
| `utils/design_tokens.INTENT_*` (4 vars) | :29–32 | High | Delete |
| `utils/design_tokens.SCENARIO_*` (3 vars) | :35–37 | High | Delete |
| `utils/session.KW_NEW` | :17 | Medium | Investigate write-only key |
| `engine/assumptions.Provenance` | :31 | Medium | Keep (annotation value) |
| `engine/historical_engine.run_historical_forecast` | :631 | Low | Keep — Historical page UI uses it |
| `scripts/run_forecast_e2e.py` | :61 (broken import) | High | Fix `load_semrush_keywords` → `load_keyword_portfolio` |
| `utils/roadmap_loader.py` | whole file | — | **Keep** — active fallback path |
| All prompts in `prompts/` | — | — | **Keep** — all live |
| All pages | — | — | **Keep** — all registered in app.py |
