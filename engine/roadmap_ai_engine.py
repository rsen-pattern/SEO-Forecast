"""AI-powered roadmap extraction engine.

Converts an uploaded xlsx/csv roadmap file into a structured per-focus-area bundle
using a Bi Frost LLM call. The bundle maps cleanly to the per-focus assumption keys
in engine/assumptions.py via _detect_from_roadmap_bundle().

Pure Python — no Streamlit imports. Session-state caching is handled by the caller
(page passes `cache=st.session_state.setdefault("roadmap_ai_cache", {})`).
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime

import pandas as pd

from engine.ai_engine import _load_prompt, _parse_llm_json, generate_with_fallback

# ── Schema constant ───────────────────────────────────────────────────────────

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


# ── Internal helpers ──────────────────────────────────────────────────────────


def _cache_key(raw_bytes: bytes, nl_correction: str | None, model: str) -> str:
    h = hashlib.sha256()
    h.update(raw_bytes)
    h.update((nl_correction or "").encode())
    h.update(model.encode())
    return h.hexdigest()[:16]


_SKIP_SHEET_PATTERNS = ("menu", "stephs sheet")


def _read_roadmap_file(raw_bytes: bytes, file_extension: str) -> pd.DataFrame:
    """Parse roadmap file bytes into a DataFrame.

    For multi-sheet xlsx files, concatenates all non-menu sheets with a
    __sheet__ marker column so the AI sees the full roadmap structure.
    header=None is used so rows containing labels like "Client Name" are kept.
    """
    buf = io.BytesIO(raw_bytes)
    ext = file_extension.lower().lstrip(".")

    if ext in ("xlsx", "xls"):
        try:
            xl = pd.ExcelFile(buf, engine="openpyxl")
        except Exception as exc:
            raise ValueError(f"Cannot read Excel file: {exc}") from exc

        sheets_to_read = [
            s for s in xl.sheet_names
            if not any(p in s.lower() for p in _SKIP_SHEET_PATTERNS)
        ]

        frames = []
        for sheet in sheets_to_read:
            try:
                sdf = xl.parse(sheet, header=None)
            except Exception:
                continue
            if sdf.empty:
                continue
            sdf.insert(0, "__sheet__", sheet)
            frames.append(sdf)

        if not frames:
            raise ValueError("No usable sheets found in workbook")

        max_cols = max(f.shape[1] for f in frames)
        aligned = []
        for f in frames:
            for i in range(f.shape[1], max_cols):
                f[i] = None
            aligned.append(f)
        df = pd.concat(aligned, ignore_index=True)

    elif ext == "tsv":
        try:
            df = pd.read_csv(buf, sep="\t")
        except Exception as exc:
            raise ValueError(f"Cannot read TSV file: {exc}") from exc
    else:
        # csv or unknown — try Excel then CSV
        try:
            df = pd.read_excel(buf, engine="openpyxl")
        except Exception:
            buf.seek(0)
            try:
                df = pd.read_csv(buf)
            except Exception as exc:
                raise ValueError(f"Cannot parse roadmap file: {exc}") from exc

    if df.empty:
        raise ValueError("Roadmap file is empty or could not be read")
    return df


def _df_to_markdown(df: pd.DataFrame, max_chars: int = 12000) -> tuple[str, bool]:
    """Convert DataFrame to a compact markdown representation, truncated to max_chars.

    Drops fully-empty rows and columns. When a __sheet__ marker column is present
    (added by _read_roadmap_file for multi-sheet xlsx), groups output by sheet.
    """
    df = df.dropna(how="all", axis=0)
    if len(df) > 0:
        df = df.dropna(how="all", axis=1)

    lines: list[str] = []
    if "__sheet__" in df.columns:
        for sheet, group in df.groupby("__sheet__", sort=False):
            g = group.drop(columns=["__sheet__"]).dropna(how="all", axis=1)
            lines.append(f"\n## Sheet: {sheet}\n")
            for _, row in g.iterrows():
                vals = [str(v).strip() for v in row.values if pd.notna(v) and str(v).strip()]
                if vals:
                    lines.append(" | ".join(vals))
    else:
        cols = list(df.columns)
        lines.append("| " + " | ".join(str(c) for c in cols) + " |")
        lines.append("| " + " | ".join("---" for _ in cols) + " |")
        for _, row in df.iterrows():
            vals = [str(v) if pd.notna(v) else "" for v in row.values]
            lines.append("| " + " | ".join(vals) + " |")

    full = "\n".join(lines)
    if len(full) <= max_chars:
        return full, False
    return full[:max_chars] + "\n... [truncated]", True


# ── Public API ────────────────────────────────────────────────────────────────


def extract_roadmap_with_ai(
    client,
    raw_roadmap_bytes: bytes,
    file_extension: str,
    nl_correction: str | None = None,
    previous_extraction: dict | None = None,
    model: str = "openai/gpt-4o-mini",
    cache: dict | None = None,
) -> tuple[dict, str]:
    """Extract structured roadmap bundle from a raw xlsx/csv file using AI.

    Args:
        client: Bi Frost OpenAI-compatible client.
        raw_roadmap_bytes: The uploaded file contents.
        file_extension: "xlsx" | "xls" | "csv" | "tsv".
        nl_correction: User's natural-language correction text, if any.
        previous_extraction: Prior extraction dict for re-run context.
        model: Bi Frost model ID.
        cache: Dict for caching results (pass session_state["roadmap_ai_cache"]).
            Identical inputs return the cached result without an AI call.

    Returns:
        (bundle, used_model) where bundle matches ROADMAP_BUNDLE_SCHEMA.
    """
    key = _cache_key(raw_roadmap_bytes, nl_correction, model)
    if cache is not None and key in cache:
        return cache[key]["bundle"], cache[key]["model"]

    df = _read_roadmap_file(raw_roadmap_bytes, file_extension)
    roadmap_md, truncated = _df_to_markdown(df, max_chars=12000)

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

    # Stamp extraction date
    bundle["extraction_date"] = datetime.now(UTC).isoformat()

    # Downgrade confidence when input was truncated
    if truncated and "source_summary" in bundle:
        conf = bundle["source_summary"].get("parsing_confidence", 0.9)
        bundle["source_summary"]["parsing_confidence"] = min(float(conf), 0.75)

    if cache is not None:
        cache[key] = {"bundle": bundle, "model": used_model}

    return bundle, used_model


MAX_ENRICHMENT_INPUT_CHARS = 4000
MAX_EXTRACTION_INPUT_CHARS = 12000


def compute_cache_key(raw_bytes: bytes, nl_correction: str | None, model: str) -> str:
    """Public alias for _cache_key — pages use this to check before calling load_roadmap_v2."""
    return _cache_key(raw_bytes, nl_correction, model)


def _bundle_summary_for_enrichment(bundle: dict) -> str:
    """Compact text summary of a parsed bundle for the enrichment prompt."""
    parts = [
        f"Format: {bundle.get('format_detected', 'unknown')}",
        f"Timeline: {bundle.get('timeline', {})}",
        "Per-focus:",
    ]
    for focus, data in bundle.get("per_focus", {}).items():
        parts.append(
            f"  {focus}: {data.get('monthly_hours', 0):.1f} h/mo, "
            f"{data.get('effort_level', '?')}, {data.get('task_count', 0)} tasks"
        )
    cp = bundle.get("content_plan", [])
    if cp:
        parts.append(f"Content plan: {len(cp)} launches, sample: {cp[:3]}")
    return "\n".join(parts)[:MAX_ENRICHMENT_INPUT_CHARS]


def _extract_raw_task_text(bundle: dict) -> str:
    """Flat text list of all per-focus tasks for the enrichment prompt."""
    lines = []
    for focus, data in bundle.get("per_focus", {}).items():
        for task in data.get("tasks", []):
            lines.append(f"[{focus}] {task.get('name', '?')}: {task.get('description', '')}")
    return "\n".join(lines)[:MAX_ENRICHMENT_INPUT_CHARS]


def _reclassify_task(bundle: dict, task_name: str, from_focus: str, to_focus: str) -> None:
    """Move a task from one focus to another, re-summing hours."""
    per_focus = bundle.get("per_focus", {})
    src = per_focus.get(from_focus, {})
    dst = per_focus.get(to_focus, {})
    if not src or not dst:
        return
    src_tasks = src.get("tasks", [])
    matched = [t for t in src_tasks if t.get("name") == task_name]
    if not matched:
        return
    task = matched[0]
    task_hours = float(task.get("hours", 0) or 0)
    src["tasks"] = [t for t in src_tasks if t.get("name") != task_name]
    src["monthly_hours"] = max(0.0, src.get("monthly_hours", 0) - task_hours)
    src["task_count"] = len(src["tasks"])
    dst.setdefault("tasks", []).append(task)
    dst["monthly_hours"] = dst.get("monthly_hours", 0) + task_hours
    dst["task_count"] = len(dst["tasks"])


def enrich_bundle_with_ai(
    client,
    bundle: dict,
    model: str = "openai/gpt-4o-mini",
) -> tuple[dict, str]:
    """Add recommendations, gaps, and focus corrections to a deterministic bundle.

    Does NOT modify per_focus hours, content_plan, or client_metadata.
    Returns (enriched_bundle, used_model). When client is None, returns bundle unchanged.
    """
    if client is None:
        return bundle, "no-client"

    system, user_tmpl = _load_prompt("enrich_roadmap")
    user_input = user_tmpl.substitute(
        bundle_summary=_bundle_summary_for_enrichment(bundle),
        raw_task_text=_extract_raw_task_text(bundle),
    )
    text, used_model = generate_with_fallback(
        client, model, system, user_input, temperature=0.2,
    )
    enrichment = _parse_llm_json(text)

    bundle["recommendations"] = enrichment.get("recommendations", [])
    bundle["gaps"] = enrichment.get("gaps", [])

    for correction in enrichment.get("focus_corrections", []):
        task_name = correction.get("task_name")
        from_focus = correction.get("from_focus")
        to_focus = correction.get("to_focus")
        if task_name and from_focus and to_focus:
            _reclassify_task(bundle, task_name, from_focus, to_focus)

    return bundle, used_model


def extract_roadmap_full_ai(
    client,
    raw_bytes: bytes,
    file_extension: str,
    nl_correction: str | None = None,
    previous_bundle: dict | None = None,
    model: str = "openai/gpt-4o-mini",
) -> tuple[dict, str]:
    """Full AI extraction for unknown-format roadmaps.

    Unlike extract_roadmap_with_ai (which uses the v1 schema prompt), this uses
    the v2 bundle schema prompt and is the preferred path for unknown formats.
    """
    if client is None:
        raise ValueError("AI client required for unknown-format roadmap extraction")

    try:
        df = _read_roadmap_file(raw_bytes, file_extension)
    except Exception as exc:
        raise ValueError(f"Could not read file: {exc}") from exc

    roadmap_content = _df_to_markdown(df, max_chars=MAX_EXTRACTION_INPUT_CHARS)[0]

    correction_ctx = ""
    if nl_correction and previous_bundle:
        correction_ctx = (
            f'User correction to previous extraction:\n"{nl_correction}"\n\n'
            f"Previous extraction:\n{json.dumps(previous_bundle, indent=2)[:2000]}"
        )
    elif nl_correction:
        correction_ctx = f'User correction:\n"{nl_correction}"'

    system, user_tmpl = _load_prompt("extract_roadmap_full")
    user_input = user_tmpl.substitute(
        roadmap_content=roadmap_content,
        correction_context=correction_ctx,
    )
    text, used_model = generate_with_fallback(
        client, model, system, user_input, temperature=0.3, max_tokens=6000,
    )
    bundle = _parse_llm_json(text)
    bundle["extraction_date"] = datetime.now(UTC).isoformat()
    return bundle, used_model


def estimate_extraction_tokens(
    roadmap_md: str,
    correction_ctx: str = "",
    schema_str: str = "",
) -> int:
    """Rough token estimate for a roadmap extraction call (4 chars ≈ 1 token)."""
    chars = len(roadmap_md) + len(correction_ctx) + len(schema_str) + 1500  # system prompt overhead
    return chars // 4


def load_roadmap_v2(
    client,
    raw_bytes: bytes,
    filename: str,
    nl_correction: str | None = None,
    previous_bundle: dict | None = None,
    model: str = "openai/gpt-4o-mini",
) -> tuple[dict, str]:
    """Main entry point for roadmap ingestion. Returns (bundle, used_model_or_'deterministic').

    Dispatches to the correct parser based on format detection:
    - pattern_native → parse_pattern_native (deterministic)
    - task_table / param_table → legacy parsers wrapped in v2 bundle
    - unknown → AI extraction via extract_roadmap_with_ai (if client available)
    """
    from engine.roadmap_native_parser import (
        detect_roadmap_format,
        parse_pattern_native,
        wrap_legacy_param_table_as_bundle,
        wrap_legacy_task_table_as_bundle,
    )
    from utils.roadmap_loader import parse_param_table, parse_task_table

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    fmt = detect_roadmap_format(raw_bytes, ext)

    if fmt == "pattern_native":
        bundle = parse_pattern_native(raw_bytes, ai_client=client, source_filename=filename)
        return bundle, "hybrid_ai" if client is not None else "deterministic"

    if fmt == "task_table":
        df = pd.read_csv(io.BytesIO(raw_bytes)) if ext == "csv" else pd.read_excel(io.BytesIO(raw_bytes))
        legacy = parse_task_table(df)
        bundle = wrap_legacy_task_table_as_bundle(legacy)
        if client is not None:
            bundle, used_model = enrich_bundle_with_ai(client, bundle, model=model)
            return bundle, used_model
        return bundle, "deterministic"

    if fmt == "param_table":
        df = pd.read_csv(io.BytesIO(raw_bytes)) if ext == "csv" else pd.read_excel(io.BytesIO(raw_bytes))
        legacy = parse_param_table(df)
        return wrap_legacy_param_table_as_bundle(legacy), "deterministic"

    # Unknown format — use full AI extraction (v2 schema)
    if client is not None:
        return extract_roadmap_full_ai(
            client, raw_bytes, ext,
            nl_correction=nl_correction,
            previous_bundle=previous_bundle,
            model=model,
        )

    raise NotImplementedError(
        "Unknown roadmap format and no AI client available. "
        "Provide a Bi Frost API key for AI-based extraction."
    )
