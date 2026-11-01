"""Yahoo Finance MCP server (Streamable HTTP + OAuth 2.1).

The only data source is the Yahoo Finance API, accessed through yfinance.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import math
import os
import time
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Literal

import pandas as pd
import uvicorn
import yfinance as yf
from auth import AuthConfig, AuthMiddleware, register_routes
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp
from yfinance.const import _QUERY1_URL_, EQUITY_SCREENER_EQ_MAP, EQUITY_SCREENER_FIELDS, FUND_SCREENER_FIELDS

warnings.filterwarnings("ignore", module="yfinance")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yahoo-finance-mcp")

mcp = MCPServer(
    name="yahoo-finance",
    instructions=(
        "Tools for Yahoo Finance data. "
        "Tokyo-listed stocks use a '.T' suffix (e.g. '7203.T'). Indices look like '^N225' or '^GSPC', "
        "currencies like 'USDJPY=X', futures like 'CL=F', and crypto like 'BTC-USD'. "
        "If you don't know a ticker, call search_symbols first (search in English). "
        "Data that Yahoo Finance does not provide, such as Japanese government bond yields, is not available."
    ),
)


def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Drop-in replacement for mcp.tool() that turns exceptions into ToolError.

    The MCP SDK hides the message of any exception other than ToolError, which leaves
    the client with no idea what went wrong.
    """

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except ToolError:
                raise
            except Exception as e:
                raise ToolError(str(e) or type(e).__name__) from e

        return mcp.tool()(awrapper)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(str(e) or type(e).__name__) from e

    return mcp.tool()(wrapper)


# ---------------------------------------------------------------------------
# JSON conversion helpers
# ---------------------------------------------------------------------------


def _key(k: Any) -> str:
    if isinstance(k, (pd.Timestamp, datetime)):
        return k.date().isoformat() if (k.hour, k.minute, k.second) == (0, 0, 0) else k.isoformat()
    if isinstance(k, date):
        return k.isoformat()
    return str(k)


def _clean(value: Any) -> Any:
    """Convert pandas / numpy values into something JSON-serializable."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {_key(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return _records(value)
    if isinstance(value, pd.Series):
        return _clean(value.to_dict())
    if isinstance(value, (pd.Timestamp, datetime)) and value.tzinfo is None:
        return _key(value)  # naive midnight becomes a plain date
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _records(df: pd.DataFrame | None, index_name: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Turn a DataFrame into a list of row dicts, keeping a meaningful index as a column."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    if not isinstance(df.index, pd.RangeIndex):
        name = index_name or df.index.name or "key"
        df = df.reset_index(names=name)
    rows = [_clean(r) for r in df.to_dict(orient="records")]
    return rows[:limit] if limit is not None else rows


def _compact_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Drop all-zero dividend/split columns and round prices to 4 decimals to keep responses small."""
    if df is None or df.empty:
        return df
    drop = [c for c in ("Dividends", "Stock Splits", "Capital Gains") if c in df.columns and not df[c].any()]
    df = df.drop(columns=drop)
    price_cols = [c for c in ("Open", "High", "Low", "Close", "Adj Close") if c in df.columns]
    df[price_cols] = df[price_cols].round(4)
    return df


