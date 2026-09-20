import io
import json
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from heckle.errors import APIError, GenerationError
from heckle.forges.shared.http import RESTClient, next_link, retry_delay
from fixtures import connection


class Response:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.body).encode()


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
    def open(self, request, **kwargs):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException): raise response
        return response


@pytest.mark.parametrize("forge,header,prefix", [
    ("github", "Authorization", "Bearer "), ("gitlab", "Private-token", ""),
    ("gitea", "Authorization", "token "), ("forgejo", "Authorization", "token "),
])
def test_auth_and_cache(forge, header, prefix):
    client = RESTClient(connection(forge))
    client._opener = Opener([Response({"name": "example"})])
    first, _ = client.get("/test")
    first["name"] = "changed"
    second, _ = client.get("/test")
    assert second["name"] == "example"
    assert len(client._opener.requests) == 1
    assert client._opener.requests[0].get_header(header) == prefix + "test-token"


def test_gitlab_next_page():
    client = RESTClient(connection("gitlab"))
    client._opener = Opener([Response([{"id": 1}], {"X-Next-Page": "2"}), Response([{"id": 2}], {"X-Next-Page": ""})])
    assert [o["id"] for o in client.get_paginated("/groups")] == [1, 2]
    assert parse_qs(urlsplit(client._opener.requests[1].full_url).query)["page"] == ["2"]


def test_pagination_never_follows_external_origin():
    client = RESTClient(connection("github"))
    client._opener = Opener([Response([1], {"Link": '<https://attacker.test/steal>; rel="next"'})])
    with pytest.raises(GenerationError, match="different origin"):
        client.get_paginated("/repos")
    assert len(client._opener.requests) == 1


@pytest.mark.parametrize("suffix", [
    "/../admin", "/%2e%2e/admin", "/%252e%252e/admin", "/items#fragment",
    "/items\\..\\admin",
])
def test_api_url_cannot_escape_root_by_encoding_or_ambiguous_path(suffix):
    client = RESTClient(connection("gitea"))
    with pytest.raises(GenerationError):
        client.request("GET", client.root + suffix)


def test_equivalent_default_https_port_is_same_origin():
    client = RESTClient(connection("gitea"))
    client.check_url(client.root.replace(".test", ".test:443") + "/items")


def test_pagination_loop_is_rejected():
    client = RESTClient(connection("gitea"))
    url = client.root + "/items?limit=50&page=1"
    client._opener = Opener([Response([1], {"Link": f'<{url}>; rel="next"'})])
    with pytest.raises(GenerationError, match="loop"):
        client.get_paginated("/items")


@pytest.mark.parametrize("body", [[{"name": "X"}], {"variables": [{"name": "X"}]}])
def test_variable_envelopes(body):
    client = RESTClient(connection("forgejo"))
    client._opener = Opener([Response(body)])
    assert client.get_paginated("/items", key="variables") == [{"name": "X"}]


@pytest.mark.parametrize("method,payload", [("DELETE", None), ("POST", {"query": "mutation { x }"}), ("PATCH", {})])
def test_no_mutating_discovery(method, payload):
    client = RESTClient(connection("github"))
    client._opener = Opener([])
    with pytest.raises(GenerationError, match="only permits"):
        client.request(method, client.root + "/graphql", payload=payload)
    assert not client._opener.requests


def test_primary_rate_limit_is_not_capped():
    assert retry_delay({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "4600"}, 0, now=1000) == 3602
    assert retry_delay({"retry-after": "180"}, 0, now=1000) == 180


def test_errors_never_echo_tokens_or_response_body():
    client = RESTClient(connection("gitlab"))
    client._opener = Opener([urllib.error.HTTPError(client.root + "/test?private=secret", 403, "denied", {}, io.BytesIO(b'{"message":"SECRET-VALUE"}'))])
    with pytest.raises(APIError) as error:
        client.get("/test", params={"private": "secret"})
    assert "SECRET-VALUE" not in str(error.value)
    assert "?" not in str(error.value)


def test_transport_cache_is_bounded():
    client = RESTClient(connection('gitlab'))
    client.cache_limit = 2
    client._opener = Opener([Response({'id': index}) for index in range(4)])
    for index in range(4):
        client.get(f'/items/{index}')
    assert len(client._cache) == 2
