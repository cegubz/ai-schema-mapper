"""Workbook + column profiling.

Reads the customer workbook, matches sheets to roles via the flexible sheet_config,
and profiles each column so the scorer and the LLM have evidence to reason over.
No mapping decisions are made here.
"""
from __future__ import annotations
import re
import warnings
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="pandas")


def _norm_tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]", " ", str(text).lower()).split()


def match_sheets(sheet_names: list[str], sheet_config: list[dict]) -> dict:
    """Return {role: {sheet_name, target}} using case-insensitive substring patterns.

    Flexible: driven entirely by sheet_config, so new customers/sheets need no code.
    """
    matched, unmatched_roles = {}, []
    lowered = {s: s.lower() for s in sheet_names}
    for entry in sheet_config:
        role, target = entry["role"], entry["target"]
        patterns = [p.lower() for p in entry.get("match_patterns", [])]
        hits = [s for s, low in lowered.items() if any(p in low for p in patterns)]
        if not hits:
            unmatched_roles.append(role)
            continue
        # If several tabs match, defer the tie-break to the caller (row count).
        matched[role] = {"candidates": hits, "target": target}
    return {"matched": matched, "unmatched_roles": unmatched_roles}


def read_workbook(path: str) -> dict:
    """Read all sheets into DataFrames (dropping fully-empty rows)."""
    xls = pd.ExcelFile(path)
    frames = {}
    for name in xls.sheet_names:
        df = xls.parse(name)
        frames[name] = df.dropna(how="all").reset_index(drop=True)
    return frames


def _value_evidence(series: pd.Series, dtype: str) -> float:
    s = series.dropna()
    if len(s) == 0:
        return 0.0
    if dtype == "floc":
        return float(s.astype(str).str.contains(r"-", na=False).mean())
    if dtype == "numeric":
        return float(pd.to_numeric(s, errors="coerce").notna().mean())
    if dtype == "date":
        return float(pd.to_datetime(s, errors="coerce", dayfirst=True).notna().mean())
    if dtype == "model":
        return float(s.astype(str).str.match(r"^[0-9]{2,3}[A-Z]{0,2}$|^D[0-9]", na=False).mean())
    if dtype == "str":
        return float((s.astype(str).str.len() > 1).mean())
    return 0.5


def profile_columns(df: pd.DataFrame, sample_n: int = 8) -> list[dict]:
    """Per-column profile: header, non-null ratio, dtype evidence, and value samples."""
    profiles = []
    n = max(1, len(df))
    for col in df.columns:
        if str(col).startswith("Unnamed"):
            continue
        s = df[col]
        samples = (
            s.dropna().astype(str).unique()[:sample_n].tolist()
        )
        profiles.append(
            {
                "column": str(col),
                "tokens": _norm_tokens(col),
                "non_null_ratio": round(float(s.notna().sum()) / n, 3),
                "evidence": {
                    k: round(_value_evidence(s, k), 3)
                    for k in ("floc", "numeric", "date", "model", "str")
                },
                "samples": samples,
            }
        )
    return profiles
