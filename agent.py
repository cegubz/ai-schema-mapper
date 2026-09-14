"""Agent entrypoint.

run_agent(request) is the single callable used by every surface (Azure Function, CLI,
tests). It is transport-agnostic: give it a dict, get a dict back. Logic Apps calls the
HTTP Function which is a thin wrapper around this.

Request contract (all keys optional except input):
{
  "customer_id": "default",                 # which config/customers/*.json to use
  "input":  { "path": "..."} | {"content_base64": "...", "filename": "x.xlsx"},
  "output_dir": "…",                        # local backend only
  "sheet_config_override": [ ... ],         # optional per-call override (flexible)
  "return_inline": false                    # if true, include CSVs base64 in response
}

Response contract:
{
  "status": "succeeded" | "failed",
  "run_id": "...",
  "customer_id": "...",
  "mapping_report": { ... },                # confidence per column, row counts, warnings
  "outputs": { "NEO": "<path|url>", "LAO": "...", "Normalized": "..." },
  "outputs_inline": { ... base64 ... }      # only when return_inline=true
  "error": "..."                            # only on failure
}
"""
from __future__ import annotations
import uuid
import base64
import copy

from core.settings import load_customer_config
from core.mapping_engine import run_mapping
from core.storage import get_storage


def run_agent(request: dict) -> dict:
    run_id = request.get("run_id") or uuid.uuid4().hex[:12]
    customer_id = request.get("customer_id", "default")

    try:
        cfg = copy.deepcopy(load_customer_config(customer_id))
        # Per-call flexibility: allow Logic Apps to override sheet detection at runtime.
        if request.get("sheet_config_override"):
            cfg["sheet_config"] = request["sheet_config_override"]

        storage = get_storage(request.get("output_dir"))
        input_path = storage.fetch_input(request["input"])

        result = run_mapping(input_path, cfg)

        file_map = {"NEO": "NEO.csv", "LAO": "LAO.csv"}
        out_locations, out_inline = {}, {}

        for target, df in result["outputs"].items():
            loc = storage.write_csv(df, file_map.get(target, f"{target}.csv"), run_id)
            out_locations[target] = loc
            if request.get("return_inline"):
                out_inline[target] = base64.b64encode(df.to_csv(index=False).encode()).decode()

        norm = result["normalized"]
        loc = storage.write_csv(norm, "Normalized.csv", run_id)
        out_locations["Normalized"] = loc
        if request.get("return_inline"):
            out_inline["Normalized"] = base64.b64encode(norm.to_csv(index=False).encode()).decode()

        response = {
            "status": "succeeded",
            "run_id": run_id,
            "customer_id": customer_id,
            "mapping_report": result["report"],
            "outputs": out_locations,
        }
        if request.get("return_inline"):
            response["outputs_inline"] = out_inline
        return response

    except Exception as exc:  # surface a clean error to the orchestrator
        return {
            "status": "failed",
            "run_id": run_id,
            "customer_id": customer_id,
            "error": f"{type(exc).__name__}: {exc}",
        }
