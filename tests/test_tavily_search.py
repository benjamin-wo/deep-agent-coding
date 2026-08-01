import os

import httpx
import pytest

import tavily_search as ts_mod
from tavily_search import format_search_results, tavily_search

os.environ["TAVILY_API_KEY"] = "test-tavily-key"


def _mock_client(requests: list, real_client):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == "https://api.tavily.com/search"
        return httpx.Response(
            200,
            json={
                "query": "deepseek pricing",
                "answer": "DeepSeek offers a cheap API.",
                "results": [
                    {"title": "DeepSeek Docs", "url": "https://api-docs.deepseek.com", "content": "Pricing per token.", "score": 0.95},
                    {"title": "Example", "url": "https://example.com", "content": "Some content here.", "score": 0.8},
                ],
                "response_time": 1.23,
            },
        )

    return real_client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    requests: list = []
    real_client = httpx.Client
    monkeypatch.setattr(ts_mod.httpx, "Client", lambda **kw: _mock_client(requests, real_client))
    yield requests


def test_search_returns_parsed_results(_patch_client):
    result = tavily_search("deepseek pricing")
    assert result["query"] == "deepseek pricing"
    assert result["answer"] == "DeepSeek offers a cheap API."
    assert len(result["results"]) == 2
    assert result["results"][0]["title"] == "DeepSeek Docs"
    assert result["results"][0]["url"] == "https://api-docs.deepseek.com"


def test_search_posts_api_key(_patch_client):
    tavily_search("hello")
    sent = _patch_client[0]
    body = sent.read().decode() if sent.content else ""
    assert "test-tavily-key" in body


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY")
    with pytest.raises(ValueError):
        tavily_search("hi")


def test_format_search_results():
    result = {
        "query": "q",
        "answer": "summary",
        "results": [
            {"title": "T", "url": "https://t", "content": "c" * 500},
        ],
    }
    out = format_search_results(result)
    assert "Search: q" in out
    assert "Summary: summary" in out
    assert "T" in out
    assert "https://t" in out
    assert "c" * 400 in out
    assert "c" * 401 not in out


def test_format_no_results():
    out = format_search_results({"query": "q", "answer": "", "results": []})
    assert "(no results)" in out
