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



Hermes resolves its own provider credentials from the environment — no

interactive `hermes login` or `hermes setup` step, so deployment stays a

single command. See **LLM provider** below for which variables it reads.



## LLM provider



`LLM_PROVIDER` is the only switch. It picks one block of `.env`; the other

block sits there untouched, so an OpenAI key and a local server can coexist

and neither leaks into the other.



```env

LLM_PROVIDER=openai            # openai | ollama | vllm | lmstudio



# 1: OpenAI

OPENAI_API_KEY=sk-...

OPENAI_BASE_URL=https://api.openai.com/v1

LLM_MODEL=gpt-4.1

HERMES_TASK_MODEL=gpt-4.1-mini



# 2: local OpenAI-compatible server (Ollama / vLLM / LM Studio)

OLLAMA_BASE_URL=http://host.docker.internal:11434/v1

OLLAMA_MODEL=qwen3.8:27b

OLLAMA_TASK_MODEL=qwen3:8b

```



| `LLM_PROVIDER` | Key | Endpoint | Model | Key required |

|---|---|---|---|---|

| `openai` | `OPENAI_API_KEY` | `OPENAI_BASE_URL` | `LLM_MODEL` | yes |

| `ollama` | `OLLAMA_API_KEY` | `OLLAMA_BASE_URL` | `OLLAMA_MODEL` | no |

| `vllm` | `OLLAMA_API_KEY` | `OLLAMA_BASE_URL` | `OLLAMA_MODEL` | no |

| `lmstudio` | `OLLAMA_API_KEY` | `OLLAMA_BASE_URL` | `OLLAMA_MODEL` | no |



The three local providers share one env block on purpose — point

`OLLAMA_BASE_URL` at whichever server is running. They differ only in the

provider profile handed to Hermes, and that profile is not cosmetic: the

`ollama` one sends `think=false`, detects `num_ctx` and lifts the `max_tokens`

floor. Without it Ollama truncates every reply at its internal

`num_predict=128` default.

**Reasoning models.** A thinking model (Qwen3, DeepSeek-R1, …) served over an
OpenAI-compatible endpoint puts its thoughts in a separate `reasoning` field
and leaves `content` empty until it stops thinking. If the token budget runs
out mid-thought the reply comes back genuinely blank. Keep
`HERMES_REASONING_ENABLED=false`: Hermes then sends `reasoning_effort="none"`
plus `think=false`, and the `pressreliz` tools send the same pair on any

local profile.
Verify a new endpoint honours it before trusting it — some builds ignore one
flag or the other:

```bash
curl -s $OLLAMA_BASE_URL/chat/completions -H 'Content-Type: application/json'   -d '{"model":"'"$OLLAMA_MODEL"'","messages":[{"role":"user","content":"Reply with exactly: OK"}],
       "max_tokens":128,"reasoning_effort":"none","think":false}'
```

A `"reasoning": null` in the response means thinking is really off.



`HERMES_INFERENCE_PROVIDER` follows `LLM_PROVIDER` automatically; set it only

to override. From Docker, the host machine's Ollama is reachable at

`host.docker.internal` — compose maps it for Linux hosts too.



The model must support tool calling, since the host agent is a tool-calling

loop (`ollama show <model>` lists `tools` under Capabilities).



### Task model



A chat UI runs small jobs behind the scenes — Open WebUI generates chat

titles, tags and follow-up suggestions by sending a prompt marked `### Task:`

through the normal chat route. When `HERMES_TASK_MODEL` (OpenAI) or

`OLLAMA_TASK_MODEL` (local) is set, those go to that model in a single call:

no tools, no history, no memory writes. Everything else reaches the full

agent. If the task model errors, the request falls through to the main agent

rather than failing.



Unset the variable, or set `HERMES_TASK_ROUTING=false`, and the main model

answers them as before.



## Hermes toolsets



`HERMES_ENABLED_TOOLSETS` selects which of Hermes' 59 toolsets the host agent

gets. The default enables persistence and self-improvement, and nothing that

reaches outside the container:



| Toolset | Tools | What it gives the agent |

|---|---|---|

| `memory` | `memory` | durable facts, re-injected into every later turn |

| `session_search` | `session_search` | recall and summarize past conversations |

| `skills` | `skill_manage`, `skill_view`, `skills_list` | write and revise its own skill documents |

