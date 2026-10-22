"""The schema and every read and write. Postgres when DATABASE_URL is set, a local SQLite file otherwise.

SQL is written once with %s placeholders; JSON columns are text; timestamps are ISO strings made in Python,
so the same statements run on both engines.
"""
import datetime
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager

from . import config

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS events (
        id text PRIMARY KEY, title text NOT NULL, description text NOT NULL DEFAULT '',
        date text NOT NULL DEFAULT '', place text NOT NULL DEFAULT '',
        status text NOT NULL DEFAULT 'running', error text, created_at text NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS questions (
        id text PRIMARY KEY, event_id text NOT NULL, position integer NOT NULL, text text NOT NULL,
        bias_axis text NOT NULL, truth_kind text NOT NULL, why_contested text NOT NULL DEFAULT '',
        coverage real NOT NULL DEFAULT 0, status text NOT NULL DEFAULT 'pending',
        no_source_reason text, run_id text, created_at text NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sources (
        id text PRIMARY KEY, question_id text NOT NULL, url text NOT NULL, domain text NOT NULL,
        title text NOT NULL DEFAULT '', holder text NOT NULL, class text NOT NULL,
        coverage real NOT NULL DEFAULT 0, limits text NOT NULL DEFAULT '', reasoning text NOT NULL DEFAULT '',
        published_at text, run_id text, fetched_at text NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS elements (
        id text PRIMARY KEY, source_id text NOT NULL, question_id text NOT NULL,
        verbatim text NOT NULL, locator text NOT NULL DEFAULT '', published_at text,
        note text NOT NULL DEFAULT '', run_id text, created_at text NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS runs (
        id text PRIMARY KEY, kind text NOT NULL, subject_id text NOT NULL, model text NOT NULL,
        started_at text NOT NULL, finished_at text, status text NOT NULL DEFAULT 'running', error text,
        input_tokens integer NOT NULL DEFAULT 0, output_tokens integer NOT NULL DEFAULT 0,
        tool_calls integer NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS trace_events (
        run_id text NOT NULL, seq integer NOT NULL, at text NOT NULL, type text NOT NULL,
        payload text NOT NULL, PRIMARY KEY (run_id, seq))""",
]

PG = bool(config.DATABASE_URL)
_lock = threading.Lock()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def new_id():
    return str(uuid.uuid4())


@contextmanager
def connect():
    if PG:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(config.DATABASE_URL, row_factory=dict_row, autocommit=True) as conn:
            if config.DB_SCHEMA:
                conn.execute(f'SET search_path TO "{config.DB_SCHEMA}"')
            yield conn
    else:
        with _lock:
            conn = sqlite3.connect(config.SQLITE_PATH, timeout=30)
            conn.row_factory = lambda c, r: {d[0]: r[i] for i, d in enumerate(c.description)}
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()


def _q(sql):
    return sql if PG else sql.replace("%s", "?")


def init():
    with connect() as conn:
        if PG and config.DB_SCHEMA:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{config.DB_SCHEMA}"')
        for stmt in SCHEMA:
            conn.execute(stmt)


def all_rows(sql, *params):
    with connect() as conn:
        return conn.execute(_q(sql), params).fetchall()


def one_row(sql, *params):
    with connect() as conn:
        return conn.execute(_q(sql), params).fetchone()


def execute(sql, *params):
    with connect() as conn:
        conn.execute(_q(sql), params)


# --- events ---

def add_event(title, description, date, place):
    eid = new_id()
    execute("INSERT INTO events (id, title, description, date, place, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            eid, title, description, date, place, now())
    return eid


def set_event_status(eid, status, error=None):
    execute("UPDATE events SET status = %s, error = %s WHERE id = %s", status, error, eid)


def events():
    return all_rows("SELECT * FROM events ORDER BY created_at DESC")


def event(eid):
    return one_row("SELECT * FROM events WHERE id = %s", eid)


# --- questions ---

def add_question(eid, position, q, run_id):
    qid = new_id()
    execute("""INSERT INTO questions (id, event_id, position, text, bias_axis, truth_kind, why_contested, run_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            qid, eid, position, q["text"], q["bias_axis"], q["truth_kind"], q.get("why_contested", ""), run_id, now())
    return qid


def set_question_result(qid, coverage, status, no_source_reason, run_id):
    execute("UPDATE questions SET coverage = %s, status = %s, no_source_reason = %s, run_id = %s WHERE id = %s",
            coverage, status, no_source_reason, run_id, qid)


def set_question_status(qid, status):
    execute("UPDATE questions SET status = %s WHERE id = %s", status, qid)


def questions(eid):
    return all_rows("SELECT * FROM questions WHERE event_id = %s ORDER BY position", eid)


# --- sources and elements ---

def add_source(qid, s, run_id):
    sid = new_id()
    domain = s["url"].split("/")[2] if "://" in s["url"] else s["url"]
    execute("""INSERT INTO sources (id, question_id, url, domain, title, holder, class, coverage, limits, reasoning,
               published_at, run_id, fetched_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            sid, qid, s["url"], domain, s.get("title", ""), s["holder"], s["class"], float(s.get("coverage", 0)),
            s.get("limits", ""), s.get("reasoning", ""), s.get("published_at") or None, run_id, now())
    return sid


def sources(qid):
    return all_rows("SELECT * FROM sources WHERE question_id = %s ORDER BY class, coverage DESC", qid)


def source(sid):
    return one_row("SELECT * FROM sources WHERE id = %s", sid)


def add_element(sid, qid, e, run_id):
    execute("""INSERT INTO elements (id, source_id, question_id, verbatim, locator, published_at, note, run_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            new_id(), sid, qid, e["verbatim"], e.get("locator", ""), e.get("published_at") or None,
            e.get("note", ""), run_id, now())


def elements(sid):
    return all_rows("SELECT * FROM elements WHERE source_id = %s ORDER BY created_at", sid)


def domain_history(domain):
    """Every classification of one domain across questions: the same source rated differently per question."""
    return all_rows("""SELECT s.domain, s.class, s.coverage, s.holder, q.text AS question, e.title AS event
                       FROM sources s JOIN questions q ON q.id = s.question_id JOIN events e ON e.id = q.event_id
                       WHERE s.domain = %s ORDER BY s.fetched_at""", domain)


# --- runs and traces ---

def start_run(run_id, kind, subject_id, model):
    execute("INSERT INTO runs (id, kind, subject_id, model, started_at) VALUES (%s, %s, %s, %s, %s)",
            run_id, kind, subject_id, model, now())


def finish_run(run_id, status, usage, error=None):
    execute("""UPDATE runs SET finished_at = %s, status = %s, error = %s,
               input_tokens = %s, output_tokens = %s, tool_calls = %s WHERE id = %s""",
            now(), status, error, usage["input_tokens"], usage["output_tokens"], usage["tool_calls"], run_id)


def add_event_trace(run_id, seq, type_, payload):
    execute("INSERT INTO trace_events (run_id, seq, at, type, payload) VALUES (%s, %s, %s, %s, %s)",
            run_id, seq, now(), type_, json.dumps(payload, default=str, ensure_ascii=False))


def run(run_id):
    return one_row("SELECT * FROM runs WHERE id = %s", run_id)


def trace(run_id):
    rows = all_rows("SELECT seq, at, type, payload FROM trace_events WHERE run_id = %s ORDER BY seq", run_id)
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows


def runs(limit=100):
    return all_rows("SELECT * FROM runs ORDER BY started_at DESC LIMIT %s", limit)
