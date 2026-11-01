"""Smoke tests against the real Yahoo Finance API. Run with `uv run pytest -m live`."""

import pytest
from mcp import Client

import server

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def call(name, args=None):
    async with Client(server.mcp) as c:
        r = await c.call_tool(name, args or {})
    assert not r.is_error, r.content
    return r.structured_content


@pytest.mark.parametrize("symbol", ["AAPL", "7203.T", "^N225", "USDJPY=X", "BTC-USD", "CL=F", "SPCX"])
async def test_quote(symbol):
    d = await call("get_quote", {"symbol": symbol})
    assert d["price"] > 0 and d["previousClose"] > 0 and d["as_of"]


async def test_quote_matches_history():
    q = await call("get_quote", {"symbol": "AAPL"})
    h = await call("get_price_history", {"symbol": "AAPL", "period": "5d"})
    closes = [r["Close"] for r in h["rows"]]
    assert q["previousClose"] == pytest.approx(closes[-2], rel=1e-3) or q["price"] == pytest.approx(closes[-1], rel=1e-3)


async def test_prices():
    assert (await call("get_quotes", {"symbols": ["AAPL", "MSFT"]}))["quotes"][1]["price"] > 0
    assert (await call("get_price_history", {"symbol": "AAPL", "period": "1mo"}))["count"] >= 15
    assert (await call("get_multi_price_history", {"symbols": ["AAPL", "7203.T"], "period": "1mo"}))["count"] >= 15


async def test_fundamentals():
    assert (await call("get_company_info", {"symbol": "AAPL"}))["longName"]
    assert (await call("get_financial_statements", {"symbol": "7203.T"}))["periods"]
    assert (await call("get_financial_statements", {"symbol": "AAPL", "freq": "trailing"}))["periods"]
    assert (await call("get_valuation_measures", {"symbol": "AAPL"}))["measures"]
    assert (await call("get_earnings", {"symbol": "AAPL"}))["earnings_dates"]
    assert (await call("get_analyst_data", {"symbol": "AAPL"}))["price_targets"]
    assert (await call("get_holders", {"symbol": "AAPL"}))["institutional_holders"]
    assert (await call("get_dividends_and_splits", {"symbol": "AAPL"}))["dividends"]
    assert (await call("get_shares_outstanding", {"symbol": "AAPL"}))["latest"] > 0
    assert (await call("get_sec_filings", {"symbol": "AAPL"}))["filings"]
    assert (await call("get_fund_data", {"symbol": "SPY"}))["top_holdings"]


async def test_news_and_options():
    assert (await call("get_news", {"symbol": "AAPL", "count": 3}))["news"]
    assert (await call("get_option_chain", {"symbol": "AAPL"}))["calls"]


async def test_discovery():
    assert any(r["symbol"] == "SPCX" for r in (await call("search_symbols", {"query": "SpaceX"}))["results"])
    assert (await call("search_symbols", {"query": "nikkei", "asset_type": "index"}))["results"]
    assert (await call("screen_securities", {"predefined": "most_actives", "count": 5}))["results"]
    d = await call("screen_securities", {"filters": [{"field": "region", "op": "eq", "value": "jp"},
                                                     {"field": "intradaymarketcap", "op": "gt", "value": 10_000_000_000_000}], "count": 5})
    assert all(r["symbol"].endswith(".T") for r in d["results"])
    assert (await call("get_sector", {"sector_key": "technology"}))["top_companies"]
    assert (await call("get_industry", {"industry_key": "semiconductors"}))["top_companies"]


async def test_market_and_calendar():
    d = await call("get_market_summary")
    assert {"^IRX", "^FVX", "^TNX", "^TYX"} <= {i["symbol"] for i in d["RATES"]["items"]}
    assert len(d["CURRENCIES"]["items"]) > 1  # yf.Market.summary collapses these into one item
    for kind in ("earnings", "economic", "ipo", "splits"):
        assert "events" in await call("get_calendar", {"kind": kind, "limit": 5})


async def test_live_ticks_crypto():
    d = await call("get_live_ticks", {"symbols": ["BTC-USD", "ETH-USD"], "seconds": 10})
    assert d["ticks"], d  # crypto streams 24/7
