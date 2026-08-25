# Hermes host agent — starter

A minimal, production-shaped FastAPI service around a **Hermes host agent**:
conversation, multi-turn session memory, and a tool-calling loop. No domain
logic — add your own tools and build from here.

## Architecture

```
Gateway (Open WebUI, …) → POST /v1/chat
    → Hermes host agent  (system prompt + session history)
        └─ tools from agents/hermes_host.py::_host_langchain_tools()
```

The real Hermes framework is a hard requirement. Its build backend refuses pip
wheel builds by design, so the image clones the source to `/opt/hermes-agent`
and puts it on `PYTHONPATH` rather than installing it. The build then verifies
the import and **fails** if it is not usable — an image never ships silently
without Hermes.

Two backends, chosen automatically at startup:

| Backend | When | What |
|---|---|---|
| `hermes` | normal operation | real Hermes `AIAgent` — plugins, toolsets, its own conversation loop |
| `hermes_lite` | safety net if the above cannot start | LangGraph `create_react_agent` with the same design |

Hermes resolves its own provider credentials. `HERMES_INFERENCE_PROVIDER`
together with `OPENAI_API_KEY` / `OPENAI_BASE_URL` is all it needs — no
interactive `hermes login` or `hermes setup` step, so deployment stays a
single command.

## Layout

```
agents/
  hermes_host.py    host agent: sessions, backends, tool loop
  example_tool.py   template tool (`echo`) — copy this for your own
app/
  api.py            FastAPI routes
  main.py           process entrypoint (uvicorn)
  logging_setup.py  JSON logging
config/
  hermes_config.yaml  Hermes profile (plugins/toolsets off by default)
  logging.yaml
prompts/
  hermes_coordinator.md  host system prompt
scripts/
  start.sh          container entrypoint
  healthcheck.sh
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness — never touches the LLM |
| GET | `/ready` | host readiness; `503` when not ready |
| GET | `/v1/info` | backend, model, registered tools |
| POST | `/v1/chat` | chat; `{"message": "...", "session_id": "...", "reset_session": false}` |
| GET | `/docs` | OpenAPI UI |

Set `API_BEARER_TOKEN` to require `Authorization: Bearer …` on `/v1/chat`.

## Run

Local: see [install_local.md](install_local.md) · Docker: see [install.md](install.md)

```bash
curl -s localhost:8080/v1/chat -H 'content-type: application/json' \
  -d '{"message":"salom"}'
```

## Adding a tool

1. Copy `agents/example_tool.py`, rename the function, write a real docstring —
   the LLM reads it to decide when to call the tool.
2. Register it in `agents/hermes_host.py` → `_host_langchain_tools()`.
3. Describe it in `prompts/hermes_coordinator.md` under **Tools**.

## Configuration

All via environment (`.env`, see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | — | required; host is not ready without it |
| `LLM_MODEL` | `gpt-4.1` | also `HERMES_MODEL`, `OPENAI_MODEL` |
| `OPENAI_BASE_URL` | OpenAI | point at any compatible gateway |
| `HERMES_INFERENCE_PROVIDER` | `openai` | provider Hermes resolves credentials for |
| `HERMES_SYSTEM_PROMPT_PATH` | `prompts/hermes_coordinator.md` | host prompt |
| `HERMES_ENABLED_TOOLSETS` | *(empty)* | comma-separated Hermes toolsets |
| `HERMES_MAX_ITERATIONS` | `12` | tool-loop cap |
| `HERMES_SESSION_HISTORY_LIMIT` | `6` | turns kept per session |
| `HERMES_SKIP_MEMORY` | `false` | `true` = stateless |
| `HERMES_REASONING_ENABLED` | `false` | keep `false` on gpt-4* |
| `API_BEARER_TOKEN` | — | unset = no auth |
| `CORS_ORIGINS` | `*` | comma-separated |

Sessions are in-process and per-worker: `API_WORKERS>1` will split them.
