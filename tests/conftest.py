import socket
import threading
import time
from types import SimpleNamespace

import pandas as pd
import pytest
import uvicorn

import server

CLIENT_ID = "yf-test-client"
CLIENT_SECRET = "s" * 40
TZ = "America/New_York"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _idx(*dates):
    return pd.DatetimeIndex(pd.to_datetime(list(dates)).tz_localize(TZ), name="Date")


STMT = pd.DataFrame(
    {pd.Timestamp("2025-12-31"): [100.0, float("nan")], pd.Timestamp("2024-12-31"): [80.0, 5.0]},
    index=["Total Revenue", "Other"],
)


class FakeTicker:
    """Stub for yfinance.Ticker that returns fixed values."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.fast_info = {
            "previousClose": 146.45,  # Yahoo sometimes returns a wrong value here; it must not be used
            "marketCap": float("nan"),
            "shares": 100,
            "open": 147.9,
            "fiftyDayAverage": 149.1,
            "twoHundredDayAverage": 145.8,
            "tenDayAverageVolume": 1,
            "threeMonthAverageVolume": 2,
            "yearChange": -0.1,
            "timezone": TZ,
            "quoteType": "EQUITY",
        }
        self.history_metadata = {
            "regularMarketPrice": 147.49,
            "previousClose": 144.75,
            "longName": "Fake Corp",
            "currency": "USD",
            "fullExchangeName": "NasdaqGS",
            "instrumentType": "EQUITY",
            "regularMarketTime": pd.Timestamp("2026-10-23 16:00:00", tz=TZ),
            "regularMarketVolume": 1000,
        }
        self.info = {"longName": "Fake Corp", "trailingPE": float("inf"), "companyOfficers": [{"name": "A", "title": "CEO", "extra": 1}]}
        self._hist = pd.DataFrame(
            {
                "Open": [160.56123456, 154.21],
                "High": [166.0, 155.0],
                "Low": [156.0, 143.0],
                "Close": [159.31, 144.75],
                "Adj Close": [159.31, 144.75],
                "Volume": [220785200, 197046500],
                "Dividends": [0.0, 0.0],
                "Stock Splits": [0.0, 0.0],
            },
            index=_idx("2026-10-21", "2026-10-22"),
        )
        self.dividends = pd.Series([0.25, 0.26], index=_idx("2026-05-10", "2026-08-10"))
        self.splits = pd.Series([], dtype=float, index=pd.DatetimeIndex([], name="Date"))
        self.capital_gains = pd.Series([], dtype=float, index=pd.DatetimeIndex([], name="Date"))
        self.analyst_price_targets = {"current": 147.49, "mean": 257.49}
        self.recommendations = pd.DataFrame({"period": ["0m"], "buy": [23]})
        self.upgrades_downgrades = pd.DataFrame({"Firm": ["X"]}, index=pd.Index([pd.Timestamp("2026-10-20")], name="GradeDate"))
        self.earnings_estimate = pd.DataFrame({"avg": [1.0]}, index=pd.Index(["0q"], name="period"))
        self.revenue_estimate = self.eps_trend = self.eps_revisions = self.earnings_estimate
        self.calendar = {"Earnings Date": [pd.Timestamp("2026-11-03").date()]}
        self.earnings_history = pd.DataFrame({"epsActual": [1.85]}, index=pd.Index(["2026-06-30"], name="quarter"))
        self.major_holders = pd.DataFrame({"Value": [0.01, 0.66]}, index=pd.Index(["insidersPercentHeld", "institutionsPercentHeld"], name="Breakdown"))
        self.institutional_holders = pd.DataFrame({"Holder": ["Blackrock"], "pctHeld": [0.08]})
        self.mutualfund_holders = self.institutional_holders
        self.insider_transactions = pd.DataFrame({"Insider": [f"P{i}" for i in range(50)]})
        self.insider_purchases = pd.DataFrame({"Trans": [1]})
        self.insider_roster_holders = pd.DataFrame({"Name": ["A"]})
        self.sec_filings = [{"date": pd.Timestamp("2026-09-01").date(), "type": "10-K", "title": "Annual", "edgarUrl": "u", "exhibits": {}}]
        self.options = ("2026-10-30", "2026-11-06")
        self.funds_data = SimpleNamespace(
            description="desc",
            fund_overview={"family": "F"},
            fund_operations=pd.DataFrame({"X": [0.001]}, index=pd.Index(["Expense Ratio"], name="Attributes")),
            asset_classes={"stockPosition": 1.0},
            top_holdings=pd.DataFrame({"Name": ["NVIDIA"]}, index=pd.Index(["NVDA"], name="Symbol")),
            sector_weightings={"technology": 0.4},
            equity_holdings=pd.DataFrame(),
            bond_holdings=pd.DataFrame(),
            bond_ratings={},
        )

    @property
    def growth_estimates(self):
        raise KeyError("not available")

    def history(self, **kwargs):
        self.history_kwargs = kwargs
        return self._hist.copy()

    def get_income_stmt(self, freq="yearly"):
        return STMT

    get_balance_sheet = get_cash_flow = get_income_stmt

    def get_valuation_measures(self, freq="quarterly", periods=5):
        return pd.DataFrame({"Current": [1.0e12], "6/30/2026": [0.9e12]}, index=["Market Cap"])

    def get_earnings_dates(self, limit=12):
        return pd.DataFrame({"EPS Estimate": [1.98]}, index=pd.DatetimeIndex([pd.Timestamp("2026-11-02 16:00", tz=TZ)], name="Earnings Date"))

    def get_shares_full(self, start=None, end=None):
        idx = pd.DatetimeIndex(pd.to_datetime(["2026-01-05", "2026-01-20", "2026-02-03", "2026-03-03"]).tz_localize(TZ))
        return pd.Series([100, 101, 101, 99], index=idx)

    def get_news(self, count=10):
        return []

    def option_chain(self, exp):
        strikes = [float(s) for s in range(100, 200, 5)]
        df = pd.DataFrame(
            {"contractSymbol": [f"C{s}" for s in strikes], "strike": strikes, "lastPrice": 1.0,
             "contractSize": "REGULAR", "currency": "USD"}
        )
        return SimpleNamespace(calls=df, puts=df.copy(), underlying={"regularMarketPrice": 147.49})


class NotFoundTicker(FakeTicker):
    def __init__(self, symbol):
        super().__init__(symbol)
        self.history_metadata = {}
        self._hist = self._hist.iloc[0:0]
        self.info = {"trailingPegRatio": None}


def fake_ticker(symbol):
    return NotFoundTicker(symbol) if symbol == "ZZZZ" else FakeTicker(symbol)


class FakeSearch:
    def __init__(self, query, max_results=8, news_count=8, include_research=False, **_):
        self.quotes = [{"symbol": "SPCX", "shortname": "Space Exploration Technologies", "exchange": "NMS"}][:max_results]
        self.news = [{"title": "SpaceX news", "publisher": "Stocktwits", "link": "https://example.com", "providerPublishTime": 1792794957}][:news_count]
        self.research = [{"reportHeadline": "R"}]


class FakeLookup:
    def __init__(self, query):
        self.query = query

    def get_index(self, count=25):
        return pd.DataFrame({"shortName": ["Nikkei 225"], "regularMarketPrice": [62693.16], "rank": [1]}, index=pd.Index(["^N225"], name="symbol"))


def fake_download(symbols, **kw):
    idx = pd.DatetimeIndex(pd.to_datetime(["2026-10-21", "2026-10-22"]), name="Date")
    cols = pd.MultiIndex.from_product([["Close", "Volume"], symbols], names=["Price", "Ticker"])
    return pd.DataFrame([[1.0] * len(cols), [2.123456] * len(cols)], index=idx, columns=cols)


def fake_screen(query, **kw):
    fake_screen.last = (query, kw)
    return {"total": 2, "quotes": [{"symbol": "8306.T", "shortName": "MUFG", "marketCap": 3.7e13, "ignored": 1}]}


class FakeMarket:
    def __init__(self, region):
        self.region = region

    def _fetch_json(self, url, params):
        return {"marketSummaryResponse": {"result": [
            {"symbol": "^TNX", "shortName": "10-Yr Bond", "exchange": "CGI", "regularMarketPrice": 4.988, "regularMarketChange": 0.035, "regularMarketTime": 1792785596},
            {"symbol": "^TYX", "shortName": "30-Yr Bond", "exchange": "CGI", "regularMarketPrice": 5.315},
        ]}}

    @property
    def status(self):
        return {"status": "closed", "message": "closed", "open": pd.Timestamp("2026-10-26 13:30", tz="UTC")}


class FakeSector:
    def __init__(self, key, region="US"):
        self.key, self.region = key, region
        self.name, self.symbol = "Technology", "^YH311"
        self.overview = {"companies_count": 866}
        self.top_companies = pd.DataFrame({"name": [f"C{i}" for i in range(40)]}, index=pd.Index([f"S{i}" for i in range(40)], name="symbol"))
        self.top_etfs = {"QQQ": "Invesco"}
        self.top_mutual_funds = {}
        self.industries = pd.DataFrame({"name": ["Semiconductors"]}, index=pd.Index(["semiconductors"], name="key"))
        self.research_reports = []
        self.sector_key = "technology"
        self.top_growth_companies = self.top_performing_companies = self.top_companies.head(5)


class FakeCalendars:
    def __init__(self, start=None, end=None):
        self.args = (start, end)

    def get_earnings_calendar(self, market_cap=None, filter_most_active=True, limit=12):
        return pd.DataFrame({"Company": ["Amazon"], "Event Start Date": [pd.Timestamp("2026-10-29 20:00", tz="UTC")]}, index=pd.Index(["AMZN"], name="Symbol"))

    def get_economic_events_calendar(self, limit=12):
        return pd.DataFrame({"Region": ["US"]}, index=pd.Index(["CPI"], name="Event"))

    get_ipo_info_calendar = get_splits_calendar = get_earnings_calendar


@pytest.fixture
def fake_yf(monkeypatch):
    for name, fake in {
        "Ticker": fake_ticker, "Search": FakeSearch, "Lookup": FakeLookup, "download": fake_download,
        "screen": fake_screen, "Market": FakeMarket, "Sector": FakeSector, "Industry": FakeSector, "Calendars": FakeCalendars,
    }.items():
        monkeypatch.setattr(server.yf, name, fake)


@pytest.fixture
def live_server(monkeypatch):
    """Start the authenticated HTTP server in a background thread and return its base URL."""
    monkeypatch.setenv("OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("OAUTH_SIGNING_KEY", raising=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(server.build_app(), host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)
