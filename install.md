# Install — Hermes host agent (Docker)

1. `cp .env.example .env` and set `OPENAI_API_KEY` (and `LLM_MODEL` if not `gpt-4.1`)
2. `docker compose build && docker compose up -d`
3. `curl http://127.0.0.1:8080/ready`
4. `POST /v1/chat` with `{"message":"..."}`

Host prompt: `prompts/hermes_coordinator.md`
Tools: `agents/hermes_host.py` → `_host_langchain_tools()`
