"""Call every tool over the MCP protocol (in-process) with yfinance stubbed out."""

import json

import pytest
from mcp import Client

import server
from conftest import fake_screen

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("fake_yf")]

EXPECTED_TOOLS = {
    "get_quote", "get_quotes", "get_price_history", "get_multi_price_history",
    "get_company_info", "get_financial_statements", "get_valuation_measures", "get_earnings",
    "get_analyst_data", "get_holders", "get_dividends_and_splits", "get_shares_outstanding",
    "get_sec_filings", "get_fund_data", "get_news", "get_option_chain",
    "search_symbols", "screen_securities", "list_screener_fields", "get_sector", "get_industry",
    "get_market_summary", "get_calendar", "get_live_ticks",
}


async def call(name, args=None):
    async with Client(server.mcp) as c:
        r = await c.call_tool(name, args or {})
    assert not r.is_error, r.content
    json.dumps(r.structured_content, allow_nan=False)  # must be valid JSON for the client
    return r.structured_content


async def call_error(name, args=None) -> str:
    async with Client(server.mcp) as c:
        r = await c.call_tool(name, args or {})
    assert r.is_error
    return r.content[0].text


async def test_list_tools():
    async with Client(server.mcp) as c:
        tools = await c.list_tools()
    assert {t.name for t in tools.tools} == EXPECTED_TOOLS


# --- prices ---------------------------------------------------------------------


async def test_get_quote_uses_metadata_previous_close():
    d = await call("get_quote", {"symbol": "SPCX"})
    assert d["previousClose"] == 144.75  # not fast_info.previousClose (146.45)
    assert d["change"] == pytest.approx(2.74)
    assert d["changePercent"] == pytest.approx(1.8929, abs=1e-3)
    assert d["as_of"] == "2026-10-23T16:00:00-04:00"
    assert d["marketCap"] is None  # NaN becomes null


async def test_get_quote_not_found_has_readable_message():
    msg = await call_error("get_quote", {"symbol": "ZZZZ"})
    assert "ZZZZ" in msg and "search_symbols" in msg


async def test_get_quotes_mixes_success_and_error():
    d = await call("get_quotes", {"symbols": ["AAPL", "ZZZZ"]})
    assert d["quotes"][0]["price"] == 147.49
    assert "No Yahoo Finance data found" in d["quotes"][1]["error"]


async def test_get_price_history_compacts_rows():
    d = await call("get_price_history", {"symbol": "AAPL", "period": "5d"})
    row = d["rows"][0]
    assert row["Open"] == 160.5612  # rounded to 4 decimals
    assert "Dividends" not in row and "Stock Splits" not in row  # all-zero columns are dropped
    assert d["currency"] == "USD" and d["count"] == 2


async def test_get_price_history_empty_has_note():
    d = await call("get_price_history", {"symbol": "ZZZZ", "interval": "1m", "period": "1mo"})
    assert d["count"] == 0 and "1m covers the last 8 days" in d["note"]


async def test_get_price_history_truncation_note():
    d = await call("get_price_history", {"symbol": "AAPL", "max_rows": 1})
    assert d["truncated"] and len(d["rows"]) == 1 and d["rows"][0]["Close"] == 144.75


async def test_get_multi_price_history():
    d = await call("get_multi_price_history", {"symbols": ["AAPL", "7203.T"], "field": "Close"})
    assert d["rows"][-1] == {"date": "2026-10-22", "AAPL": 2.1235, "7203.T": 2.1235}


# --- company information -------------------------------------------------------


async def test_get_company_info_trims_officers():
    d = await call("get_company_info", {"symbol": "AAPL"})
    assert d["companyOfficers"] == [{"name": "A", "title": "CEO"}]
    assert d["trailingPE"] is None


async def test_get_company_info_not_found():
    assert "No Yahoo Finance data found" in await call_error("get_company_info", {"symbol": "ZZZZ"})


@pytest.mark.parametrize("statement", ["income", "balance", "cashflow"])
async def test_get_financial_statements(statement):
    d = await call("get_financial_statements", {"symbol": "AAPL", "statement": statement, "freq": "quarterly"})
    assert d["periods"] == {"2025-12-31": {"Total Revenue": 100.0}, "2024-12-31": {"Total Revenue": 80.0, "Other": 5.0}}


async def test_balance_sheet_has_no_ttm():
    assert "TTM" in await call_error("get_financial_statements", {"symbol": "AAPL", "statement": "balance", "freq": "trailing"})


