"""The web app: a JSON API over the database plus the single page in static/."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, pipeline

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    db.init()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


class NewEvent(BaseModel):
    title: str
    description: str = ""
    date: str = ""
    place: str = ""


@app.get("/api/events")
def events():
    return db.events()


@app.post("/api/events")
def create_event(e: NewEvent):
    return {"id": pipeline.start(e.title, e.description, e.date, e.place)}


@app.get("/api/events/{eid}")
def event(eid: str):
    t = pipeline.tree(eid)
    if t is None:
        raise HTTPException(404)
    return t


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    r = db.run(run_id)
    if r is None:
        raise HTTPException(404)
    return {"run": r, "events": db.trace(run_id)}


@app.get("/api/domains/{domain}")
def domain(domain: str):
    return db.domain_history(domain)
