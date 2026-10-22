"""Keenable web search and page fetch: the only way the agents reach the web."""
import httpx

from . import config

BASE = "https://api.keenable.ai"


def _headers():
    return {"X-API-Key": config.KEENABLE_API_KEY}


def search(query, published_after=None, max_results=10, mode="pro"):
    body = {"query": query, "mode": mode, "max_results": max_results}
    if published_after:
        body["published_after"] = published_after
    r = httpx.post(f"{BASE}/v1/search", json=body, headers=_headers(), timeout=60)
    r.raise_for_status()
    return [
        {
            "title": x.get("title"),
            "url": x.get("url"),
            "published_at": x.get("published_at"),
            "snippet": x.get("snippet") or x.get("description"),
        }
        for x in r.json().get("results", [])
    ]


def fetch(url, prompt=None, max_chars=20000):
    params = {"url": url, "max_chars": max_chars}
    if prompt:
        params["prompt"] = prompt
    r = httpx.get(f"{BASE}/v1/fetch", params=params, headers=_headers(), timeout=90)
    r.raise_for_status()
    d = r.json()
    return {"url": d.get("url"), "title": d.get("title"), "published_at": d.get("published_at"), "content": d.get("content")}
