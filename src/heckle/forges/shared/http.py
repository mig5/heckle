"""Dependency-free, read-only HTTP with origin-pinned pagination and shared backoff."""

from __future__ import annotations

import copy
from collections import OrderedDict
from dataclasses import dataclass
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
from heckle.activity import format_clock, format_duration
from heckle.config import Connection
from heckle.errors import APIError, GenerationError

FORGE_NAMES = {"github": "GitHub", "gitlab": "GitLab", "gitea": "Gitea", "forgejo": "Forgejo"}


@dataclass(frozen=True)
class Quota:
    """A rate-limit window as the forge reports it."""

    remaining: int
    limit: int
    reset: float  # epoch seconds
    resource: str = "core"


def quota_from_headers(headers: dict[str, str], resource: str) -> Quota | None:
    def header(name: str) -> str | None:
        return headers.get(f"x-ratelimit-{name}") or headers.get(f"ratelimit-{name}")

    try:
        return Quota(
            int(header("remaining") or ""),
            int(header("limit") or ""),
            float(header("reset") or ""),
            headers.get("x-ratelimit-resource") or resource,
        )
    except ValueError:
        return None


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
            return max(
                1.0, float(headers.get("x-ratelimit-reset") or headers["ratelimit-reset"]) - now + 2
            )
        except (KeyError, ValueError):
            return 60.0
    return min(2**attempt, 60) + secrets.SystemRandom().random()


class PinnedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, check: Callable[[str], None]) -> None:
        self.check = check

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
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
        # Progress and rate-limit messages; the pipeline points this at its output.
        self.notify: Callable[[str], None] = lambda message: None
        self.requests = 0
        # Stop this many requests short of exhausting a window, leaving headroom for
        # requests already in flight from other workers.
        self.quota_reserve = 50
        self._quotas: dict[str, Quota] = {}
        self._announced: set[tuple[str, int]] = set()
        context = ssl.create_default_context(cafile=connection.ca_file)
        self._opener = urllib.request.build_opener(
            PinnedRedirect(self.check_url), urllib.request.HTTPSHandler(context=context)
        )

    def check_url(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)

        def endpoint(parts: urllib.parse.SplitResult) -> tuple[str, str | None, int | None]:
            scheme = parts.scheme.lower()
            default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
            return (
                scheme,
                parts.hostname.lower() if parts.hostname else None,
                parts.port or default_port,
            )

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
        if root_path and not (
            candidate_path == root_path or candidate_path.startswith(root_path + "/")
        ):
            # GitHub Enterprise GraphQL is outside /api/v3 and handled explicitly.
            if not (
                self.connection.source.forge == "github"
                and candidate_path == root_path.removesuffix("/v3") + "/graphql"
            ):
                raise GenerationError("Pagination/redirect escaped the configured API root")

    def headers(self) -> dict[str, str]:
        forge = self.connection.source.forge
        headers = {"Accept": "application/json", "User-Agent": f"heckle/{__version__}"}
        if forge == "gitlab":
            headers["PRIVATE-TOKEN"] = self.connection.token
        else:
            headers["Authorization"] = (
                "Bearer " if forge == "github" else "token "
            ) + self.connection.token
        if forge == "github":
            from heckle.forges.github.constants import API_VERSION

            headers.update(
                {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION}
            )
        return headers

    @property
    def forge_name(self) -> str:
        forge = self.connection.source.forge
        return FORGE_NAMES.get(forge, forge)

    @staticmethod
    def _resource(url: str) -> str:
        return "graphql" if url.endswith("/graphql") else "core"

    def _observe(self, headers: dict[str, str], url: str) -> None:
        quota = quota_from_headers(headers, self._resource(url))
        if quota is not None:
            with self._lock:
                self._quotas[quota.resource] = quota

    def known_quota(self, resource: str = "core") -> Quota | None:
        with self._lock:
            return self._quotas.get(resource)

    def _pace(self, url: str) -> None:
        """Pause before a request would exhaust the current rate-limit window."""
        resource = self._resource(url)
        with self._lock:
            quota = self._quotas.get(resource)
            if quota is None or quota.remaining > self.quota_reserve:
                return
            now = time.time()
            if quota.reset <= now:
                del self._quotas[resource]  # that window is over
                return
            resume = quota.reset + 2
            hold_until = time.monotonic() + (resume - now)
            # A rate-limit backoff may already be waiting this long, and said so.
            already_held = self._not_before >= hold_until - 5
            self._not_before = max(self._not_before, hold_until)
            # Expect a fresh window after the reset; the next response corrects this.
            self._quotas[resource] = Quota(quota.limit, quota.limit, quota.reset + 3600, resource)
            key = (resource, int(quota.reset))
            announce = key not in self._announced and not already_held
            self._announced.add(key)
        if announce:
            kind = "" if resource == "core" else f" {resource}"
            self.notify(
                f"{self.forge_name} API quota is nearly used up ({quota.remaining} of "
                f"{quota.limit}{kind} requests left). Pausing until {format_clock(resume)} "
                f"(about {format_duration(resume - now)}) rather than running out. "
                "Heckle is still working; please be patient."
            )

    def _wait(self) -> None:
        while True:
            with self._lock:
                delay = self._not_before - time.monotonic()
            if delay <= 0:
                return
            time.sleep(delay)

    def _backoff(self, headers: dict[str, str], attempt: int, *, rate: bool = False) -> None:
        delay = retry_delay(headers, attempt)
        with self._lock:
            resume = time.monotonic() + delay
            # Several workers can hit the same limit; announce a wait only once.
            announce = rate and delay >= 10 and resume > self._not_before + 5
            self._not_before = max(self._not_before, resume)
        if announce:
            self.notify(
                f"{self.forge_name} API rate limit reached; waiting until "
                f"{format_clock(time.time() + delay)} (about {format_duration(delay)}) before "
                "retrying. Heckle is still working; please be patient."
            )

    def paused_until(self) -> float | None:
        """Epoch time requests are held until, if they are being held."""
        with self._lock:
            delay = self._not_before - time.monotonic()
        return time.time() + delay if delay > 1 else None

    def describe(self) -> str:
        """One-line discovery status for a live display; never includes response data."""
        parts = [f"{self.requests:,} API requests"]
        quota = self.known_quota()
        if quota is not None:
            parts.append(
                f"quota {quota.remaining:,}/{quota.limit:,}, resets {format_clock(quota.reset)}"
            )
        paused = self.paused_until()
        if paused is not None:
            parts.append(f"paused for the rate limit until {format_clock(paused)}")
        return " · ".join(parts)

    def request(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        cache: bool = True,
        paced: bool = True,
    ) -> tuple[Any, dict[str, str]]:
        self.check_url(url)
        if method != "GET":
            query = (payload or {}).get("query", "").lstrip()
            if method != "POST" or not url.endswith("/graphql") or not query.startswith("query"):
                raise GenerationError("Heckle discovery only permits GET and GraphQL queries")
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        key = (method, url, data)
        with self._lock:
            if cache and key in self._cache:
                self._cache.move_to_end(key)
                return copy.deepcopy(self._cache[key])
        headers = self.headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(self.connection.retries + 1):
            if paced:
                self._pace(url)
                self._wait()
            with self._lock:
                self.requests += 1
            try:
                request = urllib.request.Request(url, data=data, headers=headers, method=method)
                with self._opener.open(request, timeout=self.connection.timeout) as response:
                    raw = response.read()
                    body = json.loads(raw) if raw else None
                    response_headers = {k.lower(): v for k, v in response.headers.items()}
                    self._observe(response_headers, url)
                    result = (body, response_headers)
                    if cache:
                        with self._lock:
                            self._cache[key] = copy.deepcopy(result)
                            while len(self._cache) > self.cache_limit:
                                self._cache.popitem(last=False)
                    return result
            except urllib.error.HTTPError as exc:
                response_headers = {k.lower(): v for k, v in exc.headers.items()}
                self._observe(response_headers, url)
                # Inspect only for rate-limit classification; never log the response body.
                raw = exc.read().decode("utf-8", errors="replace")
                rate = exc.code == 429 or (
                    exc.code == 403
                    and (
                        response_headers.get("x-ratelimit-remaining") == "0"
                        or "retry-after" in response_headers
                        or "secondary rate limit" in raw.lower()
                    )
                )
                if attempt < self.connection.retries and (rate or exc.code in {500, 502, 503, 504}):
                    if (
                        rate
                        and "retry-after" not in response_headers
                        and response_headers.get("x-ratelimit-remaining") != "0"
                    ):
                        response_headers["retry-after"] = "60"
                    self._backoff(response_headers, attempt, rate=rate)
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

    def get_paginated(
        self, path: str, *, params: dict[str, Any] | None = None, key: str | None = None
    ) -> list[Any]:
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
            url = (
                self.root + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(query, doseq=True)
            )
        raise GenerationError("API pagination exceeded its safety limit")
