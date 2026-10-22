"""One event, end to end: questions, then sources per question, then verbatim elements per primary source,
then a coverage number per question. Every agent decision is traced in the runs and trace_events tables."""
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from . import agent, config, db, keenable, templates

TRUTH_KINDS = ["authored_statement", "administrative_record", "measured_count", "recording", "none"]
CLASSES = ["primary", "secondary", "opinion"]

QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "bias_axis": {"type": "string"},
                    "truth_kind": {"type": "string", "enum": TRUTH_KINDS},
                    "why_contested": {"type": "string"},
                },
                "required": ["text", "bias_axis", "truth_kind", "why_contested"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

SOURCES_SCHEMA = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "holder": {"type": "string"},
                    "class": {"type": "string", "enum": CLASSES},
                    "coverage": {"type": "number"},
                    "limits": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "published_at": {"type": "string"},
                },
                "required": ["url", "title", "holder", "class", "coverage", "limits", "reasoning", "published_at"],
                "additionalProperties": False,
            },
        },
        "no_source_reason": {"type": "string"},
    },
    "required": ["sources", "no_source_reason"],
    "additionalProperties": False,
}

ELEMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "verbatim": {"type": "string"},
                    "locator": {"type": "string"},
                    "published_at": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["verbatim", "locator", "published_at", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["elements"],
    "additionalProperties": False,
}

SYSTEM = ("You are one agent of a platform that gives an unbiased view of an event. The platform never interprets: "
          "it maps questions to the sources that hold the truth for them and quotes those sources verbatim. "
          "Answer only with the JSON asked for.")

progress = {}  # event_id -> {"stage", "done", "total", "errors": []}


def start(title, description, date, place):
    """Create the event and run the pipeline in a background thread. Returns the event id."""
    eid = db.add_event(title, description, date, place)
    progress[eid] = {"stage": "questions", "done": 0, "total": 0, "errors": []}
    threading.Thread(target=run_event, args=(eid,), daemon=True).start()
    return eid


def run_event(eid):
    ev = db.event(eid)
    p = progress.setdefault(eid, {"stage": "questions", "done": 0, "total": 0, "errors": []})
    try:
        qs = questions(ev)
        p.update(stage="sources", done=0, total=len(qs))
        with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
            list(pool.map(lambda q: _guard(p, lambda: question_sources(ev, q)), qs))
        p.update(stage="done")
        db.set_event_status(eid, "done")
    except Exception as e:
        p["errors"].append(repr(e)[:300])
        p.update(stage="failed")
        db.set_event_status(eid, "failed", repr(e)[:500])


def _guard(p, fn):
    try:
        fn()
    except Exception as e:
        p["errors"].append(repr(e)[:300])
    finally:
        p["done"] += 1


def questions(ev):
    prompt = templates.render("questions", max_questions=config.MAX_QUESTIONS, **_ev(ev))
    answer, run_id = agent.run("questions", ev["id"], config.QUESTION_MODEL, SYSTEM, prompt, [], QUESTIONS_SCHEMA,
                               effort="high")
    out = []
    for i, q in enumerate(answer["questions"][: config.MAX_QUESTIONS]):
        qid = db.add_question(ev["id"], i, q, run_id)
        out.append({**q, "id": qid})
    return out


def question_sources(ev, q):
    db.set_question_status(q["id"], "searching")
    prompt = templates.render("sources", question=q["text"], bias_axis=q["bias_axis"], truth_kind=q["truth_kind"],
                              max_sources=config.MAX_SOURCES_PER_QUESTION, **_ev(ev))
    answer, run_id = agent.run("sources", q["id"], config.SOURCE_MODEL, SYSTEM, prompt, agent.web_tools(),
                               SOURCES_SCHEMA, effort="high")
    found = []
    for s in answer["sources"][: config.MAX_SOURCES_PER_QUESTION]:
        if not s["url"].startswith("http"):
            continue
        found.append((db.add_source(q["id"], s, run_id), s))
    db.set_question_status(q["id"], "extracting")
    primaries = [(sid, s) for sid, s in found if s["class"] == "primary"]
    with ThreadPoolExecutor(max_workers=max(1, len(primaries))) as pool:
        counts = list(pool.map(lambda x: _elements_safe(ev, q, *x), primaries))
    coverage = min(100.0, sum(float(s["coverage"]) for (sid, s), n in zip(primaries, counts) if n > 0))
    status = "none" if coverage == 0 else ("partial" if coverage < 70 else "covered")
    db.set_question_result(q["id"], coverage, status, answer.get("no_source_reason") or None, run_id)


def _elements_safe(ev, q, sid, s):
    try:
        return source_elements(ev, q, sid, s)
    except Exception as e:
        progress.get(ev["id"], {}).setdefault("errors", []).append(repr(e)[:300])
        return 0


def source_elements(ev, q, sid, s):
    prompt = templates.render("elements", question=q["text"], url=s["url"], holder=s["holder"], **_ev(ev))
    answer, run_id = agent.run("elements", sid, config.EXTRACT_MODEL, SYSTEM, prompt, agent.web_tools(),
                               ELEMENTS_SCHEMA, effort="medium")
    page = _page_text(s["url"])
    kept = 0
    for e in answer["elements"][:6]:
        v = e["verbatim"].strip()
        if not v:
            continue
        if page is not None and _norm(v) not in page:
            continue  # not a verbatim substring of the page: never store a paraphrase
        if page is None:
            e = {**e, "note": ("page could not be re-fetched to verify. " + e.get("note", "")).strip()}
        db.add_element(sid, q["id"], e, run_id)
        kept += 1
    return kept


def _norm(text):
    text = unicodedata.normalize("NFKC", text or "")
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'), ("\u00ab", '"'), ("\u00bb", '"'), ("\u00a0", " ")):
        text = text.replace(a, b)
    text = re.sub(r"[*_`#>\[\]]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _page_text(url):
    """The page as Keenable returns it, normalised, to check that every element is a verbatim substring."""
    try:
        return _norm(keenable.fetch(url, max_chars=100000).get("content") or "") or None
    except Exception:
        return None


def tree(eid):
    """The whole event as the page shows it."""
    ev = db.event(eid)
    if ev is None:
        return None
    qs = []
    for q in db.questions(eid):
        srcs = []
        for s in db.sources(q["id"]):
            srcs.append({**s, "elements": db.elements(s["id"])})
        qs.append({**q, "sources": srcs})
    covered = [q for q in qs if q["status"] == "covered"]
    partial = [q for q in qs if q["status"] == "partial"]
    return {
        "event": ev,
        "progress": progress.get(eid, {"stage": ev["status"], "done": 0, "total": 0, "errors": []}),
        "questions": qs,
        "stats": {
            "questions": len(qs),
            "covered": len(covered),
            "partial": len(partial),
            "none": len([q for q in qs if q["status"] == "none"]),
            "avg_coverage": round(sum(q["coverage"] for q in qs) / len(qs), 1) if qs else 0,
            "primary_sources": sum(1 for q in qs for s in q["sources"] if s["class"] == "primary"),
            "elements": sum(len(s["elements"]) for q in qs for s in q["sources"]),
        },
    }


def _ev(ev):
    return {"title": ev["title"], "description": ev["description"], "date": ev["date"], "place": ev["place"]}
