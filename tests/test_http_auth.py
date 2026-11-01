"""Start a real HTTP server and test OAuth / Basic auth and the Streamable HTTP transport."""

import base64
import hashlib
import secrets
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

import httpx2
import jwt
import pytest
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata

import server
from conftest import CLIENT_ID, CLIENT_SECRET

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
N_TOOLS = 24
BASIC = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
REDIRECT = "http://localhost:33418/callback"


def list_tools(base, auth=None, **extra_headers):
    headers = {**MCP_HEADERS, **extra_headers}
    if auth:
        headers["Authorization"] = auth
    return httpx2.post(f"{base}/mcp", headers=headers, json=LIST_TOOLS)


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize(base, **overrides):
    params = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
              "code_challenge_method": "S256", "state": "xyz", "scope": "mcp", **overrides}
    return httpx2.get(f"{base}/authorize", params={k: v for k, v in params.items() if v is not None})


def code_from(resp):
    assert resp.status_code == 302, resp.text
    q = parse_qs(urlparse(resp.headers["location"]).query)
    return {k: v[0] for k, v in q.items()}


# --- startup and public endpoints ----------------------------------------------


def test_build_app_requires_client_credentials(monkeypatch):
    monkeypatch.delenv("OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("OAUTH_CLIENT_SECRET", raising=False)
    with pytest.raises(SystemExit):
        server.build_app()


def test_build_app_rejects_short_secret(monkeypatch):
    monkeypatch.setenv("OAUTH_CLIENT_ID", "x")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "short")
    with pytest.raises(SystemExit):
        server.build_app()


