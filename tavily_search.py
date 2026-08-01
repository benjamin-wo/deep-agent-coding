"""Tavily web-search API helper for the agent.

Uses Tavily's REST API (https://docs.tavily.com) with the TAVILY_API_KEY env
var. This module is deliberately free of langchain imports so unit tests can
exercise the API layer without the full dependency stack; the LangChain tool
wrapper lives in agent.py.
"""

import os

import httpx

TAVILY_ENDPOINT = "https://api.tavily.com/search"


def tavily_search(
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    include_answer: bool = True,
    api_key: str | None = None,
) -> dict:
    """Run a Tavily search and return the parsed result.

    Returns a dict with keys: query, answer, results (list of
    {title, url, content, score}), response_time.
    Raises ValueError if TAVILY_API_KEY is missing, or httpx errors on failure.
    """
    key = api_key or os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise ValueError("TAVILY_API_KEY is not configured in environment variables.")

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            TAVILY_ENDPOINT,
            json={
                "api_key": key,
                "query": query,
                "search_depth": search_depth if search_depth in ("basic", "advanced") else "basic",
                "max_results": max(1, min(int(max_results), 10)),
                "include_answer": bool(include_answer),
                "include_raw_content": False,
            },
        )
    resp.raise_for_status()
    data = resp.json()

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
            "score": r.get("score"),
        }
        for r in (data.get("results") or [])
    ]
    return {
        "query": data.get("query", query),
        "answer": data.get("answer", ""),
        "results": results,
        "response_time": data.get("response_time"),
    }


def format_search_results(result: dict, max_results: int = 5) -> str:
    """Render a tavily_search result as a readable string for the agent."""
    lines = [f"Search: {result.get('query')}"]
    answer = result.get("answer")
    if answer:
        lines.append(f"Summary: {answer}")
    for i, r in enumerate(result.get("results", [])[:max_results], 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['content'][:400]}")
    if not result.get("results"):
        lines.append("(no results)")
    return "\n".join(lines)
