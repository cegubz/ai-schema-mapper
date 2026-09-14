"""Mapping engine — orchestrates the established workflow end to end.

Order (unchanged from the project):
  detect sheets -> profile columns -> deterministic score -> LLM refine (optional)
  -> normalize/quarantine rows -> build NEO/LAO -> assemble mapping_report.
"""
from __future__ import annotations
import pandas as pd

from . import profiling, scorer, builders, normalizer
from .llm_mapper import refine_mapping
from .settings import settings


def _pick_sheet(frames: dict, candidates: list[str]) -> str:
    """Tie-break multiple matching tabs by most populated rows."""
    return max(candidates, key=lambda s: len(frames[s].dropna(how="all")))


def run_mapping(workbook_path: str, cfg: dict) -> dict:
    frames = profiling.read_workbook(workbook_path)
    sheet_match = profiling.match_sheets(list(frames.keys()), cfg["sheet_config"])

    # Resolve role -> concrete sheet name
    role_to_sheet, warnings = {}, []
    for role, info in sheet_match["matched"].items():
        chosen = _pick_sheet(frames, info["candidates"])
        role_to_sheet[role] = chosen
        if len(info["candidates"]) > 1:
            warnings.append(f"Role {role} matched {info['candidates']}; picked '{chosen}' by row count.")

    # Measurement-Points functional-location values for the LTP join-key bonus
    # mp_role = "MEASUREMENT_POINTS"
    mp_role = cfg["normalization"].get("join_key_role", "MEASUREMENT_POINTS")
    mp_key_values = set()
    if mp_role in role_to_sheet:
        mp_df = frames[role_to_sheet[mp_role]]
        for col in mp_df.columns:
            if "functional" in str(col).lower() or "function location" in str(col).lower():
                mp_key_values = set(mp_df[col].dropna().astype(str))
                break

    report = {
        "matched_sheets": [
            {"role": r, "sheet_name": s, "rows": int(len(frames[s]))}
            for r, s in role_to_sheet.items()
        ],
        "unmatched_roles": sheet_match["unmatched_roles"],
        "mappings": {},
        "column_confidence_summary": {},
        "row_counts": {},
        "warnings": warnings,
        "llm_used": settings.llm_configured(),
    }

    outputs = {}          # target -> DataFrame
    rejected_frames = []  # Normalized rows across sheets

    for target, tcfg in cfg["targets"].items():
        role = tcfg["source_sheet_role"]
        if role not in role_to_sheet:
            report["mappings"][target] = []
            continue
        df = frames[role_to_sheet[role]]
        profiles = profiling.profile_columns(df)

        # join-overlap function for the join key
        def _overlap(colname, _df=df):
            vals = set(_df[colname].dropna().astype(str))
            if not vals or not mp_key_values:
                return 0.0
            return len(vals & mp_key_values) / len(vals)

        det = scorer.score_target(tcfg["fields"], profiles, cfg["scoring"], _overlap)

        # LLM refinement (alias reasoning + notes); deterministic numbers stand unless LLM
        # picks a *different valid* column, in which case we keep the deterministic score
        # for that column but record the LLM note.
        llm = refine_mapping(target, tcfg["fields"], profiles, det)

        resolved, rows = {}, []
        for f in tcfg["fields"]:
            can = f["canonical"]
            sc = f.get("source_class")
            if sc == "customer_file":
                d = det.get(can, {})
                src = d.get("source_column")
                note = ""
                if can in llm and llm[can].get("source_column") and llm[can]["source_column"] != src:
                    # LLM proposes a different valid column -> accept its choice, flag review
                    src = llm[can]["source_column"]
                    note = f"LLM override: {llm[can].get('notes','')}"
                elif can in llm:
                    note = llm[can].get("notes", "")
                resolved[can] = src
                rows.append({
                    "canonical_field": can, "source_column": src, "source_class": sc,
                    "name_score": d.get("name_score", 0.0), "value_score": d.get("value_score", 0.0),
                    "confidence": d.get("confidence", 0.0), "band": d.get("band", "reject"),
                    "status": "mapped" if src else "unmapped", "notes": note,
                })
            elif sc == "constant":
                rows.append({"canonical_field": can, "source_column": None, "source_class": sc,
                             "confidence": 1.0, "band": "auto_accept", "status": "constant", "notes": ""})
            elif sc == "derived":
                rows.append({"canonical_field": can, "source_column": None, "source_class": sc,
                             "confidence": None, "band": None, "status": "derived",
                             "notes": f"transform={f.get('transform')}"})
            else:  # enrichment
                rows.append({"canonical_field": can, "source_column": None, "source_class": sc,
                             "confidence": 0.0, "band": None, "status": "requires_enrichment",
                             "notes": f"join={f.get('join')}"})
        report["mappings"][target] = rows

        conf_vals = [r["confidence"] for r in rows
                     if r["source_class"] == "customer_file" and r["confidence"] is not None]
        report["column_confidence_summary"][f"{target}_mean"] = (
            round(sum(conf_vals) / len(conf_vals), 3) if conf_vals else None
        )

        # normalize/quarantine
        key_field = cfg["normalization"]["join_key_canonical"]
        key_col = resolved.get(key_field)
        id_field = tcfg.get("identity_canonical")
        id_col = resolved.get(id_field, key_col)
        clean, rejected = normalizer.split_clean_rejected(
            df, role_to_sheet[role], key_col, id_col, cfg["normalization"]["reject_rules"]
        )
        if not rejected.empty:
            rejected_frames.append(rejected)

        outputs[target] = builders.build_target(clean, tcfg, resolved, cfg["constants"])
        report["row_counts"][target] = int(len(outputs[target]))

    normalized = (
        pd.concat(rejected_frames, ignore_index=True) if rejected_frames else pd.DataFrame()
    )
    report["row_counts"]["Normalized"] = int(len(normalized))

    return {"report": report, "outputs": outputs, "normalized": normalized}
