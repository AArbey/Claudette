"""Bounded HTTP search and readable page loading for Brain tools."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit


class WebToolError(Exception):
    pass


class _NetworkHandle:
    def __init__(self, sock: socket.socket):
        self.sock = sock

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class _Links(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self.href = ""
        self.label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href") or ""
            self.label = []

    def handle_data(self, data: str) -> None:
        if self.href:
            self.label.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self.href:
            return
        url = urljoin(self.base_url, self.href)
        label = " ".join("".join(self.label).split())
        if urlsplit(url).scheme in {"http", "https"} and label and len(self.links) < 60:
            self.links.append({"text": label[:160], "url": url[:2048]})
        self.href = ""
        self.label = []


def _clean_url(url: str, *, allow_loopback: bool = False) -> tuple[str, str, int, str]:
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise WebToolError("Invalid URL")
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
        raise WebToolError("Only HTTP(S) URLs without credentials are allowed")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as error:
        raise WebToolError("Invalid URL port") from error
    if parts.hostname.lower().rstrip(".") == "localhost" and not allow_loopback:
        raise WebToolError("localhost is blocked")
    clean = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
    return clean, parts.hostname, port, parts.scheme


def _addresses(host: str, port: int, *, allow_loopback: bool, brain_url: str, brain_ports: set[int]) -> list[tuple]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise WebToolError(f"Cannot resolve host: {error}") from error
    brain_host = urlsplit(brain_url).hostname
    brain_ips = set()
    for own_host in (brain_host, socket.gethostname()):
        if not own_host:
            continue
        try:
            brain_ips.update(item[4][0] for item in socket.getaddrinfo(
                own_host, None, type=socket.SOCK_STREAM))
        except OSError:
            continue
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if (ip.is_loopback and not allow_loopback) or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise WebToolError("Address is blocked")
        if port in brain_ports and (str(ip) in brain_ips or ip.is_loopback):
            raise WebToolError("Brain service address is blocked")
        if str(ip) in {"100.100.100.200", "fd00:ec2::254"}:
            raise WebToolError("Metadata address is blocked")
    return infos


def fetch_http(
    url: str, *, brain_url: str, brain_ports: set[int], cancellation=None,
    max_bytes: int = 2 * 1024 * 1024, allow_loopback: bool = False,
) -> tuple[str, str, bytes]:
    """Fetch with pinned DNS result; validate every redirect and cap response size."""
    for _ in range(6):
        clean, host, port, scheme = _clean_url(url, allow_loopback=allow_loopback)
        infos = _addresses(host, port, allow_loopback=allow_loopback,
                           brain_url=brain_url, brain_ports=brain_ports)
        family, socktype, proto, _, address = infos[0]
        conn = http.client.HTTPConnection(host, port, timeout=10)
        raw = socket.socket(family, socktype, proto)
        raw.settimeout(10)
        conn.sock = raw
        handle = _NetworkHandle(raw)
        response = None
        if cancellation is not None:
            cancellation.raise_if_cancelled()
            cancellation.bind(handle)
        try:
            raw.connect(address)
            if scheme == "https":
                conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
                handle.sock = conn.sock
            parts = urlsplit(clean)
            target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
            conn.request("GET", target, headers={
                "Host": parts.netloc, "User-Agent": "AIHelperBrain/1",
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
                "Connection": "close", "Accept-Encoding": "identity",
            })
            # HTTPConnection may close its socket after headers while HTTPResponse
            # still reads from a file wrapper. Keep a duplicate to interrupt it.
            transport = conn.sock
            handle.sock = socket.socket(
                transport.family, transport.type, transport.proto,
                fileno=os.dup(transport.fileno()),
            )
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise WebToolError("Redirect has no Location")
                url = urljoin(clean, location)
                continue
            if response.status == 403:
                raise WebToolError("HTTP 403")
            if response.status >= 400:
                raise WebToolError(f"HTTP {response.status}")
            chunks = []
            total = 0
            deadline = time.monotonic() + 20
            while True:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WebToolError("Web request exceeded 20 second limit")
                chunk = response.read1(min(65536, max_bytes + 1 - total))
                if not chunk:
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise WebToolError("Page exceeds 2 MiB download limit")
                chunks.append(chunk)
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if response.length not in (None, 0):
                raise WebToolError("Incomplete HTTP response")
            return clean, response.getheader("Content-Type", ""), b"".join(chunks)
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            if cancellation is not None and cancellation.cancelled:
                cancellation.raise_if_cancelled()
            raise WebToolError(f"Cannot fetch page: {error}") from error
        finally:
            if cancellation is not None:
                cancellation.unbind(handle)
            if response is not None:
                response.close()
            handle.close()
            conn.close()
    raise WebToolError("Too many redirects")


def search_searxng(query: str, limit: int, base_url: str, *, brain_url: str, brain_ports: set[int], cancellation=None) -> dict:
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise WebToolError("Search query must contain 1–500 characters")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise WebToolError("Search limit must be between 1 and 20")
    if not base_url:
        raise WebToolError("Configure SearXNG in Web settings first")
    results = []
    seen = set()
    for page in range(1, 4):
        search_url = base_url.rstrip("/") + "/search?" + urlencode(
            {"q": query.strip(), "format": "json", "pageno": page})
        try:
            _, content_type, body = fetch_http(
                search_url, brain_url=brain_url, brain_ports=brain_ports,
                cancellation=cancellation, max_bytes=2 * 1024 * 1024,
                allow_loopback=True,
            )
        except WebToolError as error:
            if "HTTP 403" in str(error):
                raise WebToolError("SearXNG denied JSON (HTTP 403); enable json in search.formats") from error
            raise
        if "json" not in content_type.lower():
            raise WebToolError("SearXNG did not return JSON; enable json in search.formats")
        try:
            payload = json.loads(body)
        except ValueError as error:
            raise WebToolError("SearXNG returned invalid JSON") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise WebToolError("SearXNG returned invalid results")
        if not payload["results"]:
            break
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            try:
                clean_url, _, _, _ = _clean_url(url)
            except (WebToolError, ValueError):
                continue
            if clean_url in seen:
                continue
            seen.add(clean_url)
            results.append({
                "title": str(item.get("title") or "")[:300],
                "url": clean_url,
                "snippet": str(item.get("content") or "")[:1200],
            })
            if len(results) == limit:
                break
        if len(results) == limit:
            break
    return {"query": query.strip(), "results": results, "count": len(results)}


def load_web_page(url: str, *, brain_url: str, brain_ports: set[int], cancellation=None) -> dict:
    final_url, content_type, body = fetch_http(
        url, brain_url=brain_url, brain_ports=brain_ports, cancellation=cancellation,
    )
    if "html" not in content_type.lower():
        raise WebToolError("Page is not HTML")
    try:
        import trafilatura
    except ImportError as error:
        raise WebToolError("Web page extractor unavailable") from error
    extracted = trafilatura.extract(
        body, output_format="json", with_metadata=True,
        include_links=True, include_comments=False,
    )
    if not extracted:
        raise WebToolError("No readable text found; page may require JavaScript")
    try:
        document = json.loads(extracted)
    except ValueError as error:
        raise WebToolError("Cannot decode extracted page") from error
    content = document.get("text") or ""
    if not content.strip():
        raise WebToolError("No readable text found; page may require JavaScript")
    truncated = len(content) > 100_000
    parser = _Links(final_url)
    parser.feed(body.decode("utf-8", errors="replace"))
    return {
        "url": final_url,
        "title": document.get("title") or "",
        "text": content[:100_000],
        "links": parser.links,
        "truncated": truncated,
    }