def test_health_and_metadata_are_public(live_server):
    assert httpx2.get(f"{live_server}/health").text == "ok"
    prm = httpx2.get(f"{live_server}/.well-known/oauth-protected-resource").json()
    assert prm["resource"] == f"{live_server}/mcp" and prm["authorization_servers"] == [live_server]
    assert httpx2.get(f"{live_server}/.well-known/oauth-protected-resource/mcp").json() == prm
    asm = httpx2.get(f"{live_server}/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == live_server
    assert asm["token_endpoint"] == f"{live_server}/token"
    assert set(asm["grant_types_supported"]) == {"authorization_code", "refresh_token", "client_credentials"}
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert "registration_endpoint" not in asm  # dynamic client registration is disabled


def test_public_base_url_overrides_host(monkeypatch, live_server):
    import dataclasses
    monkeypatch.setattr(server, "_auth_config", dataclasses.replace(server._auth_config, public_base_url="https://yf.example.com"))
    asm = httpx2.get(f"{live_server}/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == "https://yf.example.com"


# --- 401 and challenge --------------------------------------------------------


@pytest.mark.parametrize("auth", [None, "Bearer wrong", "Bearer " + "x" * 200, "Basic !!!",
                                  "Basic " + base64.b64encode(f"{CLIENT_ID}:wrong".encode()).decode(),
                                  f"Bearer {CLIENT_SECRET}"])
def test_rejects_bad_auth(live_server, auth):
    r = list_tools(live_server, auth)
    assert r.status_code == 401
    challenge = r.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{live_server}/.well-known/oauth-protected-resource"' in challenge
    assert ('error="invalid_token"' in challenge) == (auth is not None)


def test_non_mcp_paths_also_require_auth(live_server):
    assert httpx2.get(f"{live_server}/anything").status_code == 401


# --- Basic (header-based clients) ---------------------------------------------


def test_basic_auth_with_client_credentials(live_server):
    r = list_tools(live_server, BASIC)
    assert r.status_code == 200 and len(r.json()["result"]["tools"]) == N_TOOLS


def test_cloud_run_host_header_is_allowed(live_server):
    r = list_tools(live_server, BASIC, Host="yahoo-finance-mcp-xyz.a.run.app")
    assert r.status_code == 200


# --- client_credentials ----------------------------------------------------------


@pytest.mark.parametrize("style", ["basic", "post"])
def test_client_credentials_grant(live_server, style):
    if style == "basic":
        r = httpx2.post(f"{live_server}/token", data={"grant_type": "client_credentials"}, headers={"Authorization": BASIC})
    else:
        r = httpx2.post(f"{live_server}/token", data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600 and "refresh_token" not in body
    assert list_tools(live_server, f"Bearer {body['access_token']}").status_code == 200


def test_token_endpoint_rejects_wrong_secret(live_server):
    r = httpx2.post(f"{live_server}/token", data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": "nope"})
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"


def test_unsupported_grant(live_server):
    r = httpx2.post(f"{live_server}/token", data={"grant_type": "password"}, headers={"Authorization": BASIC})
    assert r.json()["error"] == "unsupported_grant_type"


def test_expired_and_forged_tokens_are_rejected(live_server):
    key = server._auth_config.signing_key
    now = int(time.time())
    expired = jwt.encode({"typ": "access", "sub": CLIENT_ID, "iat": now - 7200, "exp": now - 3600}, key, "HS256")
    forged = jwt.encode({"typ": "access", "sub": CLIENT_ID, "exp": now + 3600}, b"k" * 32, "HS256")
    refresh_as_access = jwt.encode({"typ": "refresh", "sub": CLIENT_ID, "exp": now + 3600}, key, "HS256")
    other_client = jwt.encode({"typ": "access", "sub": "someone", "exp": now + 3600}, key, "HS256")
    none_alg = jwt.encode({"typ": "access", "sub": CLIENT_ID, "exp": now + 3600}, None, "none")
    for t in (expired, forged, refresh_as_access, other_client, none_alg):
        assert list_tools(live_server, f"Bearer {t}").status_code == 401


# --- authorization code + PKCE ------------------------------------------------


def test_authorization_code_flow_with_refresh(live_server):
    verifier, challenge = pkce()
    q = code_from(authorize(live_server, code_challenge=challenge))
    assert q["state"] == "xyz" and q["iss"] == live_server
    r = httpx2.post(f"{live_server}/token", data={"grant_type": "authorization_code", "code": q["code"],
                                                   "redirect_uri": REDIRECT, "code_verifier": verifier,
                                                   "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert list_tools(live_server, f"Bearer {tokens['access_token']}").status_code == 200
    r = httpx2.post(f"{live_server}/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
                    headers={"Authorization": BASIC})
    assert r.status_code == 200
    assert list_tools(live_server, f"Bearer {r.json()['access_token']}").status_code == 200


@pytest.mark.parametrize("redirect", ["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback",
                                      "https://app.notion.com/workflows/mcp/oauth/callback",
                                      "https://chatgpt.com/connector_platform_oauth_redirect", "http://127.0.0.1:5555/cb"])
def test_allowed_redirects(live_server, redirect):
    _, challenge = pkce()
    assert "code" in code_from(authorize(live_server, redirect_uri=redirect, code_challenge=challenge))


@pytest.mark.parametrize("overrides,status", [
    ({"client_id": "evil"}, 400),
    ({"redirect_uri": "https://evil.example.com/cb"}, 400),
    ({"redirect_uri": "http://evil.example.com/cb"}, 400),
])
def test_authorize_rejects_without_redirecting(live_server, overrides, status):
    _, challenge = pkce()
    r = authorize(live_server, code_challenge=challenge, **overrides)
    assert r.status_code == status and "location" not in r.headers


@pytest.mark.parametrize("overrides,error", [
    ({"code_challenge": None}, "invalid_request"),
    ({"code_challenge_method": "plain", "code_challenge": "abc"}, "invalid_request"),
    ({"response_type": "token", "code_challenge": "abc"}, "unsupported_response_type"),
])
def test_authorize_errors_redirect_back(live_server, overrides, error):
    q = code_from(authorize(live_server, **overrides))
    assert q["error"] == error and q["state"] == "xyz"


def test_code_exchange_checks_verifier_redirect_and_secret(live_server):
    verifier, challenge = pkce()
    code = code_from(authorize(live_server, code_challenge=challenge))["code"]
    base = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": verifier}
    cases = [
        ({**base, "code_verifier": "wrong"}, BASIC, "invalid_grant"),
        ({**base, "code_verifier": ""}, BASIC, "invalid_grant"),
        ({**base, "redirect_uri": "http://localhost:1/other"}, BASIC, "invalid_grant"),
        ({**base, "code": "garbage"}, BASIC, "invalid_grant"),
        (base, "Basic " + base64.b64encode(f"{CLIENT_ID}:bad".encode()).decode(), "invalid_client"),
    ]
    for data, auth, error in cases:
        assert httpx2.post(f"{live_server}/token", data=data, headers={"Authorization": auth}).json()["error"] == error


def test_access_token_cannot_be_used_as_code_or_refresh(live_server):
    access = httpx2.post(f"{live_server}/token", data={"grant_type": "client_credentials"}, headers={"Authorization": BASIC}).json()["access_token"]
    for data in ({"grant_type": "refresh_token", "refresh_token": access},
                 {"grant_type": "authorization_code", "code": access, "redirect_uri": REDIRECT, "code_verifier": "x"}):
        assert httpx2.post(f"{live_server}/token", data=data, headers={"Authorization": BASIC}).json()["error"] == "invalid_grant"


# --- end-to-end with the standard MCP client -----------------------------------


class MemoryStorage:
    def __init__(self):
        self.tokens = None
        self.client = OAuthClientInformationFull(
            client_id=CLIENT_ID, client_secret=CLIENT_SECRET, redirect_uris=[REDIRECT],
            token_endpoint_auth_method="client_secret_post", grant_types=["authorization_code", "refresh_token"],
        )

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, info):
        self.client = info


@pytest.mark.anyio
@pytest.mark.usefixtures("fake_yf")
async def test_mcp_sdk_oauth_client_full_flow(live_server):
    """The MCP SDK's OAuth client (discovery -> authorization -> token exchange) works as-is, following the same spec as Claude and Notion."""
    captured = {}

    async def redirect_handler(url: str) -> None:  # open the authorization URL instead of a browser
        captured["location"] = httpx2.get(url).headers["location"]

    async def callback_handler() -> AuthorizationCodeResult:
        q = {k: v[0] for k, v in parse_qs(urlparse(captured["location"]).query).items()}
        return AuthorizationCodeResult(code=q["code"], state=q.get("state"), iss=q.get("iss"))

    storage = MemoryStorage()
    provider = OAuthClientProvider(
        server_url=f"{live_server}/mcp",
        client_metadata=OAuthClientMetadata(redirect_uris=[REDIRECT], client_name="test"),
        storage=storage, redirect_handler=redirect_handler, callback_handler=callback_handler,
    )
    http = create_mcp_http_client(auth=provider)
    async with http, Client(streamable_http_client(f"{live_server}/mcp", http_client=http)) as c:
        assert len((await c.list_tools()).tools) == N_TOOLS
        r = await c.call_tool("get_quote", {"symbol": "SPCX"})
    assert not r.is_error and r.structured_content["price"] == 147.49
    assert storage.tokens.refresh_token


@pytest.mark.anyio
@pytest.mark.usefixtures("fake_yf")
async def test_mcp_client_with_basic_header(live_server):
    http = create_mcp_http_client(headers={"Authorization": BASIC})
    async with http, Client(streamable_http_client(f"{live_server}/mcp", http_client=http)) as c:
        assert len((await c.list_tools()).tools) == N_TOOLS


# --- long-lived API key CLI ----------------------------------------------------


def test_issue_token_cli(live_server, monkeypatch):
    env = {"OAUTH_CLIENT_ID": CLIENT_ID, "OAUTH_CLIENT_SECRET": CLIENT_SECRET, "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "auth.py", "issue-token", "--days", "1", "--base-url", live_server],
                         capture_output=True, text=True, env=env, check=True).stdout.strip()
    assert list_tools(live_server, f"Bearer {out}").status_code == 200
    claims = jwt.decode(out, options={"verify_signature": False})
    assert 86000 < claims["exp"] - claims["iat"] <= 86400
