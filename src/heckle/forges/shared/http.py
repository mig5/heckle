"""Dependency-free, read-only HTTP with origin-pinned pagination and shared backoff."""
from __future__ import annotations

import copy
from collections import OrderedDict
import email.utils
import json
import secrets
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from heckle import __version__
from heckle.config import Connection
from heckle.errors import APIError, GenerationError


def next_link(header: str | None) -> str | None:
    for item in (header or "").split(","):
        match = re.match(r'\s*<([^>]+)>\s*;.*?\brel\s*=\s*["\']?next\b', item)
        if match:
            return match.group(1)
    return None


def retry_delay(headers: dict[str, str], attempt: int, now: float | None = None) -> float:
    now = time.time() if now is None else now
    value = headers.get("retry-after")
    if value:
        try:
            return max(1.0, float(value))
        except ValueError:
            try:
                return max(1.0, email.utils.parsedate_to_datetime(value).timestamp() - now)
            except (ValueError, TypeError, OverflowError):
                pass
    if headers.get("x-ratelimit-remaining") == "0" or headers.get("ratelimit-remaining") == "0":
        try:
            return max(1.0, float(headers.get("x-ratelimit-reset") or headers["ratelimit-reset"]) - now + 2)
        except (KeyError, ValueError):
            return 60.0
    return min(2 ** attempt, 60) + secrets.SystemRandom().random()


class PinnedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, check: Callable[[str], None]) -> None:
        self.check = check

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        self.check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RESTClient:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.root = connection.api_root.rstrip("/")
        self._origin = urllib.parse.urlsplit(self.root)
        # Bound transport caching independently of the user's inventory size.
        self._cache: OrderedDict[tuple[str, str, bytes | None], Any] = OrderedDict()
        self.cache_limit = 256
        self._lock = threading.RLock()
        self._not_before = 0.0
        context = ssl.create_default_context(cafile=connection.ca_file)
        self._opener = urllib.request.build_opener(
            PinnedRedirect(self.check_url), urllib.request.HTTPSHandler(context=context)
        )

    def check_url(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        def endpoint(parts: urllib.parse.SplitResult) -> tuple[str, str | None, int | None]:
            scheme = parts.scheme.lower()
            default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
            return scheme, parts.hostname.lower() if parts.hostname else None, parts.port or default_port

        try:
            same_endpoint = endpoint(parsed) == endpoint(self._origin)
        except ValueError:
            same_endpoint = False
        if not same_endpoint or parsed.username or parsed.password or parsed.fragment:
            raise GenerationError("Refusing to send API credentials to a different origin")
        def canonical_path(value: str) -> str:
            decoded = value
            for _ in range(3):
                expanded = urllib.parse.unquote(decoded)
                if expanded == decoded:
                    break
                decoded = expanded
            if "\\" in decoded or "\x00" in decoded:
                raise GenerationError("Invalid API URL path")
            segments: list[str] = []
            for segment in decoded.split("/"):
                if segment in {"", "."}:
                    continue
                if segment == "..":
                    if segments:
                        segments.pop()
                    continue
                segments.append(segment)
            return "/" + "/".join(segments)

        root_path = canonical_path(self._origin.path).rstrip("/")
        candidate_path = canonical_path(parsed.path)
        if root_path and not (candidate_path == root_path or candidate_path.startswith(root_path + "/")):
            # GitHub Enterprise GraphQL is outside /api/v3 and handled explicitly.
            if not (self.connection.source.forge == "github" and candidate_path == root_path.removesuffix("/v3") + "/graphql"):
                raise GenerationError("Pagination/redirect escaped the configured API root")

    def headers(self) -> dict[str, str]:
        forge = self.connection.source.forge
        headers = {"Accept": "application/json", "User-Agent": f"heckle/{__version__}"}
        if forge == "gitlab":
            headers["PRIVATE-TOKEN"] = self.connection.token
        else:
            headers["Authorization"] = ("Bearer " if forge == "github" else "token ") + self.connection.token
        if forge == "github":
            from heckle.forges.github.constants import API_VERSION
            headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION})
        return headers

    def _wait(self) -> None:
        while True:
            with self._lock:
                delay = self._not_before - time.monotonic()
            if delay <= 0:
                return
            time.sleep(delay)

    def _backoff(self, headers: dict[str, str], attempt: int) -> None:
        delay = retry_delay(headers, attempt)
        with self._lock:
            self._not_before = max(self._not_before, time.monotonic() + delay)

    def request(self, method: str, url: str, *, payload: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        self.check_url(url)
        if method != "GET":
            query = (payload or {}).get("query", "").lstrip()
            if method != "POST" or not url.endswith("/graphql") or not query.startswith("query"):
                raise GenerationError("Heckle discovery only permits GET and GraphQL queries")
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        key = (method, url, data)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return copy.deepcopy(self._cache[key])
        headers = self.headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(self.connection.retries + 1):
            self._wait()
            try:
                request = urllib.request.Request(url, data=data, headers=headers, method=method)
                with self._opener.open(request, timeout=self.connection.timeout) as response:
                    raw = response.read()
                    body = json.loads(raw) if raw else None
                    response_headers = {k.lower(): v for k, v in response.headers.items()}
                    result = (body, response_headers)
                    with self._lock:
                        self._cache[key] = copy.deepcopy(result)
                        while len(self._cache) > self.cache_limit:
                            self._cache.popitem(last=False)
                    return result
            except urllib.error.HTTPError as exc:
                response_headers = {k.lower(): v for k, v in exc.headers.items()}
                # Inspect only for rate-limit classification; never log the response body.
                raw = exc.read().decode("utf-8", errors="replace")
                rate = exc.code == 429 or (exc.code == 403 and (
                    response_headers.get("x-ratelimit-remaining") == "0"
                    or "retry-after" in response_headers
                    or "secondary rate limit" in raw.lower()
                ))
                if attempt < self.connection.retries and (rate or exc.code in {500, 502, 503, 504}):
                    if rate and "retry-after" not in response_headers and response_headers.get("x-ratelimit-remaining") != "0":
                        response_headers["retry-after"] = "60"
                    self._backoff(response_headers, attempt)
                    continue
                raise APIError(exc.code, url) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < self.connection.retries:
                    self._backoff({}, attempt)
                    continue
                raise APIError(0, url, "Network or TLS failure") from exc
            except (ValueError, UnicodeError) as exc:
                raise APIError(0, url, "Invalid JSON response") from exc
        raise GenerationError("HTTP retry loop exhausted")

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        url = self.root + "/" + path.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        return self.request("GET", url)

    def get_paginated(self, path: str, *, params: dict[str, Any] | None = None, key: str | None = None) -> list[Any]:
        size_name = "limit" if self.connection.source.forge in {"gitea", "forgejo"} else "per_page"
        query = {size_name: 50 if size_name == "limit" else 100, "page": 1, **(params or {})}
        url = self.root + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(query, doseq=True)
        seen: set[str] = set()
        output: list[Any] = []
        for _ in range(10000):
            if url in seen:
                raise GenerationError("API pagination loop detected")
            seen.add(url)
            self.check_url(url)
            page_numbers = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("page")
            if page_numbers and page_numbers[-1].isdigit():
                query["page"] = int(page_numbers[-1])
            body, headers = self.request("GET", url)
            page = body.get(key) if key and isinstance(body, dict) else body
            if not isinstance(page, list):
                raise APIError(0, url, "Expected an API collection")
            output.extend(page)
            linked = next_link(headers.get("link"))
            if linked:
                url = urllib.parse.urljoin(url, linked)
                continue
            if "x-next-page" in headers:
                following = headers["x-next-page"]
                if not following:
                    return output
            elif "link" in headers or len(page) < int(query[size_name]):
                return output
            else:
                following = str(int(query["page"]) + 1)
            if not str(following).isdigit() or int(following) <= int(query["page"]):
                raise GenerationError("Invalid next-page header")
            query["page"] = int(following)
            url = self.root + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(query, doseq=True)
        raise GenerationError("API pagination exceeded its safety limit")
