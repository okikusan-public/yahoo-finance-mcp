"""OAuth 2.1 authorization server and resource-server authentication for a single pre-registered client.

Supported methods:
- Authorization code + PKCE (S256) + refresh tokens: Claude.ai / Claude Desktop custom connectors,
  Claude Code (--client-id), Notion custom agents, ChatGPT connectors
- client_credentials: scripts and other agents obtain a token directly
- Basic auth (Authorization: Basic base64(client_id:client_secret)): clients that only support static headers
- Long-lived API keys (bearer tokens issued with `python auth.py issue-token`): clients that only accept a bearer token

Tokens are HS256-signed JWTs, so the server keeps no state and runs unchanged across multiple Cloud Run instances.
Rotating the client secret invalidates every issued token, unless OAUTH_SIGNING_KEY is set separately.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import jwt
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 300
SCOPE = "mcp"
MCP_PATH = "/mcp"

# Callback URLs of Claude.ai / Claude Desktop, Notion and ChatGPT.
# Loopback redirects (localhost / 127.0.0.1, used by Claude Code) are always allowed.
DEFAULT_REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://app.notion.com/workflows/mcp/oauth/callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
)
PUBLIC_PATHS = (
    "/health",
    "/authorize",
    "/token",
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
    "/.well-known/oauth-protected-resource",
)


@dataclass(frozen=True)
class AuthConfig:
    client_id: str
    client_secret: str
    signing_key: bytes
    redirect_uris: tuple[str, ...]
    public_base_url: str | None

    @classmethod
    def from_env(cls) -> AuthConfig:
        client_id = os.environ.get("OAUTH_CLIENT_ID", "").strip()
        client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise SystemExit("Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET. The server refuses to start without authentication.")
        if len(client_secret) < 32:
            raise SystemExit("OAUTH_CLIENT_SECRET must be at least 32 characters (e.g. openssl rand -hex 32).")
        key = os.environ.get("OAUTH_SIGNING_KEY", "").strip()
        signing_key = key.encode() if key else hmac.new(client_secret.encode(), b"yahoo-finance-mcp/jwt", hashlib.sha256).digest()
        extra = tuple(u.strip() for u in os.environ.get("OAUTH_REDIRECT_URIS", "").split(",") if u.strip())
        base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/") or None
        return cls(client_id, client_secret, signing_key, DEFAULT_REDIRECT_URIS + extra, base)

    # --- helpers ------------------------------------------------------------

    def base_url(self, request: Request) -> str:
        if self.public_base_url:
            return self.public_base_url
        return f"{request.url.scheme}://{request.url.netloc}"

    def check_client(self, client_id: str | None, client_secret: str | None) -> bool:
        ok_id = hmac.compare_digest((client_id or "").encode(), self.client_id.encode())
        ok_secret = hmac.compare_digest((client_secret or "").encode(), self.client_secret.encode())
        return ok_id and ok_secret

    def redirect_allowed(self, uri: str) -> bool:
        if uri in self.redirect_uris:
            return True
        u = urlparse(uri)
        # RFC 8252: loopback redirects for native apps may use any port
        return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1") and not u.fragment

    def sign(self, claims: dict[str, Any], ttl: int) -> str:
        now = int(time.time())
        return jwt.encode({**claims, "iat": now, "exp": now + ttl, "jti": secrets.token_urlsafe(12)}, self.signing_key, "HS256")

    def verify(self, token: str, typ: str) -> dict[str, Any] | None:
        try:
            claims = jwt.decode(token, self.signing_key, algorithms=["HS256"], options={"verify_aud": False})
        except jwt.PyJWTError:
            return None
        if claims.get("typ") != typ or claims.get("sub") != self.client_id:
            return None
        return claims

    def issue_tokens(self, base: str, with_refresh: bool, scope: str = SCOPE) -> dict[str, Any]:
        access = self.sign({"typ": "access", "sub": self.client_id, "iss": base, "aud": base + MCP_PATH, "scope": scope}, ACCESS_TTL)
        body: dict[str, Any] = {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL, "scope": scope}
        if with_refresh:
            body["refresh_token"] = self.sign({"typ": "refresh", "sub": self.client_id, "iss": base, "scope": scope}, REFRESH_TTL)
        return body

    def authenticate_header(self, header: str) -> bool:
        """Validate the Authorization header of a request to a protected path."""
        scheme, _, value = header.partition(" ")
        scheme = scheme.lower()
        if scheme == "bearer" and value:
            return self.verify(value.strip(), "access") is not None
        if scheme == "basic" and value:
            try:
                cid, _, secret = base64.b64decode(value.strip(), validate=True).decode().partition(":")
            except (binascii.Error, UnicodeDecodeError):
                return False
            return self.check_client(cid, secret)
        return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _oauth_error(error: str, description: str, status: int = 400, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status,
                        headers={"Cache-Control": "no-store", **(headers or {})})


def protected_resource_metadata(cfg: AuthConfig, request: Request) -> JSONResponse:
    """RFC 9728"""
    base = cfg.base_url(request)
    return JSONResponse({
        "resource": base + MCP_PATH,
        "authorization_servers": [base],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Yahoo Finance MCP",
    })


def authorization_server_metadata(cfg: AuthConfig, request: Request) -> JSONResponse:
    """RFC 8414 (no dynamic client registration endpoint)"""
    base = cfg.base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": base + "/authorize",
        "token_endpoint": base + "/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "scopes_supported": [SCOPE],
        "authorization_response_iss_parameter_supported": True,
    })


def authorize(cfg: AuthConfig, request: Request) -> Response:
    """Authorization endpoint.

    There is exactly one pre-registered client, and exchanging the code requires both the client
    secret and the PKCE verifier, so the code is issued immediately without a consent screen.
    """
    q = request.query_params
    client_id, redirect_uri = q.get("client_id", ""), q.get("redirect_uri", "")
    # Show an error page instead of redirecting when client_id or redirect_uri is invalid (prevents open redirects)
    if not hmac.compare_digest(client_id.encode(), cfg.client_id.encode()):
        return HTMLResponse("<h1>invalid client_id</h1>", status_code=400)
    if not cfg.redirect_allowed(redirect_uri):
        return HTMLResponse("<h1>redirect_uri is not allowed</h1>", status_code=400)

    base = cfg.base_url(request)

    def back(**params: str | None) -> RedirectResponse:
        params = {k: v for k, v in {**params, "state": q.get("state"), "iss": base}.items() if v is not None}
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)

    if q.get("response_type") != "code":
        return back(error="unsupported_response_type", error_description="Only response_type=code is supported")
    if not q.get("code_challenge") or q.get("code_challenge_method") != "S256":
        return back(error="invalid_request", error_description="PKCE with code_challenge_method=S256 is required")
    code = cfg.sign(
        {"typ": "code", "sub": cfg.client_id, "redirect_uri": redirect_uri, "cc": q["code_challenge"],
         "scope": q.get("scope") or SCOPE},
        CODE_TTL,
    )
    return back(code=code)


async def token(cfg: AuthConfig, request: Request) -> Response:
    form = await request.form()
    client_id, client_secret = form.get("client_id"), form.get("client_secret")
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            client_id, _, client_secret = base64.b64decode(header[6:].strip(), validate=True).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return _oauth_error("invalid_client", "Malformed Authorization header", 401)
    if not cfg.check_client(str(client_id or ""), str(client_secret or "")):
        return _oauth_error("invalid_client", "Invalid client_id or client_secret", 401,
                            {"WWW-Authenticate": 'Basic realm="token"'})

    base = cfg.base_url(request)
    grant = form.get("grant_type")
    if grant == "client_credentials":
        return JSONResponse(cfg.issue_tokens(base, with_refresh=False), headers={"Cache-Control": "no-store"})
    if grant == "authorization_code":
        claims = cfg.verify(str(form.get("code", "")), "code")
        if claims is None:
            return _oauth_error("invalid_grant", "Authorization code is invalid or expired")
        if form.get("redirect_uri") != claims["redirect_uri"]:
            return _oauth_error("invalid_grant", "redirect_uri does not match the authorization request")
        verifier = str(form.get("code_verifier", ""))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not verifier or not hmac.compare_digest(challenge, claims["cc"]):
            return _oauth_error("invalid_grant", "code_verifier does not match (PKCE)")
        return JSONResponse(cfg.issue_tokens(base, with_refresh=True, scope=claims.get("scope", SCOPE)),
                            headers={"Cache-Control": "no-store"})
    if grant == "refresh_token":
        claims = cfg.verify(str(form.get("refresh_token", "")), "refresh")
        if claims is None:
            return _oauth_error("invalid_grant", "Refresh token is invalid or expired")
        return JSONResponse(cfg.issue_tokens(base, with_refresh=True, scope=claims.get("scope", SCOPE)),
                            headers={"Cache-Control": "no-store"})
    return _oauth_error("unsupported_grant_type", "Supported grant types: authorization_code, refresh_token, client_credentials")


def register_routes(mcp: Any, cfg_getter: Any) -> None:
    """Register the OAuth endpoints as MCPServer custom routes. The config is created at startup, so it is passed as a getter."""

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
    @mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
    async def _prm(request: Request) -> Response:
        return protected_resource_metadata(cfg_getter(), request)

    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
    @mcp.custom_route("/.well-known/openid-configuration", methods=["GET"])
    async def _asm(request: Request) -> Response:
        return authorization_server_metadata(cfg_getter(), request)

    @mcp.custom_route("/authorize", methods=["GET"])
    async def _authorize(request: Request) -> Response:
        return authorize(cfg_getter(), request)

    @mcp.custom_route("/token", methods=["POST"])
    async def _token(request: Request) -> Response:
        return await token(cfg_getter(), request)


class AuthMiddleware:
    """ASGI middleware that requires a Bearer (OAuth access token) or Basic (client_id:secret) header on every non-public path."""

    def __init__(self, app: ASGIApp, cfg: AuthConfig) -> None:
        self.app = app
        self.cfg = cfg

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(PUBLIC_PATHS):
            await self.app(scope, receive, send)
            return
        header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        if header and self.cfg.authenticate_header(header):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        metadata_url = self.cfg.base_url(request) + "/.well-known/oauth-protected-resource"
        challenge = f'Bearer resource_metadata="{metadata_url}", scope="{SCOPE}"'
        if header:
            challenge += ', error="invalid_token"'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", challenge.encode())],
        })
        await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})


# ---------------------------------------------------------------------------
# CLI: issue long-lived API keys (for clients that only accept a bearer token)
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Authentication utilities for Yahoo Finance MCP")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("issue-token", help="Issue a long-lived bearer token (API key)")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--base-url", required=True, help="Public URL of the server (e.g. https://xxx.a.run.app)")
    args = parser.parse_args()
    cfg = AuthConfig.from_env()
    base = args.base_url.rstrip("/")
    print(cfg.sign({"typ": "access", "sub": cfg.client_id, "iss": base, "aud": base + MCP_PATH, "scope": SCOPE},
                   args.days * 24 * 3600))


if __name__ == "__main__":
    main()
