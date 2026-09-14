"""LLM alias-reasoning layer (OpenAI).

Faithful to the recommended hybrid design: the deterministic scorer owns the numbers;
the LLM confirms/repairs alias matches for ambiguous columns and writes human-readable
notes, using the project prompt (prompts/schema_mapping_prompt.md).

If no API key is configured (or USE_LLM=false), this degrades gracefully and the agent
runs the deterministic path only — so it is always callable/testable.
"""
from __future__ import annotations
import json
from .settings import settings, load_prompt

# JSON schema for the structured mapping decision we ask the model to return.
_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "canonical_field": {"type": "string"},
                    "source_column": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["canonical_field", "source_column", "confidence", "notes"],
            },
        }
    },
    "required": ["decisions"],
}


def _client():
    """Return an OpenAI-compatible client for the configured provider.

    - openai  : direct OpenAI SDK client using OPENAI_API_KEY.
    - foundry : Foundry project gateway client (models from the Foundry catalog),
                authenticated with Entra ID via DefaultAzureCredential — no API key.
    Both expose the same Responses API surface used by refine_mapping().
    """
    if settings.MODEL_PROVIDER == "foundry":
        try:
            from azure.identity import DefaultAzureCredential
            from azure.ai.projects import AIProjectClient
        except Exception as exc:  # optional deps (requirements-foundry.txt)
            raise RuntimeError("azure-ai-projects / azure-identity not installed") from exc
        project = AIProjectClient(
            endpoint=settings.FOUNDRY_PROJECT_ENDPOINT,
            credential=DefaultAzureCredential(),
        )
        return project.get_openai_client()

    try:
        from openai import OpenAI
    except Exception as exc:  # SDK not installed
        raise RuntimeError("openai SDK not available") from exc
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def refine_mapping(
    target: str,
    fields: list[dict],
    column_profiles: list[dict],
    deterministic: dict,
    model: str | None = None,
) -> dict:
    """Ask the model to review/repair ambiguous mappings. Returns {canonical: {...}}.

    Never raises to the caller: on any failure it returns {} and the agent keeps the
    deterministic result.
    """
    if not settings.llm_configured():
        return {}

    model = model or settings.OPENAI_MODEL
    system_prompt = load_prompt()

    user_payload = {
        "target": target,
        "customer_file_fields": [
            {"canonical": f["canonical"], "aliases": f.get("aliases", []), "dtype": f.get("dtype")}
            for f in fields
            if f.get("source_class") == "customer_file"
        ],
        "columns": [
            {"column": p["column"], "evidence": p["evidence"], "samples": p["samples"]}
            for p in column_profiles
        ],
        "deterministic_candidates": deterministic,
        "instruction": (
            "Confirm or correct the source_column for each customer_file field. "
            "Only choose from the provided columns. Do NOT invent columns. "
            "For enrichment/constant fields, do not propose a source. "
            "Return confidence in [0,1] and a one-line note, especially for ambiguous columns."
        ),
    }

    try:
        client = _client()
        resp = client.responses.create(
            model=model,
            reasoning={"effort": settings.OPENAI_REASONING_EFFORT},
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "mapping_decisions",
                    "schema": _RESPONSE_SCHEMA,
                    "strict": True,
                }
            },
        )
        raw = resp.output_text
        data = json.loads(raw)
        out = {}
        valid_cols = {p["column"] for p in column_profiles}
        for d in data.get("decisions", []):
            col = d.get("source_column")
            if col is not None and col not in valid_cols:
                col = None  # guard against hallucinated columns
            out[d["canonical_field"]] = {
                "source_column": col,
                "confidence": float(d.get("confidence", 0.0)),
                "notes": d.get("notes", ""),
            }
        return out
    except Exception:
        # Any SDK / network / parsing error -> deterministic path stands.
        return {}
