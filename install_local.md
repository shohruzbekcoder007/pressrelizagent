# Local install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
python -m app.main
```

Open http://127.0.0.1:8080/docs

Only the `echo` example tool is registered — add your own in
`agents/hermes_host.py` → `_host_langchain_tools()`.
