# Unbiased view of an event

Questions → sources of truth (per question) → verbatim elements. Agents never interpret; every decision has a trace.

```
cp .env.example .env            # fill ANTHROPIC_API_KEY and KEENABLE_API_KEY
.venv/bin/uvicorn unbiased.web:app --reload --port 8000     # open http://localhost:8000
.venv/bin/python -m unbiased.cli run "Manifestation à Paris du samedi 17 octobre 2026" --date 2026-10-17 --place "Paris, France"
```

unbiased/agent.py     Claude tool loop, every step traced (runs, trace_events)
unbiased/keenable.py  web search and page fetch
unbiased/pipeline.py  questions agent → sources agent per question → elements agent per primary source → coverage
unbiased/db.py        schema: events, questions, sources, elements, runs, trace_events (Postgres or SQLite)
unbiased/web.py       API: POST/GET /api/events, GET /api/events/{id}, GET /api/runs/{id}, GET /api/domains/{domain}
unbiased/prompts/     one prompt per agent