async def test_invalid_enum_is_rejected():
    await call_error("get_financial_statements", {"symbol": "AAPL", "statement": "bogus"})
    await call_error("get_valuation_measures", {"symbol": "AAPL", "freq": "annual"})


async def test_get_valuation_measures():
    d = await call("get_valuation_measures", {"symbol": "AAPL"})
    assert d["measures"][0] == {"measure": "Market Cap", "Current": 1.0e12, "6/30/2026": 0.9e12}


async def test_get_earnings():
    d = await call("get_earnings", {"symbol": "AAPL"})
    assert d["calendar"]["Earnings Date"] == ["2026-11-03"]
    assert d["earnings_dates"][0]["date"] == "2026-11-02T16:00:00-05:00"
    assert d["earnings_history"][0] == {"quarter": "2026-06-30", "epsActual": 1.85}


async def test_get_analyst_data_all_and_include():
    d = await call("get_analyst_data", {"symbol": "AAPL"})
    assert d["price_targets"]["mean"] == 257.49
    assert d["upgrades_downgrades"][0]["date"] == "2026-10-20"
    assert d["growth_estimates"] is None  # unavailable parts are null; the tool still succeeds
    d = await call("get_analyst_data", {"symbol": "AAPL", "include": ["price_targets"]})
    assert set(d) == {"symbol", "price_targets"}


async def test_get_holders():
    d = await call("get_holders", {"symbol": "AAPL", "insider_limit": 5})
    assert d["major_holders"] == {"insidersPercentHeld": 0.01, "institutionsPercentHeld": 0.66}
    assert len(d["insider_transactions"]) == 5
    assert d["institutional_holders"][0]["Holder"] == "Blackrock"


async def test_get_dividends_and_splits_newest_first():
    d = await call("get_dividends_and_splits", {"symbol": "AAPL", "limit": 1})
    assert d["dividends"] == [{"date": "2026-08-10", "dividend": 0.26}]
    assert d["dividends_total_count"] == 2 and d["splits"] == [] and d["capital_gains"] == []


async def test_get_shares_outstanding_monthly_changes():
    d = await call("get_shares_outstanding", {"symbol": "AAPL"})
    assert d["latest"] == 99
    assert d["monthly_changes"] == [{"month": "2026-01", "shares": 101}, {"month": "2026-03", "shares": 99}]


async def test_get_sec_filings():
    d = await call("get_sec_filings", {"symbol": "AAPL"})
    assert d["filings"] == [{"date": "2026-09-01", "type": "10-K", "title": "Annual", "edgarUrl": "u"}]


async def test_get_fund_data():
    d = await call("get_fund_data", {"symbol": "SPY"})
    assert d["top_holdings"] == [{"symbol": "NVDA", "Name": "NVIDIA"}]
    assert d["fund_operations"][0]["attribute"] == "Expense Ratio"


async def test_get_news_falls_back_to_search():
    d = await call("get_news", {"symbol": "SPCX", "count": 3})
    assert d["news"][0]["title"] == "SpaceX news"


async def test_get_option_chain_trims_strikes():
    d = await call("get_option_chain", {"symbol": "AAPL", "strikes_around": 2})
    assert d["underlying_price"] == 147.49
    assert [c["strike"] for c in d["calls"]] == [135.0, 140.0, 145.0, 150.0, 155.0]  # 2 strikes on each side of 145, the strike nearest 147.49
    assert "contractSize" not in d["calls"][0]
    full = await call("get_option_chain", {"symbol": "AAPL", "strikes_around": 0})
    assert len(full["calls"]) == 20


async def test_get_option_chain_bad_expiration():
    assert "2026-10-30" in await call_error("get_option_chain", {"symbol": "AAPL", "expiration": "2099-01-01"})


# --- search, screening and sectors ---------------------------------------------


async def test_search_symbols():
    d = await call("search_symbols", {"query": "SpaceX", "include_research": True})
    assert d["results"][0]["symbol"] == "SPCX" and d["research"] == [{"reportHeadline": "R"}]


async def test_search_symbols_lookup_by_type():
    d = await call("search_symbols", {"query": "nikkei", "asset_type": "index"})
    assert d["results"] == [{"symbol": "^N225", "shortName": "Nikkei 225", "regularMarketPrice": 62693.16}]