def _series_records(s: pd.Series | None, value_name: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Turn a date-indexed Series into [{date, value_name}], newest first."""
    if s is None or not isinstance(s, pd.Series) or s.empty:
        return []
    s = s.sort_index(ascending=False)
    if limit is not None:
        s = s.head(limit)
    return [{"date": _key(k), value_name: _clean(v)} for k, v in s.items()]


def _statement(df: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    """Turn a financial statement (rows = line items, columns = periods) into {period: {item: value}}."""
    if df is None or df.empty:
        return {}
    return {_key(col): _clean(df[col].dropna().to_dict()) for col in df.columns}


def _safe(fn: Callable[[], Any], label: str, symbol: str = "") -> Any:
    """Return None for data a symbol doesn't have, instead of failing the whole tool."""
    try:
        return fn()
    except Exception as e:
        logger.info("%s unavailable for %s: %s", label, symbol, e)
        return None


def _not_found(symbol: str) -> ValueError:
    return ValueError(f"No Yahoo Finance data found for '{symbol}'. Use search_symbols to find the correct ticker.")


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------


def _quote_core(symbol: str) -> dict[str, Any]:
    """Read Yahoo's own price, previous close and timestamp from the chart metadata.

    fast_info.previousClose is often off by a day, so it is not used.
    """
    t = yf.Ticker(symbol)
    hist = t.history(period="5d", auto_adjust=False)
    meta = t.history_metadata or {}
    price = _clean(meta.get("regularMarketPrice"))
    if price is None:
        if hist.empty:
            raise _not_found(symbol)
        price = _clean(hist["Close"].iloc[-1])
    prev = _clean(meta.get("previousClose"))
    if prev is None and len(hist) >= 2:
        prev = _clean(hist["Close"].iloc[-2])
    q: dict[str, Any] = {
        "symbol": symbol,
        "name": meta.get("longName") or meta.get("shortName"),
        "price": price,
        "previousClose": prev,
        "change": price - prev if prev else None,
        "changePercent": (price - prev) / prev * 100 if prev else None,
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "instrumentType": meta.get("instrumentType"),
        "as_of": _clean(meta.get("regularMarketTime")),
        "dayHigh": _clean(meta.get("regularMarketDayHigh")),
        "dayLow": _clean(meta.get("regularMarketDayLow")),
        "volume": _clean(meta.get("regularMarketVolume")),
        "fiftyTwoWeekHigh": _clean(meta.get("fiftyTwoWeekHigh")),
        "fiftyTwoWeekLow": _clean(meta.get("fiftyTwoWeekLow")),
    }
    period = meta.get("currentTradingPeriod") or {}
    if period:
        q["tradingPeriod"] = {k: _clean({"start": v.get("start"), "end": v.get("end")}) for k, v in period.items() if isinstance(v, dict)}
    return q


@tool
def get_quote(symbol: str) -> dict[str, Any]:
    """Get the current price, change vs. previous close, day range, volume, 52-week range, market cap and moving averages for one symbol.

    Args:
        symbol: Ticker (e.g. AAPL, 7203.T, ^N225, USDJPY=X, BTC-USD, CL=F)
    """
    q = _quote_core(symbol)
    fi = _safe(lambda: yf.Ticker(symbol).fast_info, "fast_info", symbol)
    if fi is not None:
        for k in ("marketCap", "shares", "open", "fiftyDayAverage", "twoHundredDayAverage",
                  "tenDayAverageVolume", "threeMonthAverageVolume", "yearChange", "timezone", "quoteType"):
            q[k] = _safe(lambda k=k: _clean(fi[k]), f"fast_info.{k}", symbol)
    return q


@tool
def get_quotes(symbols: list[str]) -> dict[str, Any]:
    """Get the current price and change for several symbols at once (up to 50). Useful for comparisons and watchlists.

    Args:
        symbols: List of tickers (e.g. ["AAPL", "MSFT", "7203.T"])
    """
    symbols = symbols[:50]

    def one(s: str) -> dict[str, Any]:
        try:
            return _quote_core(s)
        except Exception as e:
            return {"symbol": s, "error": str(e) or type(e).__name__}

    with ThreadPoolExecutor(max_workers=8) as ex:
        return {"quotes": list(ex.map(one, symbols))}


_INTRADAY_LIMITS = "Intraday limits: 1m covers the last 8 days, 2m-90m the last 60 days, 1h the last 730 days."


@tool
def get_price_history(
    symbol: str,
    period: str = "1mo",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    prepost: bool = False,
    auto_adjust: bool = False,
    max_rows: int = 250,
) -> dict[str, Any]:
    """Get OHLCV price history for one symbol, with dividend and split columns when present.

    Args:
        symbol: Ticker
        period: 1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max (ignored when start is given)
        interval: 1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo (intraday intervals have lookback limits)
        start: Start date YYYY-MM-DD
        end: End date YYYY-MM-DD (exclusive)
        prepost: Include US pre/post-market data (intraday only)
        auto_adjust: Return dividend/split-adjusted prices (drops the Adj Close column)
        max_rows: Maximum rows to return (newest first). For long ranges use interval 1wk or 1mo
    """
    t = yf.Ticker(symbol)
    kw = dict(interval=interval, prepost=prepost, auto_adjust=auto_adjust)
    df = t.history(start=start, end=end, **kw) if start else t.history(period=period, **kw)
    rows = _records(_compact_ohlcv(df), "date")
    result: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "currency": (t.history_metadata or {}).get("currency"),
        "count": len(rows),
        "truncated": len(rows) > max_rows,
        "rows": rows[-max_rows:],
    }
    if not rows:
        result["note"] = f"No data. Check that the ticker is correct. {_INTRADAY_LIMITS}"
    elif result["truncated"]:
        result["note"] = f"Returned the newest {max_rows} of {len(rows)} rows. Use a coarser interval or a shorter range."
    return result