| `todo` | `todo` | plan multi-step work |

| `pressreliz` | `statind_code` | this repo's own tool — see below |



Everything Hermes learns lives under `HERMES_HOME` — `memories/`, `skills/`,

`sessions/`, `state.db`, `SOUL.md` — kept in the `pressrelizagent-hermes-home` named

volume so a redeploy does not wipe it. `config/hermes_config.yaml` is copied in

only when the volume has no `config.yaml` yet; delete the volume to re-seed it.



Toolsets such as `terminal`, `code_execution`, `file` and `browser` let the

agent act inside the container. Set `API_BEARER_TOKEN` before enabling any of

them — `/v1/chat` is unauthenticated while it is unset.



### The `pressreliz` toolset

This repo's own toolset, registered by `plugins/pressreliz/`. Adding a tool is
one entry in `_TOOLS` in `__init__.py`.

**`statind_code`** searches the Uzbek statistical indicator register and returns
the closest **5 rows** for each name given — `id`, `kod`, `nomi`, `yol` (path),
`daraja`, `davriylik` and a `moslik` score. Neo4j's `indicator_fulltext` index
covers Uzbek latin, Uzbek cyrillic and Russian names plus the classifier path,
so a Russian query finds the Uzbek row; the standard analyzer splits on the
apostrophe, so the register's four spellings of `o'`/`o‘`/`oʻ`/`oʼ` all match.

**The tool does not choose.** It hands the agent candidates and the agent picks,
because the agent has the conversation and the tool does not. This is also what
makes an invented code impossible: every code it emits was read out of the
register a moment earlier. Classifier codes are regular enough (`1.01.01.0001`)
that a model will happily invent a plausible one, and a wrong code in a press
release is a published error. Passing a code *in* returns that exact row, which
is how a code quoted from elsewhere gets verified.

Retrieval rather than a prompt-stuffed register is a measured choice. The flat
classifier is 146,710 tokens — 56% of this model's 262k context, ~52s on a cold
cache — against a few hundred tokens for a five-row shortlist. A search returns
in well under a second.

Two properties of the register the caller has to know about, both stated in the
tool description:

- Periodicity (`yillik` / `oylik` / `choraklik`) and cross-sections
  (`hududlar kesimida`, …) are **separate indicators with separate codes**, and
  the same indicator can appear in more than one section — compare `yol` before
  choosing. The register also holds genuine duplicates (two rows are both
  `Eksport hajmi (oylik)`).
- The search is keyword-based, so it cannot expand abbreviations (`YaIM` finds
  nothing) and Uzbek case endings are not stemmed (`mahsulotning` does not match
  `mahsulot`). The agent normalizes the name before searching. Numbers are
  stripped from the query automatically, because a year quoted from a press
  release otherwise drags the shortlist onto the rows whose *names* contain
  years.

`statind_code` needs Neo4j running; when the register is unreachable it says
so and stops, rather than falling back to a guess.

## Neo4j



A `neo4j` service ships alongside the app as the knowledge-graph store.



| | Host | In the compose network |

|---|---|---|

| Browser (HTTP) | http://localhost:7476 | `neo4j:7474` |

| Bolt | `bolt://localhost:7688` | `bolt://neo4j:7687` |



The published ports are 7476/7688 rather than the usual 7474/7687 because

other projects on this machine already hold those. The app talks to Neo4j

**inside** the network, so it uses 7687 regardless — see `NEO4J_URI`.



Credentials come from `NEO4J_USER` / `NEO4J_PASSWORD`; compose assembles

Neo4j's own `NEO4J_AUTH` from the same two values, so the app and the database

cannot drift apart. APOC is enabled.



Graph data lives in the `pressrelizagent-neo4j-data` named volume. Two host

folders are bind-mounted so files can be dropped in from Windows:



| Host folder | In container | For |

|---|---|---|

| `data/neo4j/import/` | `/var/lib/neo4j/import` | CSV files for `LOAD CSV` / apoc |

| `data/neo4j/dumps/` | `/dumps` | `.dump` files to restore |



### Restoring a dump



Put the file in `data/neo4j/dumps/` (as `<database>.dump` — `neo4j.dump` for

the default database), then:



```powershell

.\scripts\restore_dump.ps1
```



Add `-Reset` after changing `NEO4J_IMAGE` to a different edition or major

version — see below for why.



