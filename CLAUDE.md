# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A remote MCP server (Streamable HTTP, stateless) that exposes Yahoo Finance data through yfinance, protected by a single-client OAuth 2.1 server. Deployed to Google Cloud Run. See `README.md` for the tool list, client setup, and deploy details.

## Repository conventions

- Everything in the repository is written in English: code, comments, tool descriptions, error messages, docs.
- Never commit credentials or personal data (secrets, tokens, email addresses, GCP project IDs or numbers, real service URLs). Real values live only in the local `.env`, which is gitignored along with `.dockerignore`/`.gcloudignore` exclusions.
- Yahoo Finance (via yfinance) is the only data source. If Yahoo does not provide something (e.g. Japanese government bond yields), report it as unavailable instead of adding another source.

## Commands

Python 3.12+ with [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env                   # then set OAUTH_CLIENT_SECRET (openssl rand -hex 32)
set -a; . ./.env; set +a
uv run python server.py                # MCP endpoint: http://localhost:8080/mcp, health: /health

uv run pytest                          # offline suite (stubbed yfinance); the `live` marker is deselected by default
uv run pytest tests/test_tools.py::test_name   # single test
uv run pytest -m live                  # smoke tests against the real Yahoo Finance API

python auth.py issue-token --days 90 --base-url <URL>   # long-lived bearer API key
./deploy.sh                            # Cloud Run deploy (env overrides: PROJECT, REGION, SERVICE, CLIENT_ID, SECRET, SA_NAME)
```

There is no linter or formatter configured.

## Architecture

Two runtime files, both copied into the Docker image (`Dockerfile` copies only `server.py` and `auth.py`):

- `server.py` defines the `MCPServer`, all tools, and `build_app()`. `main()` runs it with uvicorn.
- `auth.py` is a self-contained OAuth 2.1 authorization server plus `AuthMiddleware`. `server.py` registers its routes via `register_routes(mcp, _get_auth_config)`, and `build_app()` wraps the Streamable HTTP app in the middleware.

Things that span both files or are easy to get wrong:

- **Tools must use the local `@tool` decorator in `server.py`, not `mcp.tool()`.** It converts every exception into `ToolError(str(e))`; the MCP SDK otherwise hides the message of any non-`ToolError` exception from the client.
- **Stateless by design.** The app is built with `stateless_http=True, json_response=True`, and tokens are HS256 JWTs, so no server-side state or session affinity exists. Keep it that way for scale-to-zero on Cloud Run.
- **Single pre-registered client.** `OAUTH_CLIENT_ID`/`OAUTH_CLIENT_SECRET` are the only credentials; dynamic client registration is off. The signing key is derived from the client secret unless `OAUTH_SIGNING_KEY` is set, so rotating the secret invalidates every token and API key.
- **Auth methods all accepted by the middleware:** OAuth code + PKCE, Basic auth, `client_credentials` at `POST /token`, and long-lived bearer tokens.
- **Cloud Run reserves paths ending in `z`**, which is why the health check is `/health`, not `/healthz`.
- **Response shaping lives in the helpers** (`_clean`, `_records`, `_compact_ohlcv`, `_statement`, `_safe`): NaN/Timestamp conversion, row caps with a `truncated` flag, dropping all-zero columns, rounding. New tools should reuse them to keep payloads small.
- **Known yfinance workarounds:** change/change % come from chart metadata `previousClose` (not `fast_info.previousClose`), and `get_market_summary` calls the Yahoo endpoint directly because `yfinance.Market.summary` overwrites items that share an exchange code.

Tests (`tests/`): `conftest.py` provides the stubbed-yfinance fixtures and a `live_server` fixture; `test_http_auth.py` includes an end-to-end OAuth flow using the MCP SDK's own OAuth client. Offline tests are the default; anything hitting real Yahoo must be marked `@pytest.mark.live`.

## Stock backtesting workflow (not part of the server)

`skills/stock-*` hold six Claude/Codex skill definitions for data collection, incremental updates, backtesting, rule-set versioning, result review, and rule-search loops. They are committed; their outputs are not.

- Generated data, runs, and results go under `.cache/backtests/<package-id>/` (gitignored). One-off runner scripts live in `scripts/` (gitignored). Do not add request-specific scripts to tracked directories or the product code.
- Do not edit the `skills/` definitions unless the task is explicitly about changing a skill.
- Backtests are signal-driven (decide at close, fill at the next open), start from cash, and must pass the accounting/validation checks described in `skills/stock-backtester/references/validation.md` before numbers are reported.
- Data quality labels matter: monthly universes rebuilt from currently listed symbols are "approximate PIT" with survivorship bias, not point-in-time.
