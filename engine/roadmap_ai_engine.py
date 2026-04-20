"""AI-powered roadmap extraction engine (v2).

Converts uploaded xlsx/csv roadmap files into structured per-focus-area bundles.
Supports two extraction paths:
  1. Pattern native multi-sheet SOW format — deterministic parser, no AI cost
  2. Generic AI extraction — Bi Frost LLM call with optional NL correction

Entry point: load_roadmap_v2(client, raw_bytes, filename, ...) → (bundle, used_model)

Pure Python — no Streamlit imports. Session-state caching handled by caller.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime, timezone
from string import Template

import pandas as pd

from engine.ai_engine import _load_prompt, _parse_llm_json, generate_with_fallback

# ── Schema constants ──────────────────────────────────────────────────────────

ROADMAP_BUNDLE_SCHEMA: dict = {
    "schema_version": "1.0",
    "extraction_date": "<ISO timestamp>",
    "source_summary": {
        "total_tasks_detected": 0,
        "focus_areas_detected": [],
        "timeline_months_covered": 12,
        "parsing_confidence": 0.9,
    },
    "per_focus": {
        "content": {
            "effort_level": "moderate",
            "monthly_hours": 0.0,
            "cadence": 0,
            "task_count": 0,
            "tasks": [
                {"name": "<task name>", "hours": 0, "occurrence": "<Monthly|Quarterly|…>", "contribution": "<primary|supporting>"},
            ],
        },
        "technical": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "on_page": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "off_page": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "local": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "analytics": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "strategy": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
    },
    "timeline": {
        "months_covered": 12,
        "phasing_notes": "",
        "has_launch_dates": False,
    },
    "global_rollup": {
        "total_monthly_hours": 0.0,
        "effort_level": "moderate",
        "maintenance_coverage": 0.0,
        "content_cadence": 0,
        "positional_effort_level": "moderate",
    },
    "recommendations": [
        {"severity": "info", "message": "<recommendation text>"},
    ],
    "gaps": [
        {"focus_area": "<focus area>", "note": "<gap note>"},
    ],
}

ROADMAP_BUNDLE_SCHEMA_V2: dict = {
    "schema_version": "2.0",
    "extraction_date": "<ISO timestamp>",
    "source_format": "pattern_native",  # or "ai_extracted"
    "client_metadata": {
        "client_name": "",
        "industry": "Unknown",
        "retainer_aud_monthly": 0.0,
    },
    "source_summary": {
        "total_tasks_detected": 0,
        "focus_areas_detected": [],
        "timeline_months_covered": 12,
        "parsing_confidence": 0.9,
    },
    "per_focus": {
        "content": {
            "effort_level": "moderate",
            "monthly_hours": 0.0,
            "cadence": 0,
            "task_count": 0,
            "tasks": [],
        },
        "technical": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "on_page": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "off_page": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "local": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "analytics": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
        "strategy": {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []},
    },
    "timeline": {
        "months_covered": 12,
        "strategy_restart_month": None,
        "phasing_notes": "",
        "has_launch_dates": False,
    },
    "content_plan": [
        {
            "url": "<target URL>",
            "keyword": "<primary keyword>",
            "page_type": "<new|optimise>",
            "publish_month": 1,
            "notes": "",
        }
    ],
    "global_rollup": {
        "total_monthly_hours": 0.0,
        "effort_level": "moderate",
        "maintenance_coverage": 0.0,
        "content_cadence": 0,
        "positional_effort_level": "moderate",
    },
    "recommendations": [],
    "gaps": [],
}


# ── Format detection ──────────────────────────────────────────────────────────

# Sheet name patterns that identify a Pattern-native multi-sheet SOW workbook
_PATTERN_NATIVE_SHEETS = {
    "breakdown", "1. client", "2. consulting", "3. technical",
    "4. content", "5. links",
}

_FOCUS_SHEET_KEYWORDS = {
    "content": {"content", "copywriting", "articles"},
    "technical": {"technical", "tech", "dev"},
    "on_page": {"on-page", "on page", "onpage"},
    "off_page": {"off-page", "off page", "links", "link building"},
    "local": {"local", "gmb"},
    "analytics": {"analytics", "reporting", "tracking"},
    "strategy": {"strategy", "planning"},
}


def detect_roadmap_format(raw_bytes: bytes, file_extension: str) -> str:
    """Classify the uploaded roadmap file format.

    Returns:
        "pattern_native" — Pattern multi-sheet SOW workbook
        "task_table"     — Single-sheet CSV/xlsx with Task/Focus/Occurrence/Hours columns
        "param_table"    — Single-sheet CSV/xlsx with cadence/effort_level/maintenance_coverage
        "unknown"        — Cannot determine; caller should fall back to full AI extraction
    """
    ext = file_extension.lower().lstrip(".")

    if ext in ("xlsx", "xls"):
        try:
            buf = io.BytesIO(raw_bytes)
            xl = pd.ExcelFile(buf, engine="openpyxl")
            sheet_names_lower = {s.lower().strip() for s in xl.sheet_names}
            matches = sheet_names_lower & _PATTERN_NATIVE_SHEETS
            if len(matches) >= 4:
                return "pattern_native"
        except Exception:
            pass

    # Try reading as a single-sheet table
    try:
        df = _read_single_sheet(raw_bytes, file_extension)
        cols_lower = {c.lower().strip() for c in df.columns}

        # Task table: has Task + Focus columns
        has_task = any("task" in c for c in cols_lower)
        has_focus = any(c in ("focus", "area", "category") for c in cols_lower)
        if has_task and has_focus:
            return "task_table"

        # Param table: has cadence or effort_level
        has_cadence = any("cadence" in c for c in cols_lower)
        has_effort = any("effort" in c for c in cols_lower)
        if has_cadence or has_effort:
            return "param_table"
    except Exception:
        pass

    return "unknown"


# ── Pattern native parser ─────────────────────────────────────────────────────

# Monthly-equivalent multipliers by occurrence keyword
_OCCURRENCE_MULTIPLIERS = {
    "monthly": 1.0,
    "bi-monthly": 0.5,
    "fortnightly": 0.5,
    "quarterly": 1 / 3,
    "bi-annual": 1 / 6,
    "biannual": 1 / 6,
    "semi-annual": 1 / 6,
    "annual": 1 / 12,
    "annually": 1 / 12,
    "one-off": 1 / 12,
    "once": 1 / 12,
    "weekly": 4.0,
}

_EFFORT_BANDS = [(8, "light"), (20, "moderate")]


def _classify_hours(monthly_hours: float) -> str:
    for threshold, label in _EFFORT_BANDS:
        if monthly_hours <= threshold:
            return label
    return "aggressive"


def _monthly_hours_from_df(df: pd.DataFrame) -> dict[str, float]:
    """Aggregate monthly-equivalent hours per focus area from a task table."""
    result: dict[str, float] = {}
    if df.empty:
        return result

    # Normalise column names
    cols_lower = {c.lower().strip(): c for c in df.columns}

    focus_col = next((cols_lower[c] for c in cols_lower if c in ("focus", "area", "category")), None)
    hours_col = next((cols_lower[c] for c in cols_lower if "hour" in c), None)
    occ_col = next((cols_lower[c] for c in cols_lower if "occurrence" in c or "frequency" in c), None)

    if focus_col is None or hours_col is None:
        return result

    for _, row in df.iterrows():
        focus_raw = str(row.get(focus_col, "")).lower().strip()
        hours_raw = row.get(hours_col)
        occurrence_raw = str(row.get(occ_col, "monthly") if occ_col else "monthly").lower().strip()

        try:
            hours = float(hours_raw)
        except (TypeError, ValueError):
            continue

        mult = _OCCURRENCE_MULTIPLIERS.get(occurrence_raw, 1.0)
        monthly_h = hours * mult

        # Map focus area label to canonical key
        canon = _canonicalize_focus(focus_raw)
        result[canon] = result.get(canon, 0.0) + monthly_h

    return result


_FOCUS_ALIASES: dict[str, str] = {
    "content": "content",
    "copywriting": "content",
    "article": "content",
    "editorial": "content",
    "technical": "technical",
    "tech": "technical",
    "dev": "technical",
    "development": "technical",
    "on-page": "on_page",
    "on page": "on_page",
    "onpage": "on_page",
    "on_page": "on_page",
    "seo": "on_page",
    "off-page": "off_page",
    "off page": "off_page",
    "offpage": "off_page",
    "off_page": "off_page",
    "links": "off_page",
    "link building": "off_page",
    "digital pr": "off_page",
    "pr": "off_page",
    "local": "local",
    "gmb": "local",
    "analytics": "analytics",
    "reporting": "analytics",
    "tracking": "analytics",
    "strategy": "strategy",
    "planning": "strategy",
    "consulting": "strategy",
}


def _canonicalize_focus(raw: str) -> str:
    raw_l = raw.lower().strip()
    for alias, canon in _FOCUS_ALIASES.items():
        if alias in raw_l:
            return canon
    return "strategy"  # safe fallback


def _parse_client_detail_sheet(xl: pd.ExcelFile) -> dict:
    """Extract client name, industry, retainer from the '1. Client' sheet."""
    client_data: dict = {"client_name": "", "industry": "Unknown", "retainer_aud_monthly": 0.0}
    try:
        sheet = next(s for s in xl.sheet_names if "client" in s.lower())
        df = xl.parse(sheet, header=None)
    except (StopIteration, Exception):
        return client_data

    # Scan all cells for label-value patterns
    for _, row in df.iterrows():
        vals = [str(v).strip() for v in row.values if str(v).strip() not in ("nan", "")]
        if len(vals) < 2:
            continue
        label = vals[0].lower()
        value = vals[1]
        if "client" in label or "company" in label:
            client_data["client_name"] = value
        elif "industry" in label or "sector" in label or "vertical" in label:
            client_data["industry"] = value
        elif "retainer" in label or "fee" in label or "monthly" in label:
            try:
                cleaned = re.sub(r"[^0-9.]", "", value)
                client_data["retainer_aud_monthly"] = float(cleaned)
            except (ValueError, TypeError):
                pass

    return client_data


def _parse_breakdown_sheet(xl: pd.ExcelFile) -> dict[str, float]:
    """Extract hours-per-focus from the 'Breakdown' grid sheet."""
    try:
        sheet = next(s for s in xl.sheet_names if "breakdown" in s.lower())
        df = xl.parse(sheet, header=None)
    except (StopIteration, Exception):
        return {}

    hours_by_focus: dict[str, float] = {}
    for _, row in df.iterrows():
        vals = [str(v).strip() for v in row.values if str(v).strip() not in ("nan", "")]
        if len(vals) < 2:
            continue
        label = vals[0].lower()
        canon = _canonicalize_focus(label)
        # Find the first numeric value after the label
        for v in vals[1:]:
            try:
                cleaned = re.sub(r"[^0-9.]", "", v)
                if cleaned:
                    hours_by_focus[canon] = hours_by_focus.get(canon, 0.0) + float(cleaned)
                    break
            except (ValueError, TypeError):
                continue

    return hours_by_focus


def _parse_task_sheet(xl: pd.ExcelFile, sheet_name_pattern: str, focus_canon: str) -> list[dict]:
    """Extract task list from a numbered focus-area sheet."""
    try:
        sheet = next(s for s in xl.sheet_names if sheet_name_pattern.lower() in s.lower())
        df = xl.parse(sheet)
    except (StopIteration, Exception):
        return []

    tasks = []
    cols_lower = {c.lower().strip(): c for c in df.columns}

    task_col = next((cols_lower[c] for c in cols_lower if "task" in c or "activity" in c or "deliverable" in c), None)
    hours_col = next((cols_lower[c] for c in cols_lower if "hour" in c), None)
    occ_col = next((cols_lower[c] for c in cols_lower if "occurrence" in c or "frequency" in c), None)

    if task_col is None:
        return tasks

    for _, row in df.iterrows():
        name = str(row.get(task_col, "")).strip()
        if not name or name.lower() in ("nan", "task", "activity"):
            continue
        hours = 0.0
        if hours_col:
            try:
                hours = float(row.get(hours_col, 0) or 0)
            except (TypeError, ValueError):
                pass
        occurrence = "Monthly"
        if occ_col:
            occ_raw = str(row.get(occ_col, "Monthly")).strip()
            if occ_raw.lower() not in ("nan", ""):
                occurrence = occ_raw
        tasks.append({"name": name, "hours": hours, "occurrence": occurrence, "contribution": "primary"})

    return tasks


def _parse_content_sheet(xl: pd.ExcelFile) -> list[dict]:
    """Extract URL-level content plan from the '4. Content' sheet."""
    try:
        sheet = next(s for s in xl.sheet_names if "content" in s.lower() and any(
            c in s.lower() for c in ("4.", "content plan", "url")
        ))
        df = xl.parse(sheet)
    except StopIteration:
        # Fall back to any sheet with "content" in name
        try:
            sheet = next(s for s in xl.sheet_names if "content" in s.lower())
            df = xl.parse(sheet)
        except (StopIteration, Exception):
            return []
    except Exception:
        return []

    content_plan = []
    cols_lower = {c.lower().strip(): c for c in df.columns}

    url_col = next((cols_lower[c] for c in cols_lower if "url" in c or "page" in c or "slug" in c), None)
    kw_col = next((cols_lower[c] for c in cols_lower if "keyword" in c or "query" in c or "term" in c), None)
    type_col = next((cols_lower[c] for c in cols_lower if "type" in c or "action" in c), None)
    month_col = next((cols_lower[c] for c in cols_lower if "month" in c or "publish" in c or "date" in c), None)
    notes_col = next((cols_lower[c] for c in cols_lower if "note" in c or "brief" in c or "comment" in c), None)

    if url_col is None and kw_col is None:
        return content_plan

    for _, row in df.iterrows():
        url = str(row.get(url_col, "") if url_col else "").strip()
        keyword = str(row.get(kw_col, "") if kw_col else "").strip()
        if not url and not keyword:
            continue
        if url.lower() in ("nan", "url", "page") or keyword.lower() in ("nan", "keyword"):
            continue

        page_type = "new"
        if type_col:
            type_raw = str(row.get(type_col, "new")).lower()
            if any(w in type_raw for w in ("optim", "update", "existing", "refresh")):
                page_type = "optimise"

        publish_month = 1
        if month_col:
            try:
                pm = row.get(month_col)
                if pm is not None:
                    publish_month = max(1, int(float(str(pm).replace("month", "").strip())))
            except (TypeError, ValueError):
                pass

        notes = str(row.get(notes_col, "") if notes_col else "").strip()
        notes = "" if notes.lower() == "nan" else notes

        content_plan.append({
            "url": url,
            "keyword": keyword,
            "page_type": page_type,
            "publish_month": publish_month,
            "notes": notes,
        })

    return content_plan


def parse_pattern_native(raw_bytes: bytes) -> dict:
    """Deterministic multi-sheet parser for Pattern-native SOW workbooks.

    Does NOT call AI. Returns a v2 schema bundle.

    Args:
        raw_bytes: xlsx file bytes.

    Returns:
        Bundle matching ROADMAP_BUNDLE_SCHEMA_V2.

    Raises:
        ValueError: If the file cannot be read or has fewer than 4 recognised sheets.
    """
    try:
        buf = io.BytesIO(raw_bytes)
        xl = pd.ExcelFile(buf, engine="openpyxl")
    except Exception as exc:
        raise ValueError(f"Cannot open Excel file: {exc}") from exc

    bundle: dict = {
        "schema_version": "2.0",
        "source_format": "pattern_native",
        "extraction_date": datetime.now(timezone.utc).isoformat(),
        "client_metadata": {"client_name": "", "industry": "Unknown", "retainer_aud_monthly": 0.0},
        "source_summary": {
            "total_tasks_detected": 0,
            "focus_areas_detected": [],
            "timeline_months_covered": 12,
            "parsing_confidence": 0.9,
        },
        "per_focus": {},
        "timeline": {
            "months_covered": 12,
            "strategy_restart_month": None,
            "phasing_notes": "",
            "has_launch_dates": False,
        },
        "content_plan": [],
        "global_rollup": {},
        "recommendations": [],
        "gaps": [],
    }

    # 1. Client metadata
    bundle["client_metadata"] = _parse_client_detail_sheet(xl)

    # 2. Hours from breakdown grid (primary source)
    breakdown_hours = _parse_breakdown_sheet(xl)

    # 3. Per-focus task details from numbered sheets
    focus_sheet_map = {
        "consulting": "strategy",
        "technical": "technical",
        "content": "content",
        "links": "off_page",
    }
    all_tasks: dict[str, list[dict]] = {}
    raw_task_descriptions: list[str] = []

    for sheet_pattern, focus_canon in focus_sheet_map.items():
        tasks = _parse_task_sheet(xl, sheet_pattern, focus_canon)
        if tasks:
            all_tasks[focus_canon] = tasks
            raw_task_descriptions.extend(t["name"] for t in tasks)

    # 4. Content plan
    content_plan = _parse_content_sheet(xl)
    bundle["content_plan"] = content_plan

    # 5. Determine timeline from content plan max publish month
    if content_plan:
        max_month = max(item.get("publish_month", 1) for item in content_plan)
        bundle["timeline"]["months_covered"] = max(12, max_month)
        bundle["timeline"]["has_launch_dates"] = True

        # strategy_restart_month = last month with a content item
        last_content_month = max(item.get("publish_month", 1) for item in content_plan)
        bundle["timeline"]["strategy_restart_month"] = last_content_month

    # 6. Build per_focus from breakdown hours (or task-derived if breakdown missing)
    _FOCUS_KEYS = ["content", "technical", "on_page", "off_page", "local", "analytics", "strategy"]
    focus_areas_detected = []
    total_tasks = 0

    for focus in _FOCUS_KEYS:
        hours = breakdown_hours.get(focus, 0.0)

        # Fall back to summing task hours if breakdown sheet didn't have this focus
        if hours == 0.0 and focus in all_tasks:
            hours = sum(
                t["hours"] * _OCCURRENCE_MULTIPLIERS.get(t["occurrence"].lower(), 1.0)
                for t in all_tasks[focus]
            )

        tasks = all_tasks.get(focus, [])
        effort = _classify_hours(hours)
        task_count = len(tasks)
        total_tasks += task_count

        if hours > 0:
            focus_areas_detected.append(focus)

        bundle["per_focus"][focus] = {
            "effort_level": effort,
            "monthly_hours": round(hours, 1),
            "cadence": max(1, round(hours / 10)) if focus == "content" and hours > 0 else 0,
            "task_count": task_count,
            "tasks": tasks,
        }

    bundle["source_summary"]["total_tasks_detected"] = total_tasks
    bundle["source_summary"]["focus_areas_detected"] = focus_areas_detected
    bundle["source_summary"]["timeline_months_covered"] = bundle["timeline"]["months_covered"]

    # 7. Global rollup
    total_hours = sum(bundle["per_focus"][f]["monthly_hours"] for f in _FOCUS_KEYS)
    rollup_foci = ("content", "on_page", "off_page")
    _EFFORT_ORDER = {"light": 0, "moderate": 1, "aggressive": 2}
    _EFFORT_NAMES = ["light", "moderate", "aggressive"]
    effort_vals = [bundle["per_focus"][f]["effort_level"] for f in rollup_foci]
    max_effort_idx = max(_EFFORT_ORDER.get(v, 1) for v in effort_vals)
    pos_vals = [bundle["per_focus"]["on_page"]["effort_level"], bundle["per_focus"]["off_page"]["effort_level"]]
    pos_idx = max(_EFFORT_ORDER.get(v, 1) for v in pos_vals)
    on_page_hrs = bundle["per_focus"]["on_page"]["monthly_hours"]
    technical_hrs = bundle["per_focus"]["technical"]["monthly_hours"]
    content_hrs = bundle["per_focus"]["content"]["monthly_hours"]

    bundle["global_rollup"] = {
        "total_monthly_hours": round(total_hours, 1),
        "effort_level": _EFFORT_NAMES[max_effort_idx],
        "maintenance_coverage": round(min(1.0, (on_page_hrs + technical_hrs) / 20.0), 2),
        "content_cadence": max(1, round(content_hrs / 10)) if content_hrs > 0 else 4,
        "positional_effort_level": _EFFORT_NAMES[pos_idx],
    }

    return bundle, raw_task_descriptions


# ── AI enrichment ─────────────────────────────────────────────────────────────


def enrich_bundle_with_ai(
    client,
    bundle: dict,
    raw_task_descriptions: list[str],
    model: str = "openai/gpt-4o-mini",
) -> tuple[dict, str]:
    """Enrich a deterministically-parsed v2 bundle with AI interpretation.

    Adds recommendations, gaps, and improves effort_level classifications
    for focus areas where the task descriptions are ambiguous.

    Args:
        client: Bi Frost OpenAI-compatible client.
        bundle: v2 bundle from parse_pattern_native().
        raw_task_descriptions: List of task name strings for context.
        model: Bi Frost model ID.

    Returns:
        (enriched_bundle, used_model)
    """
    system, user_tmpl = _load_prompt("enrich_roadmap")

    tasks_text = "\n".join(f"- {t}" for t in raw_task_descriptions[:80])
    per_focus_json = json.dumps({
        k: {"monthly_hours": v["monthly_hours"], "effort_level": v["effort_level"]}
        for k, v in bundle.get("per_focus", {}).items()
    }, indent=2)

    user_input = user_tmpl.substitute(
        task_descriptions=tasks_text,
        per_focus_summary=per_focus_json,
        client_name=bundle.get("client_metadata", {}).get("client_name", "the client"),
        industry=bundle.get("client_metadata", {}).get("industry", "Unknown"),
    )

    text, used_model = generate_with_fallback(
        client, model, system, user_input, temperature=0.1, max_tokens=1500,
    )
    try:
        ai_additions = _parse_llm_json(text)
    except Exception:
        return bundle, used_model

    # Merge AI additions into bundle — never overwrite deterministic hours
    if "recommendations" in ai_additions:
        bundle["recommendations"] = ai_additions["recommendations"]
    if "gaps" in ai_additions:
        bundle["gaps"] = ai_additions["gaps"]
    # Accept effort_level corrections only when AI is confident
    corrections = ai_additions.get("effort_corrections", {})
    for focus, new_effort in corrections.items():
        if new_effort in ("light", "moderate", "aggressive") and focus in bundle.get("per_focus", {}):
            bundle["per_focus"][focus]["effort_level"] = new_effort

    return bundle, used_model


# ── Full AI extraction path ───────────────────────────────────────────────────


def _read_single_sheet(raw_bytes: bytes, file_extension: str) -> pd.DataFrame:
    """Parse first sheet of a file into a DataFrame."""
    buf = io.BytesIO(raw_bytes)
    ext = file_extension.lower().lstrip(".")
    if ext in ("xlsx", "xls"):
        try:
            return pd.read_excel(buf, engine="openpyxl")
        except Exception as exc:
            raise ValueError(f"Cannot read Excel file: {exc}") from exc
    elif ext == "tsv":
        return pd.read_csv(buf, sep="\t")
    else:
        try:
            return pd.read_excel(buf, engine="openpyxl")
        except Exception:
            buf.seek(0)
            try:
                return pd.read_csv(buf)
            except Exception as exc:
                raise ValueError(f"Cannot parse file: {exc}") from exc


# Kept for backwards compat; pages that still call this directly continue to work.
def _read_roadmap_file(raw_bytes: bytes, file_extension: str) -> pd.DataFrame:
    df = _read_single_sheet(raw_bytes, file_extension)
    if df.empty:
        raise ValueError("Roadmap file is empty or could not be read")
    return df


def _df_to_markdown(df: pd.DataFrame, max_chars: int = 4000) -> tuple[str, bool]:
    """Convert DataFrame to a compact markdown table, truncated to max_chars."""
    cols = list(df.columns)
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    rows_md = [
        "| " + " | ".join(str(v) for v in row.values) + " |"
        for _, row in df.iterrows()
    ]
    full = "\n".join([header, sep] + rows_md)
    if len(full) <= max_chars:
        return full, False
    return full[:max_chars] + "\n... [truncated]", True


def _cache_key(raw_bytes: bytes, nl_correction: str | None, model: str) -> str:
    h = hashlib.sha256()
    h.update(raw_bytes)
    h.update((nl_correction or "").encode())
    h.update(model.encode())
    return h.hexdigest()[:16]


def extract_roadmap_full_ai(
    client,
    raw_roadmap_bytes: bytes,
    file_extension: str,
    nl_correction: str | None = None,
    previous_extraction: dict | None = None,
    model: str = "openai/gpt-4o-mini",
    cache: dict | None = None,
) -> tuple[dict, str]:
    """Extract structured roadmap bundle from a raw file using full AI extraction.

    For generic (non-Pattern-native) files. Returns a v1 schema bundle.

    Args:
        client: Bi Frost OpenAI-compatible client.
        raw_roadmap_bytes: The uploaded file contents.
        file_extension: "xlsx" | "xls" | "csv" | "tsv".
        nl_correction: User's natural-language correction text, if any.
        previous_extraction: Prior extraction dict for re-run context.
        model: Bi Frost model ID.
        cache: Dict for caching results (pass session_state["roadmap_ai_cache"]).

    Returns:
        (bundle, used_model) where bundle matches ROADMAP_BUNDLE_SCHEMA.
    """
    key = _cache_key(raw_roadmap_bytes, nl_correction, model)
    if cache is not None and key in cache:
        return cache[key]["bundle"], cache[key]["model"]

    df = _read_single_sheet(raw_roadmap_bytes, file_extension)
    roadmap_md, truncated = _df_to_markdown(df, max_chars=4000)

    system, user_tmpl = _load_prompt("extract_roadmap")
    schema_str = json.dumps(ROADMAP_BUNDLE_SCHEMA, indent=2)

    if nl_correction and previous_extraction:
        correction_ctx = (
            f'User correction to previous extraction:\n"{nl_correction}"\n\n'
            f"Previous extraction (apply the correction above to this):\n"
            f"{json.dumps(previous_extraction, indent=2)}"
        )
    elif nl_correction:
        correction_ctx = f'User correction:\n"{nl_correction}"'
    else:
        correction_ctx = ""

    user_input = user_tmpl.substitute(
        roadmap_markdown=roadmap_md,
        correction_context=correction_ctx,
        schema=schema_str,
    )

    text, used_model = generate_with_fallback(
        client, model, system, user_input, temperature=0.1, max_tokens=3000,
    )
    bundle = _parse_llm_json(text)
    bundle["extraction_date"] = datetime.now(timezone.utc).isoformat()

    if truncated and "source_summary" in bundle:
        conf = bundle["source_summary"].get("parsing_confidence", 0.9)
        bundle["source_summary"]["parsing_confidence"] = min(float(conf), 0.75)

    if cache is not None:
        cache[key] = {"bundle": bundle, "model": used_model}

    return bundle, used_model


# Backwards-compat alias used by existing pages/tests
extract_roadmap_with_ai = extract_roadmap_full_ai


def _wrap_legacy_as_bundle(legacy: dict) -> dict:
    """Wrap a legacy scalar roadmap dict into a minimal v1 bundle."""
    bundle = {
        "schema_version": "1.0",
        "source_format": "legacy",
        "extraction_date": datetime.now(timezone.utc).isoformat(),
        "source_summary": {
            "total_tasks_detected": 0,
            "focus_areas_detected": [],
            "timeline_months_covered": 12,
            "parsing_confidence": 0.5,
        },
        "per_focus": {f: {"effort_level": "moderate", "monthly_hours": 0.0, "cadence": 0, "task_count": 0, "tasks": []}
                      for f in ("content", "technical", "on_page", "off_page", "local", "analytics", "strategy")},
        "timeline": {"months_covered": 12, "phasing_notes": "", "has_launch_dates": False},
        "global_rollup": {
            "total_monthly_hours": 0.0,
            "effort_level": legacy.get("effort_level", "moderate"),
            "maintenance_coverage": legacy.get("maintenance_coverage", 0.0),
            "content_cadence": legacy.get("content_cadence", 4),
            "positional_effort_level": "moderate",
        },
        "recommendations": [],
        "gaps": [],
    }
    return bundle


# ── Main entry point ──────────────────────────────────────────────────────────


def load_roadmap_v2(
    client,
    raw_bytes: bytes,
    filename: str,
    nl_correction: str | None = None,
    previous_bundle: dict | None = None,
    model: str = "openai/gpt-4o-mini",
    cache: dict | None = None,
    enrich: bool = True,
) -> tuple[dict, str]:
    """Unified roadmap loading entry point.

    Detects format → routes to appropriate parser → optionally enriches with AI.

    Routing:
        - pattern_native → parse_pattern_native() [deterministic] + optionally enrich_bundle_with_ai()
        - task_table / param_table / unknown → extract_roadmap_full_ai()

    Args:
        client: Bi Frost client (may be None for pattern_native without enrichment).
        raw_bytes: File bytes.
        filename: Original filename (used for extension detection).
        nl_correction: Natural-language correction to apply.
        previous_bundle: Prior bundle for correction context.
        model: Bi Frost model ID.
        cache: Session-state cache dict (pass st.session_state["roadmap_ai_cache"]).
        enrich: If True and format is pattern_native, call enrich_bundle_with_ai().

    Returns:
        (bundle, used_model) — used_model is "deterministic" for pattern_native without AI.
    """
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "csv"
    fmt = detect_roadmap_format(raw_bytes, ext)

    if fmt == "pattern_native":
        try:
            bundle, raw_task_descriptions = parse_pattern_native(raw_bytes)
        except ValueError:
            # Fall back to AI if native parse fails
            return extract_roadmap_full_ai(
                client, raw_bytes, ext, nl_correction, previous_bundle, model, cache
            )

        used_model = "deterministic"

        # Apply NL correction if any (re-run AI enrichment with correction context)
        if nl_correction or (enrich and client is not None):
            try:
                bundle, used_model = enrich_bundle_with_ai(client, bundle, raw_task_descriptions, model)
            except Exception:
                pass  # Enrichment is best-effort; deterministic data is still valid

        return bundle, used_model

    # Generic path: full AI extraction
    return extract_roadmap_full_ai(
        client, raw_bytes, ext, nl_correction, previous_bundle, model, cache
    )


# ── Token estimation (kept for UI cost display) ───────────────────────────────


def estimate_extraction_tokens(
    roadmap_md: str,
    correction_ctx: str = "",
    schema_str: str = "",
) -> int:
    """Rough token estimate for a roadmap extraction call (4 chars ≈ 1 token)."""
    chars = len(roadmap_md) + len(correction_ctx) + len(schema_str) + 1500
    return chars // 4