@tool
def get_multi_price_history(
    symbols: list[str],
    period: str = "1mo",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    field: Literal["Close", "Open", "High", "Low", "Volume"] = "Close",
    auto_adjust: bool = True,
    max_rows: int = 250,
) -> dict[str, Any]:
    """Get price history for several symbols as one table (date × symbol). Useful for correlation and performance comparisons. Up to 20 symbols.

    Args:
        symbols: List of tickers
        period / interval / start / end: Same as get_price_history
        field: Column to return (Close, Open, High, Low, Volume)
        auto_adjust: Dividend/split-adjusted prices (default)
        max_rows: Maximum rows to return (newest first)
    """
    symbols = symbols[:20]
    kw = dict(interval=interval, auto_adjust=auto_adjust, progress=False, threads=True)
    df = yf.download(symbols, start=start, end=end, **kw) if start else yf.download(symbols, period=period, **kw)
    if df is None or df.empty:
        return {"symbols": symbols, "count": 0, "rows": [], "note": "No data. " + _INTRADAY_LIMITS}
    table = df[field] if isinstance(df.columns, pd.MultiIndex) else df[[field]].rename(columns={field: symbols[0]})
    rows = _records(table.dropna(how="all").round(4), "date")
    return {
        "symbols": symbols,
        "field": field,
        "interval": interval,
        "count": len(rows),
        "truncated": len(rows) > max_rows,
        "rows": rows[-max_rows:],
    }


# ---------------------------------------------------------------------------
# Company information and fundamentals
# ---------------------------------------------------------------------------


@tool
def get_company_info(symbol: str) -> dict[str, Any]:
    """Get the company profile, sector, industry, employees, officers, valuation ratios (P/E, P/B, ...), margins and financial summary.

    Note: dividendYield is a percentage (3.22 = 3.22%), while trailingAnnualDividendYield is a ratio (0.0308 = 3.08%).
    For ETF and mutual fund details use get_fund_data.
    """
    info = yf.Ticker(symbol).info or {}
    if len(info) <= 2:
        raise _not_found(symbol)
    officers = info.pop("companyOfficers", None) or []
    info.pop("executiveTeam", None)
    info["companyOfficers"] = [
        {k: o.get(k) for k in ("name", "title", "age", "totalPay") if o.get(k) is not None} for o in officers[:10]
    ]
    return _clean(info)


@tool
def get_financial_statements(
    symbol: str,
    statement: Literal["income", "balance", "cashflow"] = "income",
    freq: Literal["yearly", "quarterly", "trailing"] = "yearly",
) -> dict[str, Any]:
    """Get the income statement, balance sheet or cash flow statement.

    Args:
        symbol: Ticker
        statement: income, balance or cashflow
        freq: yearly, quarterly or trailing (trailing twelve months; not available for balance)
    """
    if statement == "balance" and freq == "trailing":
        raise ValueError("The balance sheet has no TTM view. Use freq 'yearly' or 'quarterly'.")
    t = yf.Ticker(symbol)
    getter = {"income": t.get_income_stmt, "balance": t.get_balance_sheet, "cashflow": t.get_cash_flow}[statement]
    return {"symbol": symbol, "statement": statement, "freq": freq, "periods": _statement(getter(freq=freq))}


@tool
def get_valuation_measures(
    symbol: str, freq: Literal["quarterly", "monthly", "yearly", "trailing"] = "quarterly", periods: int = 5
) -> dict[str, Any]:
    """Get market cap, enterprise value, P/E, forward P/E, PEG, P/S, P/B, EV/Revenue and EV/EBITDA, current and at past period ends."""
    df = yf.Ticker(symbol).get_valuation_measures(freq=freq, periods=periods)
    return {"symbol": symbol, "freq": freq, "measures": _records(df, "measure")}


@tool
def get_earnings(symbol: str, limit: int = 12) -> dict[str, Any]:
    """Get earnings data: next earnings date and estimates (calendar), EPS estimate / reported / surprise per earnings date, and the last 4 quarters of EPS.

    Args:
        symbol: Ticker
        limit: Number of earnings_dates entries (includes upcoming dates)
    """
    t = yf.Ticker(symbol)
    return {
        "symbol": symbol,
        "calendar": _clean(_safe(lambda: t.calendar, "calendar", symbol)),
        "earnings_dates": _records(_safe(lambda: t.get_earnings_dates(limit=limit), "earnings_dates", symbol), "date"),
        "earnings_history": _records(_safe(lambda: t.earnings_history, "earnings_history", symbol), "quarter"),
    }


_ANALYST_PARTS = (
    "price_targets",
    "recommendations",
    "upgrades_downgrades",
    "earnings_estimate",
    "revenue_estimate",
    "eps_trend",
    "eps_revisions",
    "growth_estimates",
)