The script stops the server, runs `neo4j-admin database load` in a throwaway

container against the same volumes, starts the server again, waits for the

database to come online and prints the node/relationship counts. The equivalent

by hand:



```powershell

docker compose stop neo4j

docker compose run --rm --entrypoint neo4j-admin neo4j `

    database load neo4j --from-path=/dumps --overwrite-destination=true

docker compose start neo4j

```



Two failure modes worth knowing, both hit while loading the kgdb dump:



**Run it from PowerShell, not Git Bash.** Git Bash rewrites the container path

`--from-path=/dumps` into `C:/Program Files/Git/dumps`, and the load dies with

`is not an existing directory`.



**Changing edition or major version needs a fresh data volume** (`-Reset`).

A `system` database written by one edition leaves the graph reporting

`Database not currently allocated to any servers`, and `ALTER DATABASE` cannot

fix it — allocation is a precondition for altering, so it is a deadlock. Only

deleting `pressrelizagent-neo4j-data` and letting Neo4j rebuild `system`

clears it.



A load can also be refused outright with *"newer version than the current

binaries"*: a dump only moves forward across versions, never back. Raise

`NEO4J_IMAGE` to match the server that produced it.



Memory is deliberately conservative (`NEO4J_HEAP_MAX=1G`,

`NEO4J_PAGECACHE=512m`) because this machine also runs Ollama with a 27B

model. Raise both in `.env` once the real graph is loaded.



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

| GET | `/v1/info` | backend, provider, model, task model, registered tools |

| POST | `/v1/chat` | chat; `{"message": "...", "session_id": "...", "reset_session": false}` |

| GET | `/docs` | OpenAPI UI |



Set `API_BEARER_TOKEN` to require `Authorization: Bearer …` on `/v1/chat`.



## Run



Local: see [install_local.md](install_local.md) · Docker: see [install.md](install.md)



```bash

curl -s localhost:7100/v1/chat -H 'content-type: application/json' \

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

| `LLM_PROVIDER` | `openai` | `openai` \| `ollama` \| `vllm` \| `lmstudio` |

| `OPENAI_API_KEY` | — | required when `LLM_PROVIDER=openai` |

| `OPENAI_BASE_URL` | OpenAI | any compatible gateway |

| `LLM_MODEL` | `gpt-4.1` | also `HERMES_MODEL`, `OPENAI_MODEL` |

| `HERMES_TASK_MODEL` | — | small model for `### Task:` prompts |

| `OLLAMA_BASE_URL` | `localhost:11434/v1` | local server; used by all three local providers |

| `OLLAMA_MODEL` | `qwen3:8b` | must support tool calling |

| `OLLAMA_TASK_MODEL` | — | small model for `### Task:` prompts |

| `OLLAMA_API_KEY` | — | only behind an authenticating proxy |

| `HERMES_TASK_ROUTING` | `true` | `false` = never route to the task model |

| `HERMES_INFERENCE_PROVIDER` | from `LLM_PROVIDER` | override only |

| `HERMES_SYSTEM_PROMPT_PATH` | `prompts/hermes_coordinator.md` | host prompt |

| `HERMES_LOAD_SOUL_IDENTITY` | `true` | load `prompts/soul.md` as the agent identity |

| `HERMES_ENABLED_TOOLSETS` | `memory,session_search,skills,todo` | comma-separated Hermes toolsets |

| `HERMES_MAX_ITERATIONS` | `12` | tool-loop cap |
| `HERMES_SESSION_HISTORY_LIMIT` | `6` | turns kept per session |

| `HERMES_SKIP_MEMORY` | `false` | `true` = stateless |

| `HERMES_REASONING_ENABLED` | `false` | keep `false` on gpt-4* |

| `API_BEARER_TOKEN` | — | unset = no auth |

| `CORS_ORIGINS` | `*` | comma-separated |

| `NEO4J_URI` | `bolt://neo4j:7687` | in-network address; container port, not published |

| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `pressreliz-neo4j` | also assembled into `NEO4J_AUTH` |

| `NEO4J_HTTP_PORT` / `NEO4J_BOLT_PORT` | `7476` / `7688` | published ports (7474/7687 taken) |

| `NEO4J_HEAP_MAX` / `NEO4J_PAGECACHE` | `1G` / `512m` | raise for a large graph |



Sessions are in-process and per-worker: `API_WORKERS>1` will split them.

