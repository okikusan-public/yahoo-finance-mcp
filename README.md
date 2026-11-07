# stock_skills_3

A remote [MCP](https://modelcontextprotocol.io) server that exposes Yahoo Finance data (via [yfinance](https://github.com/ranaroussi/yfinance)) to MCP clients such as **Claude** (claude.ai, Claude Desktop, Claude Code), **ChatGPT** connectors, and **Notion** custom agents.

- **24 tools** covering quotes, price history, fundamentals, analyst data, holders, options, funds, screeners, sectors, market summaries, calendars, and live ticks
- **Streamable HTTP** transport, stateless (scales to zero on Cloud Run)
- **OAuth 2.1** with a single pre-registered client (`client_id` / `client_secret`), plus Basic auth and client credentials for non-OAuth clients
- **One-command deploy** to Google Cloud Run with the secret kept in Secret Manager

> [!NOTE]
> Yahoo Finance is the only data source. yfinance uses Yahoo's unofficial APIs, so endpoints can change or be rate-limited without notice. This project is not affiliated with Yahoo. Use it for personal purposes and respect Yahoo's terms of service.

## Tools

| Category | Tool | What it returns |
|---|---|---|
| Prices | `get_quote` | Price, change vs. previous close, day range, volume, 52-week range, market cap, moving averages, trading session times |
| | `get_quotes` | Price and change for up to 50 symbols at once |
| | `get_price_history` | OHLCV history (1m to 3mo bars, pre/post market, adjusted or raw) |
| | `get_multi_price_history` | One field (e.g. Close) for up to 20 symbols as a date × symbol table |
| | `get_live_ticks` | Subscribes to Yahoo's real-time WebSocket stream for a few seconds and returns the latest tick per symbol |
| Company | `get_company_info` | Profile, sector/industry, officers, valuation ratios, margins |
| | `get_financial_statements` | Income statement, balance sheet, cash flow (yearly, quarterly, TTM) |
| | `get_valuation_measures` | Market cap, EV, P/E, forward P/E, PEG, P/S, P/B, EV/Revenue, EV/EBITDA over time |
| | `get_earnings` | Next earnings date and estimates, EPS estimate / actual / surprise history |
| | `get_analyst_data` | Price targets, rating distribution, upgrades/downgrades, EPS and revenue estimates, EPS trend and revisions, growth estimates |
| | `get_holders` | Major holders breakdown, top institutions and mutual funds, insider transactions and roster |
| | `get_dividends_and_splits` | Dividend, split, and capital-gain distribution history (newest first) |
| | `get_shares_outstanding` | Shares outstanding over time (monthly changes) |
| | `get_sec_filings` | SEC filings (US companies) |
| | `get_fund_data` | ETF / mutual fund overview, expenses, asset allocation, top holdings, sector weights, bond ratings |
| | `get_news` | Related news articles |
| | `get_option_chain` | Calls and puts for an expiration, optionally trimmed to strikes around the spot price |
| Discovery | `search_symbols` | Find tickers by name (English), optionally filtered by asset type, with research headlines |
| | `screen_securities` | Yahoo's screener: 19 predefined screens or custom filters (equity / fund / ETF), with paging |
| | `list_screener_fields` | Valid screener fields and enum values (regions, sectors, exchanges) |
| | `get_sector` / `get_industry` | Sector / industry overview, top companies, top ETFs and funds, research reports |
| Market | `get_market_summary` | Regional summaries (US, Asia, Europe, UK, US Treasury yields, currencies, commodities, crypto) and market open/close status |
| | `get_calendar` | Earnings, economic events, IPOs, and stock splits calendars |

Ticker examples: `AAPL`, `7203.T` (Tokyo), `^N225`, `^GSPC`, `USDJPY=X`, `CL=F`, `BTC-USD`.

### Data quality notes

- **Previous close.** Change and change % are computed from Yahoo's chart metadata (`previousClose`), not from `fast_info.previousClose`, which is often off by a day.
- **Market summary.** `yfinance.Market.summary` keys results by exchange code, so items on the same exchange overwrite each other (e.g. only 1 of 6 currencies survives). This server calls the same Yahoo endpoint directly and keeps every item.
- **Response size.** Option chains are trimmed to strikes near the spot price. Price history drops all-zero dividend/split columns and rounds prices. Long lists are capped and report `truncated`.
- **Errors.** Every tool returns a readable message to the client, e.g. "symbol not found, use `search_symbols`" or the list of valid expirations. MCP SDK 2.x would otherwise hide the cause.

### Yahoo Finance features intentionally not exposed

| Feature | Reason |
|---|---|
| ESG scores (`sustainability`) | Yahoo currently returns empty data for every symbol tested |
| `earnings` / `quarterly_earnings` | Discontinued by Yahoo (returns `None`); covered by `get_earnings` and `get_financial_statements` |
| News tabs (`get_news(tab=...)`) | Every tab returns empty; `get_news` falls back to Yahoo's search API |
| ISIN (`isin`) | yfinance fetches it from Business Insider, not Yahoo, and returns wrong values for non-US listings |
| `shares` | Not implemented in yfinance |

Data that Yahoo Finance does not provide (for example Japanese government bond yields) is out of scope.

## Authentication

Only one pre-registered client is accepted. It is configured with `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET`. Dynamic client registration is disabled.

| Method | Use it for | How it works |
|---|---|---|
| OAuth authorization code + PKCE | Claude.ai / Claude Desktop custom connectors, Claude Code (`--client-id`), ChatGPT connectors, Notion custom agents | Enter the client ID and secret in the client. A browser window opens and is approved automatically. Access tokens last 1 hour; refresh tokens last 30 days |
| Basic auth | Clients that only support static headers or username/password | `Authorization: Basic base64(client_id:client_secret)` |
| Client credentials | Scripts, other agents, automation tools | `POST /token` with `grant_type=client_credentials` returns a 1-hour bearer token |
| Long-lived API key | Clients that only accept a bearer token | `python auth.py issue-token --days 90 --base-url <URL>` |

Design notes:

- **Stateless tokens.** Tokens are HS256-signed JWTs, so the server keeps no state and works across multiple instances.
- **Revocation.** Rotating `OAUTH_CLIENT_SECRET` invalidates every issued token and API key, unless you set `OAUTH_SIGNING_KEY` separately.
- **No consent screen.** The authorization endpoint issues a code without asking. That is safe here because exchanging the code requires both the client secret and the PKCE verifier.
- **Allowed redirect URIs.** Claude (`https://claude.ai/api/mcp/auth_callback`, `https://claude.com/api/mcp/auth_callback`), Notion (`https://app.notion.com/workflows/mcp/oauth/callback`), ChatGPT (`https://chatgpt.com/connector_platform_oauth_redirect`), and loopback addresses (`http://localhost:*`, `http://127.0.0.1:*`). Add more with `OAUTH_REDIRECT_URIS` (comma-separated).
- **Discovery.** Metadata is served at `/.well-known/oauth-protected-resource` (RFC 9728) and `/.well-known/oauth-authorization-server` (RFC 8414). A 401 response includes `WWW-Authenticate: Bearer resource_metadata=...`.

### Configuration

| Variable | Required | Description |
|---|---|---|
| `OAUTH_CLIENT_ID` | yes | Client ID of the single allowed client |
| `OAUTH_CLIENT_SECRET` | yes | Client secret, at least 32 characters (`openssl rand -hex 32`) |
| `PUBLIC_BASE_URL` | no | Public origin used as the OAuth issuer and resource. If unset, it is derived from the request |
| `OAUTH_REDIRECT_URIS` | no | Extra allowed redirect URIs, comma-separated |
| `OAUTH_SIGNING_KEY` | no | JWT signing key. If unset, it is derived from the client secret |
| `PORT` / `HOST` | no | Listen address (default `0.0.0.0:8080`) |

See [`.env.example`](.env.example). Keep real values in `.env`, which is excluded by `.gitignore`, `.dockerignore`, and `.gcloudignore`.

## Quick start (local)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
# set OAUTH_CLIENT_SECRET in .env, e.g. with: openssl rand -hex 32
set -a; . ./.env; set +a
uv run python server.py          # MCP endpoint: http://localhost:8080/mcp
```

Try it with curl:

```bash
TOKEN=$(curl -s -u "$OAUTH_CLIENT_ID:$OAUTH_CLIENT_SECRET" -d grant_type=client_credentials \
  http://localhost:8080/token | jq -r .access_token)

curl -s http://localhost:8080/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_quote","arguments":{"symbol":"AAPL"}}}'
```

## Tests

```bash
uv run pytest              # offline: tools with a stubbed yfinance, auth flows, HTTP transport
uv run pytest -m live      # smoke tests against the real Yahoo Finance API
```

The offline suite includes an end-to-end OAuth test using the MCP Python SDK's own OAuth client: discovery, authorization, token exchange, and tool calls.

## Deploy to Google Cloud Run

Requires the `gcloud` CLI, logged in to a project with billing enabled.

```bash
./deploy.sh
# Override defaults with environment variables:
#   PROJECT (gcloud's current project), REGION (asia-northeast1), SERVICE (yahoo-finance-mcp),
#   CLIENT_ID (yahoo-finance-mcp), SECRET (yahoo-finance-mcp-client-secret), SA_NAME (yahoo-finance-mcp)
```

The script does the following:

1. Enables the required APIs: Run, Cloud Build, Artifact Registry, Secret Manager, and IAM.
2. Creates a dedicated runtime service account that can read only this one secret.
3. Generates a client secret in Secret Manager on the first run. The value is never printed or written to disk.
4. Builds the container from source and deploys it with `min-instances 0`, so it costs nothing while idle.
5. Pins `PUBLIC_BASE_URL` to the deterministic service URL, `https://<service>-<project-number>.<region>.run.app`.

Read the secret back when you need it for a client:

```bash
gcloud secrets versions access latest --secret yahoo-finance-mcp-client-secret
```

> [!IMPORTANT]
> Cloud Run reserves paths ending in `z` (such as `/healthz`), so the health check lives at `/health`.

## Connecting clients

In the examples below, `$URL` is your service URL and `$SECRET` is the client secret.

### Claude.ai / Claude Desktop

Settings → Connectors → **Add custom connector**

- URL: `$URL/mcp`
- Advanced settings → OAuth Client ID: `yahoo-finance-mcp`, OAuth Client Secret: `$SECRET`

Click **Connect**. The connector becomes available in Claude Desktop and mobile on the same account.

### Claude Code

```bash
MCP_CLIENT_SECRET=$SECRET claude mcp add --transport http -s user yahoo-finance "$URL/mcp" \
  --client-id yahoo-finance-mcp --client-secret --callback-port 33418
# Then run /mcp in Claude Code → yahoo-finance → Authenticate
```

Without a browser, use Basic auth instead:

```bash
claude mcp add --transport http -s user yahoo-finance "$URL/mcp" \
  --header "Authorization: Basic $(printf 'yahoo-finance-mcp:%s' "$SECRET" | base64)"
```

### Notion custom agents

You need a Business or Enterprise plan. A workspace admin must turn on **Enable custom MCP servers** under Settings → Connections.

Then open Agent settings → Tools & Access → Add connection → **Custom MCP server**, and set the URL to `$URL/mcp`. Pick one of these:

- **OAuth** (recommended). Authentication = OAuth. Under Advanced settings, set OAuth client ID to `yahoo-finance-mcp` and OAuth client secret to `$SECRET`.
- **Basic**. Username = `yahoo-finance-mcp`, password = `$SECRET`.

### ChatGPT

Add a custom connector (Settings → Connectors, developer mode) with:

- MCP server URL: `$URL/mcp`
- Authentication: OAuth, with client ID `yahoo-finance-mcp` and client secret `$SECRET`

ChatGPT redirects to `https://chatgpt.com/connector_platform_oauth_redirect`, which is allowed by default.

### Anything else

Any MCP client that supports Streamable HTTP can connect, using either OAuth, Basic auth, or a bearer token from `POST /token` (see the curl example above).

## Rotating the secret

```bash
openssl rand -hex 32 | tr -d '\n' | \
  gcloud secrets versions add yahoo-finance-mcp-client-secret --data-file=-
./deploy.sh        # the new revision picks up the new secret; all old tokens become invalid
```

Then update the secret in every client.

## Project layout

```
server.py        MCP server and the 24 tools
auth.py          OAuth 2.1 authorization server, auth middleware, API key CLI
deploy.sh        Cloud Run deployment
Dockerfile       Container image (uv, Python 3.12, non-root)
tests/           Offline and live test suites
skills/stock-data-collector/  Versioned stock-data collection workflow
skills/stock-data-updater/    Incremental latest-data join workflow
skills/stock-backtester/      Rule-based backtest and validation workflow
skills/stock-rule-set-manager/ Versioned trading-rule registry and comparison workflow
skills/stock-backtest-reviewer/ Backtest reconciliation and result-review workflow
skills/stock-strategy-loop-optimizer/ Iterative profit-target rule search and validation workflow
.env.example     Configuration template (no real values)
```

## License

[MIT](LICENSE)

## Acknowledgements

- [yfinance](https://github.com/ranaroussi/yfinance) by Ran Aroussi and contributors
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