async def test_screen_predefined_and_unknown():
    d = await call("screen_securities", {"predefined": "day_gainers", "count": 5})
    assert d["results"] == [{"symbol": "8306.T", "shortName": "MUFG", "marketCap": 3.7e13}]
    assert fake_screen.last == ("day_gainers", {"count": 5})
    d = await call("screen_securities", {"predefined": "day_gainers", "count": 5, "offset": 10})
    assert fake_screen.last == ("day_gainers", {"offset": 10, "size": 5}) and d["offset"] == 10
    assert "day_gainers" in await call_error("screen_securities", {"predefined": "nope"})
    assert "either" in await call_error("screen_securities", {})


async def test_screen_custom_filters_build_query():
    await call("screen_securities", {"filters": [
        {"field": "region", "op": "eq", "value": "jp"},
        {"field": "peratio.lasttwelvemonths", "op": "btwn", "value": [5, 10]},
    ], "count": 3})
    query, kw = fake_screen.last
    assert query.to_dict() == {"operator": "AND", "operands": [
        {"operator": "EQ", "operands": ["region", "jp"]},
        {"operator": "BTWN", "operands": ["peratio.lasttwelvemonths", 5, 10]},
    ]}
    assert kw == {"size": 3, "offset": 0, "sortField": "intradaymarketcap", "sortAsc": False}


async def test_list_screener_fields():
    d = await call("list_screener_fields")
    assert "intradaymarketcap" in d["fields"]["price"]
    assert "jp" in d["eq_values"]["region"] and "peer_group" not in d["eq_values"]


async def test_get_sector_and_industry():
    d = await call("get_sector", {"sector_key": "technology"})
    assert d["name"] == "Technology" and len(d["top_companies"]) == 25
    assert d["industries"] == [{"key": "semiconductors", "name": "Semiconductors"}]
    d = await call("get_industry", {"industry_key": "semiconductors"})
    assert d["sector_key"] == "technology" and len(d["top_growth_companies"]) == 5


# --- market overview and calendars ---------------------------------------------


async def test_get_market_summary_keeps_same_exchange_items():
    d = await call("get_market_summary", {"regions": ["rates"]})
    items = d["RATES"]["items"]
    assert [i["symbol"] for i in items] == ["^TNX", "^TYX"]  # both survive even though they share an exchange (CGI)
    assert items[0]["as_of"] is not None
    assert d["RATES"]["status"]["status"] == "closed"


async def test_get_market_summary_rejects_unknown_region():
    assert "JP" in await call_error("get_market_summary", {"regions": ["JP"]})


@pytest.mark.parametrize("kind,key", [("earnings", "symbol"), ("economic", "event"), ("ipo", "symbol"), ("splits", "symbol")])
async def test_get_calendar(kind, key):
    d = await call("get_calendar", {"kind": kind, "start": "2026-10-26", "end": "2026-10-30"})
    assert key in d["events"][0]


# --- real-time stream ----------------------------------------------------------


class FakeWS:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, m):
        self.sent.append(m)

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        import asyncio

        await asyncio.sleep(3600)


async def test_get_live_ticks(monkeypatch):
    import websockets.asyncio.client as wsc

    frames = ['{"type":"pricing","message":"BTC"}', '{"type":"pricing","message":"BTC2"}', '{"type":"pricing","message":"ETH"}']
    decoded = {
        "BTC": {"id": "BTC-USD", "price": 1.0, "time": "1792818769000", "market_hours": 1},
        "BTC2": {"id": "BTC-USD", "price": 2.0, "time": "1792818770000", "market_hours": 2, "junk": 1},
        "ETH": {"id": "ETH-USD", "price": 3.0},
    }
    ws = FakeWS(frames)
    monkeypatch.setattr(wsc, "connect", lambda *a, **k: ws)
    monkeypatch.setattr(server.yf.AsyncWebSocket, "_decode_message", lambda self, m: decoded[m])
    d = await call("get_live_ticks", {"symbols": ["BTC-USD", "ETH-USD", "AAPL"], "seconds": 1})
    assert json.loads(ws.sent[0]) == {"subscribe": ["BTC-USD", "ETH-USD", "AAPL"]}
    btc = d["ticks"][0]
    assert btc["price"] == 2.0 and btc["session"] == "post" and "junk" not in btc
    assert d["tick_counts"] == {"BTC-USD": 2, "ETH-USD": 1}
    assert d["no_ticks"] == ["AAPL"]