@tool
def get_analyst_data(symbol: str, include: list[str] | None = None) -> dict[str, Any]:
    """Get analyst data.

    Args:
        symbol: Ticker
        include: Parts to return (all when omitted):
            price_targets, recommendations (rating distribution over time),
            upgrades_downgrades (latest 30), earnings_estimate, revenue_estimate,
            eps_trend, eps_revisions, growth_estimates
    """
    t = yf.Ticker(symbol)
    getters: dict[str, Callable[[], Any]] = {
        "price_targets": lambda: _clean(t.analyst_price_targets),
        "recommendations": lambda: _records(t.recommendations),
        "upgrades_downgrades": lambda: _records(t.upgrades_downgrades, "date", limit=30),
        "earnings_estimate": lambda: _records(t.earnings_estimate, "period"),
        "revenue_estimate": lambda: _records(t.revenue_estimate, "period"),
        "eps_trend": lambda: _records(t.eps_trend, "period"),
        "eps_revisions": lambda: _records(t.eps_revisions, "period"),
        "growth_estimates": lambda: _records(t.growth_estimates, "period"),
    }
    parts = [p for p in (include or _ANALYST_PARTS) if p in getters]
    return {"symbol": symbol, **{p: _safe(getters[p], p, symbol) for p in parts}}


@tool
def get_holders(symbol: str, insider_limit: int = 30) -> dict[str, Any]:
    """Get holder data: ownership breakdown (insiders / institutions), top institutions, top mutual funds, insider transactions, insider purchase summary and insider roster."""
    t = yf.Ticker(symbol)
    major = _safe(lambda: t.major_holders, "major_holders", symbol)
    return {
        "symbol": symbol,
        "major_holders": _clean(major["Value"].to_dict()) if isinstance(major, pd.DataFrame) and not major.empty else {},
        "institutional_holders": _records(_safe(lambda: t.institutional_holders, "institutional", symbol)),
        "mutualfund_holders": _records(_safe(lambda: t.mutualfund_holders, "mutualfund", symbol)),
        "insider_transactions": _records(_safe(lambda: t.insider_transactions, "insider_tx", symbol), limit=insider_limit),
        "insider_purchases": _records(_safe(lambda: t.insider_purchases, "insider_purchases", symbol)),
        "insider_roster": _records(_safe(lambda: t.insider_roster_holders, "insider_roster", symbol)),
    }


@tool
def get_dividends_and_splits(symbol: str, limit: int = 40) -> dict[str, Any]:
    """Get dividend, stock split and capital gain distribution (funds/ETFs) history, newest first. Dates are ex-dates.

    Args:
        symbol: Ticker
        limit: Maximum entries per history
    """
    t = yf.Ticker(symbol)
    dividends = _safe(lambda: t.dividends, "dividends", symbol)
    return {
        "symbol": symbol,
        "dividends_total_count": int(len(dividends)) if isinstance(dividends, pd.Series) else 0,
        "dividends": _series_records(dividends, "dividend", limit),
        "splits": _series_records(_safe(lambda: t.splits, "splits", symbol), "ratio", limit),
        "capital_gains": _series_records(_safe(lambda: t.capital_gains, "capital_gains", symbol), "capital_gain", limit),
    }


