"""One agent run: a tool loop on the Messages API that ends with a JSON answer, every step traced to Postgres."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import anthropic

from . import db, keenable

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client

MAX_TURNS = 40
TRACE_RESULT_CHARS = 4000


class AgentError(Exception):
    pass


def tool(name, description, properties, required, handler):
    return {
        "spec": {
            "name": name,
            "description": description,
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
        "handler": handler,
    }


def web_tools():
    """Keenable search and fetch, shared by every agent."""
    return [
        tool(
            "web_search",
            "Search the web with Keenable. Returns up to 10 results with title, url, publication date and snippet. "
            "published_after (YYYY-MM-DD, or empty for no filter) keeps only pages published after that day.",
            {"query": {"type": "string"}, "published_after": {"type": "string"}},
            ["query", "published_after"],
            lambda a: keenable.search(a["query"], a["published_after"] or None),
        ),
        tool(
            "web_fetch",
            "Open a web page with Keenable and return its content as markdown. "
            "Give an extraction instruction in prompt to get only what you need from a long page, or empty for the page.",
            {"url": {"type": "string"}, "prompt": {"type": "string"}},
            ["url", "prompt"],
            lambda a: keenable.fetch(a["url"], a["prompt"] or None),
        ),
    ]


def run(kind, subject_id, model, system, prompt, tools, output_schema, effort="medium"):
    """Run the agent to its final answer and return (answer dict, run_id). Raises AgentError on failure."""
    run_id = str(uuid.uuid4())
    db.start_run(run_id, kind, subject_id, model)
    usage = {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0}
    seq = 0

    def event(type_, payload):
        nonlocal seq
        seq += 1
        db.add_event_trace(run_id, seq, type_, payload)

    handlers = {t["spec"]["name"]: t["handler"] for t in tools}
    messages = [{"role": "user", "content": prompt}]
    event("prompt", {"system": system, "prompt": prompt})
    try:
        for _ in range(MAX_TURNS):
            response = client().beta.messages.create(
                model=model,
                max_tokens=16000,
                system=system,
                messages=messages,
                **({"tools": [t["spec"] for t in tools]} if tools else {}),
                thinking={"type": "adaptive"},
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": output_schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
            for block in response.content:
                if block.type == "text" and response.stop_reason == "tool_use":
                    event("assistant_text", {"text": block.text})
                elif block.type == "fallback":
                    event("fallback", {"from": block.from_.model, "to": block.to.model})

            if response.stop_reason == "refusal":
                raise AgentError(f"refused: {response.stop_details}")
            if response.stop_reason == "max_tokens":
                raise AgentError("hit max_tokens")
            if response.stop_reason != "tool_use":
                text = next(b.text for b in response.content if b.type == "text")
                answer = json.loads(text)
                event("final", answer)
                db.finish_run(run_id, "done", usage)
                return answer, run_id

            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            usage["tool_calls"] += len(calls)
            with ThreadPoolExecutor(max_workers=len(calls)) as pool:
                results = list(pool.map(lambda b: _call(handlers, b), calls))
            for b, (content, is_error) in zip(calls, results):
                event("tool_call", {"name": b.name, "input": b.input, "is_error": is_error,
                                    "result": content[:TRACE_RESULT_CHARS]})
            messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "content": content, "is_error": is_error}
                    for b, (content, is_error) in zip(calls, results)
                ],
            })
        raise AgentError(f"no answer after {MAX_TURNS} turns")
    except Exception as e:
        event("error", {"error": repr(e)})
        db.finish_run(run_id, "failed", usage, repr(e))
        raise AgentError(f"{kind} {subject_id}: {e!r}") from e


def _call(handlers, block):
    try:
        return json.dumps(handlers[block.name](block.input), default=str, ensure_ascii=False), False
    except Exception as e:
        return f"Error: {e!r}", True
