"""Web tools: let Aali fetch a page and do a keyless web search.

No API key is required (a DuckDuckGo HTML search endpoint is used, the same
trick many no-key local agents rely on) — nothing here ever asks the user
for a search-API token. Every request is capped in size/time so a bad page
cannot hang the agent loop or blow past the context window.
"""

from __future__ import annotations

import re
import socket
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import requests

# Reuse file_tools' own error type so execute_tool's single
# `except FileAgentError` handler covers these tools too (safe here: FileAgentError
# is defined before file_tools imports this module, so the circular import resolves).
from .file_tools import FileAgentError  # noqa: E402

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AaliAgent/1.0"
_MAX_FETCH_CHARS = 20_000
_TIMEOUT = 15


def _guard_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FileAgentError("only http/https URLs are allowed")
    host = parsed.hostname or ""
    # Block obvious attempts to reach the machine's own network from a
    # model-controlled URL (SSRF guard) — resolve and check every address,
    # not just literal "localhost".
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        infos = []
    for info in infos:
        ip = info[4][0]
        if ip.startswith(("127.", "10.", "192.168.")) or ip in ("::1",) or ip.startswith("169.254."):
            raise FileAgentError("refusing to fetch a local/private network address")
        if ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31:
            raise FileAgentError("refusing to fetch a local/private network address")
    return url


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "tr"):
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        text = data.strip()
        if text:
            self.chunks.append(text + " ")


def fetch_url(url: str, workspace_root: Any, *, max_chars: int = _MAX_FETCH_CHARS) -> dict[str, Any]:
    """Fetch a web page and return its readable text (HTML tags stripped)."""
    url = _guard_url(url)
    try:
        response = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise FileAgentError(f"could not fetch the page: {exc}") from exc
    if response.status_code >= 400:
        raise FileAgentError(f"the page returned HTTP {response.status_code}")
    content_type = response.headers.get("Content-Type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        text = response.text[:max_chars]
        return {"url": url, "title": "", "text": text, "truncated": len(response.text) > max_chars}
    extractor = _TextExtractor()
    extractor.feed(response.text)
    text = re.sub(r"[ \t]+", " ", "".join(extractor.chunks))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    truncated = len(text) > max_chars
    return {
        "url": url,
        "title": extractor.title.strip(),
        "text": text[:max_chars],
        "truncated": truncated,
    }


def web_search(query: str, workspace_root: Any, *, max_results: int = 6) -> dict[str, Any]:
    """Keyless web search via DuckDuckGo's HTML endpoint. Returns titles,
    URLs, and short snippets — call fetch_url on a result for the full page."""
    query = query.strip()
    if not query:
        raise FileAgentError("query must not be empty")
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    response = None
    last_exc: Exception | None = None
    # DuckDuckGo intermittently resets connections (10054); retry with a
    # short backoff before giving up — one flaky reset must not kill a chat.
    for attempt in range(3):
        try:
            response = requests.post(
                search_url,
                data={"q": query},
                headers={"User-Agent": _UA},
                timeout=_TIMEOUT,
            )
            break
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(0.8 * (attempt + 1))
    if response is None:
        raise FileAgentError(f"web search failed: {last_exc}") from last_exc
    if response.status_code >= 400:
        raise FileAgentError(f"search endpoint returned HTTP {response.status_code}")
    html = response.text
    results: list[dict[str, str]] = []
    # DuckDuckGo's HTML-only endpoint renders each hit as:
    #   <a rel="nofollow" class="result__a" href="...">Title</a> ...
    #   <a class="result__snippet" ...>snippet text</a>
    link_pattern = re.compile(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL
    )
    snippet_pattern = re.compile(
        r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL
    )
    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)
    tag_re = re.compile(r"<[^>]+>")
    for i, (href, title_html) in enumerate(links[:max_results]):
        href = urljoin("https://duckduckgo.com/", href)
        title = tag_re.sub("", title_html).strip()
        snippet = tag_re.sub("", snippets[i]).strip() if i < len(snippets) else ""
        if title and href:
            results.append({"title": title, "url": href, "snippet": snippet})
    return {"query": query, "results": results}
