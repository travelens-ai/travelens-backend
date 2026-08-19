"""News feed service.

Fetches the Google News RSS search feed for a query and converts the XML
response into JSON. Google News only exposes RSS/XML (no JSON API), so we parse
the feed with the stdlib XML parser and reshape <item> entries into dicts.
"""
import threading
import time
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests as http_requests

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
_TIMEOUT = 10  # seconds

# Cache feeds briefly — the same query is often polled repeatedly and Google
# rate-limits aggressive callers.
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300  # 5 minutes


def _text(item, tag):
    """Return the text of a child tag, or None if absent/empty."""
    el = item.find(tag)
    if el is None:
        return None
    return (el.text or "").strip() or None


def _parse_feed(xml_bytes):
    """Convert a Google News RSS document into a JSON-friendly dict."""
    root = ElementTree.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        return {"feed": {}, "articles": []}

    articles = []
    for item in channel.findall("item"):
        source_el = item.find("source")
        articles.append({
            "title": _text(item, "title"),
            "link": _text(item, "link"),
            "published": _text(item, "pubDate"),
            "description": _text(item, "description"),
            "source": (source_el.text or "").strip() if source_el is not None else None,
        })

    return {
        "feed": {
            "title": _text(channel, "title"),
            "description": _text(channel, "description"),
            "language": _text(channel, "language"),
        },
        "articles": articles,
    }


def get_news(query):
    """Fetch and parse the Google News RSS feed for `query`.

    Returns (result_dict, error_string). On success error is None.
    """
    query = (query or "").strip()
    if not query:
        return None, "query (q) is required"

    now = time.time()
    with _cache_lock:
        cached = _cache.get(query)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1], None

    url = f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}"
    try:
        resp = http_requests.get(
            url,
            timeout=_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (TraveLens NewsBot)"},
        )
        resp.raise_for_status()
    except http_requests.RequestException as exc:
        return None, f"news feed unavailable: {exc}"

    try:
        result = _parse_feed(resp.content)
    except ElementTree.ParseError as exc:
        return None, f"failed to parse news feed: {exc}"

    result["query"] = query
    result["count"] = len(result["articles"])

    with _cache_lock:
        _cache[query] = (now, result)

    return result, None
