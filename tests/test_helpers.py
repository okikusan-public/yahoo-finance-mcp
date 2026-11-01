import numpy as np
import pandas as pd

import server


def test_clean_converts_nan_inf_nat_numpy_and_timestamps():
    out = server._clean(
        {
            "a": float("nan"),
            "b": float("inf"),
            "c": np.int64(3),
            "d": pd.Timestamp("2026-10-23"),
            "e": pd.Timestamp("2026-10-23 16:00", tz="America/New_York"),
            "f": pd.NaT,
            pd.Timestamp("2025-12-31"): [np.float64(1.5)],
        }
    )
    assert out == {
        "a": None,
        "b": None,
        "c": 3,
        "d": "2026-10-23",
        "e": "2026-10-23T16:00:00-04:00",
        "f": None,
        "2025-12-31": [1.5],
    }


def test_records_keeps_meaningful_index_and_drops_range_index():
    assert server._records(None) == [] and server._records(pd.DataFrame()) == []
    assert server._records(pd.DataFrame({"x": [1]})) == [{"x": 1}]
    df = pd.DataFrame({"x": [1, 2]}, index=pd.Index(["a", "b"], name="k"))
    assert server._records(df, limit=1) == [{"k": "a", "x": 1}]


def test_statement_drops_nan():
    df = pd.DataFrame({pd.Timestamp("2025-12-31"): [1.0, float("nan")]}, index=["Revenue", "X"])
    assert server._statement(df) == {"2025-12-31": {"Revenue": 1.0}}


def test_series_records_newest_first():
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-01-01", "2026-02-01"]))
    assert server._series_records(s, "v", limit=1) == [{"date": "2026-02-01", "v": 2.0}]