@tool
def get_shares_outstanding(symbol: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Get shares outstanding over time, aggregated to month-end values and returning only months where the count changed. Useful for spotting buybacks and dilution.

    Args:
        symbol: Ticker
        start: Start date YYYY-MM-DD (defaults to about 18 months ago)
        end: End date YYYY-MM-DD
    """
    s = yf.Ticker(symbol).get_shares_full(start=start, end=end)
    if s is None or s.empty:
        return {"symbol": symbol, "monthly_changes": []}
    s = s[~s.index.duplicated(keep="last")].sort_index()
    monthly = s.groupby(s.index.strftime("%Y-%m")).last()  # daily values are noisy, so use month-end values
    changed = monthly[monthly.ne(monthly.shift())]
    return {
        "symbol": symbol,
        "latest": _clean(s.iloc[-1]),
        "latest_date": _key(s.index[-1]),
        "monthly_changes": [{"month": k, "shares": _clean(v)} for k, v in changed.items()],
    }


@tool
def get_sec_filings(symbol: str, limit: int = 20) -> dict[str, Any]:
    """Get SEC filings (10-K, 10-Q, 8-K, ...), newest first. US-listed companies only."""
    filings = yf.Ticker(symbol).sec_filings or []
    if isinstance(filings, dict):
        filings = []
    keys = ("date", "type", "title", "edgarUrl")
    return {"symbol": symbol, "filings": [_clean({k: f.get(k) for k in keys}) for f in filings[:limit]]}


@tool
def get_fund_data(symbol: str) -> dict[str, Any]:
    """Get ETF / mutual fund details: overview, expenses, asset allocation, top holdings, sector weights, equity/bond characteristics and bond ratings."""
    fd = yf.Ticker(symbol).funds_data
    parts: dict[str, Callable[[], Any]] = {
        "description": lambda: fd.description,
        "fund_overview": lambda: _clean(fd.fund_overview),
        "fund_operations": lambda: _records(fd.fund_operations, "attribute"),
        "asset_classes": lambda: _clean(fd.asset_classes),
        "top_holdings": lambda: _records(fd.top_holdings, "symbol"),
        "sector_weightings": lambda: _clean(fd.sector_weightings),
        "equity_holdings": lambda: _records(fd.equity_holdings, "metric"),
        "bond_holdings": lambda: _records(fd.bond_holdings, "metric"),
        "bond_ratings": lambda: _clean(fd.bond_ratings),
    }
    result = {"symbol": symbol, **{k: _safe(f, f"funds.{k}", symbol) for k, f in parts.items()}}
    if all(result[k] in (None, "", {}, []) for k in parts):
        raise ValueError(f"No fund data for '{symbol}'. Pass an ETF or mutual fund ticker.")
    return result


@tool
def get_news(symbol: str, count: int = 10) -> dict[str, Any]:
    """Get news related to a symbol, index or keyword. Individual Japanese stocks often have no news."""
    out: list[dict[str, Any]] = []
    for item in _safe(lambda: yf.Ticker(symbol).get_news(count=count), "news", symbol) or []:
        c = item.get("content", item)
        out.append(
            {
                "title": c.get("title"),
                "summary": c.get("summary"),
                "published": c.get("pubDate"),
                "provider": (c.get("provider") or {}).get("displayName"),
                "url": (c.get("canonicalUrl") or c.get("clickThroughUrl") or {}).get("url"),
            }
        )
    if not out:  # Ticker.news often comes back empty, so fall back to the search API
        for n in yf.Search(symbol, max_results=0, news_count=count).news:
            ts = n.get("providerPublishTime")
            out.append(
                {
                    "title": n.get("title"),
                    "published": datetime.fromtimestamp(ts).astimezone().isoformat() if ts else None,
                    "provider": n.get("publisher"),
                    "url": n.get("link"),
                    "related_tickers": n.get("relatedTickers"),
                }
            )
    return {"symbol": symbol, "news": out}


_OPTION_COLUMNS = ["contractSymbol", "strike", "lastPrice", "bid", "ask", "change", "percentChange",
                   "volume", "openInterest", "impliedVolatility", "inTheMoney", "lastTradeDate"]


@tool
def get_option_chain(symbol: str, expiration: str | None = None, strikes_around: int = 10) -> dict[str, Any]:
    """Get the option chain (calls and puts). Mostly available for US stocks, ETFs and indices.

    Args:
        symbol: Ticker
        expiration: Expiration date YYYY-MM-DD (nearest expiration when omitted). All expirations are listed in `expirations`
        strikes_around: Number of strikes to return above and below the spot price. 0 returns every strike
    """
    t = yf.Ticker(symbol)
    expirations = list(t.options)
    if not expirations:
        return {"symbol": symbol, "expirations": [], "note": "Yahoo Finance has no options for this symbol."}
    if expiration and expiration not in expirations:
        raise ValueError(f"No expiration on {expiration}. Available: {expirations}")
    exp = expiration or expirations[0]
    chain = t.option_chain(exp)
    spot = _clean((chain.underlying or {}).get("regularMarketPrice")) if hasattr(chain, "underlying") else None

    def trim(df: pd.DataFrame) -> list[dict[str, Any]]:
        df = df[[c for c in _OPTION_COLUMNS if c in df.columns]].sort_values("strike")
        if strikes_around and spot is not None and not df.empty:
            i = int((df["strike"] - spot).abs().values.argmin())
            df = df.iloc[max(0, i - strikes_around) : i + strikes_around + 1]
        return _records(df.reset_index(drop=True))

    return {
        "symbol": symbol,
        "underlying_price": spot,
        "expirations": expirations,
        "expiration": exp,
        "calls": trim(chain.calls),
        "puts": trim(chain.puts),
    }


# ---------------------------------------------------------------------------
# Search, screening and sectors
# ---------------------------------------------------------------------------

_LOOKUP_TYPES = Literal["all", "stock", "etf", "mutualfund", "index", "future", "currency", "cryptocurrency"]


@tool
def search_symbols(
    query: str,
    max_results: int = 10,
    asset_type: _LOOKUP_TYPES | None = None,
    include_research: bool = False,
    fuzzy: bool = False,
) -> dict[str, Any]:
    """Search for tickers by company name or keyword (English only, e.g. 'Toyota', 'Nvidia', 'nikkei').

    Args:
        query: Search text
        max_results: Maximum number of results
        asset_type: Restrict results to one asset type (uses Yahoo's lookup API and also returns price and change)
        include_research: Also return analyst research report headlines
        fuzzy: Tolerate spelling mistakes
    """
    if asset_type:
        lk = yf.Lookup(query)
        df = getattr(lk, f"get_{asset_type}")(count=max_results)
        keep = ["symbol", "shortName", "exchange", "quoteType", "industryName",
                "regularMarketPrice", "regularMarketChange", "regularMarketPercentChange"]
        rows = _records(df, "symbol")
        return {"query": query, "asset_type": asset_type, "results": [{k: r.get(k) for k in keep if k in r} for r in rows]}
    s = yf.Search(query, max_results=max_results, news_count=0, include_research=include_research, enable_fuzzy_query=fuzzy)
    keys = ("symbol", "shortname", "longname", "exchange", "quoteType", "sector", "industry")
    result: dict[str, Any] = {
        "query": query,
        "results": [{k: q.get(k) for k in keys if q.get(k) is not None} for q in s.quotes],
    }
    if include_research:
        result["research"] = _clean(s.research)
    return result


_SCREEN_KEEP = [
    "symbol", "shortName", "longName", "exchange", "quoteType", "currency", "regularMarketPrice",
    "regularMarketChangePercent", "regularMarketVolume", "marketCap", "trailingPE", "forwardPE",
    "priceToBook", "dividendYield", "fiftyTwoWeekChangePercent", "averageAnalystRating",
    "ytdReturn", "netExpenseRatio", "fundNetAssets",
]


@tool
def screen_securities(
    predefined: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    security_type: Literal["equity", "fund", "etf"] = "equity",
    sort_field: str | None = None,
    sort_ascending: bool = False,
    count: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Screen securities with Yahoo Finance's screener. Pass either `predefined` or `filters`.

    Args:
        predefined: Name of a predefined screen: day_gainers, day_losers, most_actives, most_shorted_stocks,
            aggressive_small_caps, small_cap_gainers, growth_technology_stocks, undervalued_growth_stocks,
            undervalued_large_caps, top_etfs_us, top_performing_etfs, technology_etfs, bond_etfs,
            conservative_foreign_funds, high_yield_bond, portfolio_anchors, solid_large_growth_funds,
            solid_midcap_growth_funds, top_mutual_funds (mostly US)
        filters: List of conditions combined with AND. Each item is {"field": ..., "op": ..., "value": ...}.
            op is one of gt, gte, lt, lte, eq, btwn (value=[low, high]), is-in (value=[candidates...]).
            Example (Japanese stocks with market cap above 10 trillion JPY):
                [{"field": "region", "op": "eq", "value": "jp"},
                 {"field": "intradaymarketcap", "op": "gt", "value": 10000000000000}]
            Call list_screener_fields to see valid fields.
        security_type: What `filters` applies to (equity / fund / etf)
        sort_field: Sort field when using filters (e.g. intradaymarketcap, percentchange)
        sort_ascending: Sort ascending
        count: Number of results (max 250)
        offset: Number of results to skip (paging)
    """
    count = max(1, min(count, 250))
    if predefined:
        if predefined not in yf.PREDEFINED_SCREENER_QUERIES:
            raise ValueError(f"Unknown predefined screen: {predefined}. Available: {list(yf.PREDEFINED_SCREENER_QUERIES)}")
        if offset:  # with an offset yfinance switches to the custom-query API, which takes `size` instead of `count`
            res = yf.screen(predefined, offset=offset, size=count)
        else:
            res = yf.screen(predefined, count=count)
    elif filters:
        Q = {"equity": yf.EquityQuery, "fund": yf.FundQuery, "etf": yf.ETFQuery}[security_type]
        conds = []
        for f in filters:
            op, field, value = f["op"].lower(), f["field"], f["value"]
            operand = [field, *value] if isinstance(value, list) else [field, value]
            conds.append(Q(op, operand))
        query = conds[0] if len(conds) == 1 else Q("and", conds)
        res = yf.screen(query, size=count, offset=offset, sortField=sort_field or "intradaymarketcap", sortAsc=sort_ascending)
    else:
        raise ValueError("Pass either `predefined` or `filters`.")
    quotes = [{k: q.get(k) for k in _SCREEN_KEEP if q.get(k) is not None} for q in res.get("quotes", [])]
    return {"total": res.get("total"), "offset": offset, "count": len(quotes), "results": _clean(quotes)}


@tool
def list_screener_fields(security_type: Literal["equity", "fund", "etf"] = "equity") -> dict[str, Any]:
    """List the field names usable in screen_securities filters, plus valid values for region, sector, exchange, etc."""
    if security_type == "equity":
        fields = EQUITY_SCREENER_FIELDS
        # peer_group has hundreds of values, so leave it out
        enums = {k: sorted(v) for k, v in EQUITY_SCREENER_EQ_MAP.items() if k != "peer_group"}
    else:
        from yfinance.const import ETF_SCREENER_FIELDS, FUND_SCREENER_EQ_MAP

        fields = FUND_SCREENER_FIELDS if security_type == "fund" else ETF_SCREENER_FIELDS
        enums = {k: sorted(v) for k, v in FUND_SCREENER_EQ_MAP.items()}
    return {"security_type": security_type, "fields": _clean(fields), "eq_values": _clean(enums)}


@tool
def get_sector(sector_key: str, region: str = "US") -> dict[str, Any]:
    """Get a sector's overview, market cap, top companies, top ETFs and mutual funds, industries and research reports.

    Args:
        sector_key: basic-materials, communication-services, consumer-cyclical, consumer-defensive, energy,
            financial-services, healthcare, industrials, real-estate, technology, utilities
        region: Region (US, JP, ...). Top companies are taken from this region
    """
    s = yf.Sector(sector_key, region=region)
    return {
        "key": sector_key,
        "name": _safe(lambda: s.name, "sector.name", sector_key),
        "symbol": _safe(lambda: s.symbol, "sector.symbol", sector_key),
        "overview": _clean(_safe(lambda: s.overview, "sector.overview", sector_key)),
        "top_companies": _records(_safe(lambda: s.top_companies, "sector.top", sector_key), "symbol", limit=25),
        "top_etfs": _clean(_safe(lambda: s.top_etfs, "sector.etfs", sector_key)),
        "top_mutual_funds": _clean(_safe(lambda: s.top_mutual_funds, "sector.funds", sector_key)),
        "industries": _records(_safe(lambda: s.industries, "sector.industries", sector_key), "key"),
        "research_reports": _clean(_safe(lambda: s.research_reports, "sector.research", sector_key)),
    }


@tool
def get_industry(industry_key: str, region: str = "US") -> dict[str, Any]:
    """Get an industry's overview, top companies, top growth companies, top performers and research reports.

    Args:
        industry_key: Industry key (e.g. semiconductors, auto-manufacturers, banks-regional). See `industries` in get_sector
        region: Region (US, JP, ...)
    """
    i = yf.Industry(industry_key, region=region)
    return {
        "key": industry_key,
        "name": _safe(lambda: i.name, "industry.name", industry_key),
        "symbol": _safe(lambda: i.symbol, "industry.symbol", industry_key),
        "sector_key": _safe(lambda: i.sector_key, "industry.sector", industry_key),
        "overview": _clean(_safe(lambda: i.overview, "industry.overview", industry_key)),
        "top_companies": _records(_safe(lambda: i.top_companies, "industry.top", industry_key), "symbol", limit=25),
        "top_growth_companies": _records(_safe(lambda: i.top_growth_companies, "industry.growth", industry_key), "symbol"),
        "top_performing_companies": _records(_safe(lambda: i.top_performing_companies, "industry.perf", industry_key), "symbol"),
        "research_reports": _clean(_safe(lambda: i.research_reports, "industry.research", industry_key)),
    }


# ---------------------------------------------------------------------------
# Market overview and calendars
# ---------------------------------------------------------------------------

MARKET_REGIONS = ("US", "ASIA", "EUROPE", "GB", "RATES", "CURRENCIES", "COMMODITIES", "CRYPTOCURRENCIES")
_SUMMARY_FIELDS = "shortName,regularMarketPrice,regularMarketChange,regularMarketChangePercent,regularMarketTime,regularMarketPreviousClose"


def _market_region(region: str) -> dict[str, Any]:
    m = yf.Market(region)
    # yf.Market.summary is keyed by exchange code, so items on the same exchange overwrite each other.
    # Call the same endpoint directly and keep the full list.
    raw = m._fetch_json(
        f"{_QUERY1_URL_}/v6/finance/quote/marketSummary",
        {"fields": _SUMMARY_FIELDS, "formatted": False, "lang": "en-US", "market": region},
    )
    items = []
    for x in raw.get("marketSummaryResponse", {}).get("result", []):
        ts = x.get("regularMarketTime")
        items.append(
            {
                "symbol": x.get("symbol"),
                "name": (x.get("shortName") or "").strip(),
                "price": x.get("regularMarketPrice"),
                "change": x.get("regularMarketChange"),
                "changePercent": x.get("regularMarketChangePercent"),
                "previousClose": x.get("regularMarketPreviousClose"),
                "marketState": x.get("marketState"),
                "as_of": datetime.fromtimestamp(ts).astimezone().isoformat() if isinstance(ts, (int, float)) else None,
            }
        )
    status = _safe(lambda: m.status, "market.status", region) or {}
    result: dict[str, Any] = {"items": _clean(items)}
    if status.get("status"):
        result["status"] = _clean({k: status.get(k) for k in ("status", "message", "open", "close")})
    return result


@tool
def get_market_summary(regions: list[str] | None = None) -> dict[str, Any]:
    """Get Yahoo Finance's market summary by region: major indices, US Treasury yields, currencies, commodities, crypto, and market open/close status.

    Args:
        regions: Any of US, ASIA, EUROPE, GB, RATES (US Treasury yields and futures), CURRENCIES, COMMODITIES,
            CRYPTOCURRENCIES. All regions when omitted.
            In RATES, ^IRX/^FVX/^TNX/^TYX are yields in %, and `change` is in percentage points (0.035 = 3.5bp).
            Japanese government bond yields are not available on Yahoo Finance.
    """
    targets = [r.upper() for r in (regions or MARKET_REGIONS)]
    bad = [r for r in targets if r not in MARKET_REGIONS]
    if bad:
        raise ValueError(f"Unknown region: {bad}. Available: {list(MARKET_REGIONS)}")
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = dict(zip(targets, ex.map(lambda r: _safe(lambda: _market_region(r), "market", r), targets)))
    return {r: v if v is not None else {"error": "Failed to fetch this region"} for r, v in results.items()}


@tool
def get_calendar(
    kind: Literal["earnings", "economic", "ipo", "splits"] = "earnings",
    start: str | None = None,
    end: str | None = None,
    limit: int = 25,
    min_market_cap: float | None = None,
    most_active_only: bool = True,
) -> dict[str, Any]:
    """Get a market calendar: earnings announcements, economic events, IPOs or stock splits.

    Args:
        kind: earnings, economic, ipo or splits
        start: Start date YYYY-MM-DD (defaults to today)
        end: End date YYYY-MM-DD (defaults to 7 days after start)
        limit: Maximum number of events (max 100)
        min_market_cap: earnings only. Minimum market cap in USD
        most_active_only: earnings only. Limit to the most actively traded companies
    """
    limit = max(1, min(limit, 100))
    cal = yf.Calendars(start=start, end=end)
    if kind == "earnings":
        df = cal.get_earnings_calendar(market_cap=min_market_cap, filter_most_active=most_active_only, limit=limit)
    elif kind == "economic":
        df = cal.get_economic_events_calendar(limit=limit)
    elif kind == "ipo":
        df = cal.get_ipo_info_calendar(limit=limit)
    else:
        df = cal.get_splits_calendar(limit=limit)
    index_name = "event" if kind == "economic" else "symbol"
    return {"kind": kind, "start": start, "end": end, "events": _records(df, index_name)}


# ---------------------------------------------------------------------------
# Real-time stream (WebSocket)
# ---------------------------------------------------------------------------

YAHOO_STREAM_URL = "wss://streamer.finance.yahoo.com/?version=2"
_MARKET_HOURS = {0: "pre", 1: "regular", 2: "post", 3: "extended"}
_LIVE_KEEP = ("price", "change", "change_percent", "day_high", "day_low", "day_volume", "open_price",
              "previous_close", "bid", "ask", "bid_size", "ask_size", "currency", "exchange", "market_cap")


@tool
async def get_live_ticks(symbols: list[str], seconds: float = 5.0) -> dict[str, Any]:
    """Subscribe to Yahoo Finance's real-time stream (WebSocket) for a few seconds and return the latest tick per symbol.

    Useful for pre/post-market prices and second-by-second crypto moves.
    Symbols outside trading hours may send no ticks; use get_quote for those.

    Args:
        symbols: List of tickers (max 20)
        seconds: How long to listen (1-20)
    """
    from websockets.asyncio.client import connect

    symbols = symbols[:20]
    seconds = max(1.0, min(seconds, 20.0))
    decoder = yf.AsyncWebSocket(verbose=False)
    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    headers = {"User-Agent": "Mozilla/5.0", "Origin": "https://finance.yahoo.com"}
    deadline = time.monotonic() + seconds
    async with connect(YAHOO_STREAM_URL, additional_headers=headers, open_timeout=10) as ws:
        await ws.send(json.dumps({"subscribe": symbols}))
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError:
                break
            msg = decoder._decode_message(json.loads(raw).get("message", ""))
            sym = msg.get("id")
            if not sym:
                continue
            counts[sym] = counts.get(sym, 0) + 1
            ts = msg.get("time")
            latest[sym] = {
                "symbol": sym,
                "time": datetime.fromtimestamp(int(ts) / 1000).astimezone().isoformat() if ts else None,
                "session": _MARKET_HOURS.get(msg.get("market_hours")),
                **{k: msg[k] for k in _LIVE_KEEP if k in msg},
            }
    return {
        "seconds": seconds,
        "ticks": [latest[s] for s in symbols if s in latest],
        "tick_counts": counts,
        "no_ticks": [s for s in symbols if s not in latest],
    }


# ---------------------------------------------------------------------------
# Authentication (OAuth 2.1 / Basic). See auth.py
# ---------------------------------------------------------------------------

_auth_config: AuthConfig | None = None


def _get_auth_config() -> AuthConfig:
    assert _auth_config is not None, "called before build_app()"
    return _auth_config


register_routes(mcp, _get_auth_config)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):  # noqa: ANN001
    return PlainTextResponse("ok")


def build_app() -> ASGIApp:
    global _auth_config
    _auth_config = AuthConfig.from_env()
    # Stateless with JSON responses: no session affinity needed across Cloud Run instances or after scaling to zero
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    return AuthMiddleware(app, _auth_config)


def main() -> None:
    uvicorn.run(
        build_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
