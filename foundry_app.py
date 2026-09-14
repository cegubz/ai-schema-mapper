"""Microsoft Foundry — Hosted Agent entrypoint (Invocations protocol).

Thin HTTP wrapper over agent.run_agent, the Foundry counterpart of function_app.py.
The entire core (core/, agent.py, configs, prompt) is unchanged — deploying to Foundry
only swaps the entrypoint and (optionally) the model gateway via MODEL_PROVIDER=foundry.

Deploy (recommended):
    az login
    azd ai agent init      # scaffolds/updates the Foundry agent definition + RBAC
    azd up                 # source-ZIP or container build, deploy, wire the endpoint

Deploy (container, manual):
    docker build --platform linux/amd64 -t <acr>.azurecr.io/fmg-agent:latest .
    docker push <acr>.azurecr.io/fmg-agent:latest
    # then register the image as a Hosted Agent (SDK/REST/azd)

The Invocations protocol delivers your custom JSON payload to this server. The exact
envelope can vary by Foundry version, so _extract_payload() accepts either the raw agent
request contract (see agent.py) or a wrapped {"payload": {...}} / {"input_data": {...}}
envelope. Confirm the current shape against the Foundry quickstart if you hit a mismatch.
"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent import run_agent

app = FastAPI(title="FMG Schema-Mapping Agent", version="1.0.0")


def _extract_payload(body: dict) -> dict:
    """Unwrap the agent request contract from whatever envelope Foundry delivers."""
    if not isinstance(body, dict):
        return {}
    if "input" in body:  # already the raw contract
        return body
    for key in ("payload", "input_data", "body", "data"):
        inner = body.get(key)
        if isinstance(inner, dict):
            return inner
    return body


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/invocations")
async def invocations(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "failed", "error": "Body must be valid JSON"}, status_code=400
        )
    payload = _extract_payload(body)
    result = run_agent(payload)
    code = 200 if result.get("status") == "succeeded" else 500
    return JSONResponse(result, status_code=code)


if __name__ == "__main__":
    # Local dev:  uvicorn foundry_app:app --host 0.0.0.0 --port 8088
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8088")))
