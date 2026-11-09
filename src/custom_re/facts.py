"""Fund-level facts shared by the report content and the usability stage (dates, flags, derived checks)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Datapoints whose meaning drives several findings and caveats.
CALLS, DISTS, DEBT = "Capital call amount", "Distribution amount", "Debt maturity value"
GAV, LTV, LTV_CAP = "Fund GAV", "Fund LTV (current)", "Fund LTV Cap"
IRR, IRR_TARGET = "Fund Since Inception Net IRR", "Fund Target Net IRR"
END_TERM, END_IP = "End of fund term", "End of investment period"
COUNTRY, REGION_L1, REGION_L2 = "Country", "Region Level 1", "Region Level 2"
SECTOR_PCT_L2 = "Portfolio sector percentage (by GAV) - total portfolio"
SECTOR_PCT_L1 = "Portfolio sector percentage (by GAV) - total portfolio Sector Level 1"
OCC_L1 = "Average occupancy rate (%) - completed assets Sector Level 1"
OCC_L2 = "Average occupancy rate (%) - completed assets Sector Level 2"
COMPLETED = "Completed assets"


def col(w: pd.DataFrame, name: str) -> pd.Series:
    """Column of the fund frame, or all-NaN if the datapoint is absent."""
    return w[name] if name in w else pd.Series(np.nan, index=w.index)


def date_col(w: pd.DataFrame, name: str) -> pd.Series:
    """Date column of the fund frame; all-NaT if absent, so comparisons with a Timestamp never fail."""
    return pd.to_datetime(col(w, name), errors="coerce")


def any_positive(df: pd.DataFrame, dp: str) -> pd.Series:
    """Per fund: does any period of series `dp` have a positive amount."""
    s = df[df["datapoint"] == dp].groupby("fund")["value_num"].apply(lambda v: bool((v > 0).any()))
    return s.astype(bool)


def exact_debt(df: pd.DataFrame, w: pd.DataFrame, cfg: dict[str, Any]) -> bool:
    """Return True if every fund's debt maturities total LTV × GAV to within a tiny relative tolerance."""
    debt = df[df["datapoint"] == DEBT].groupby("fund")["value_num"].sum()
    expected = (col(w, LTV).astype(float) * col(w, GAV).astype(float)).reindex(debt.index)
    # The claim is about every fund, so a fund without a schedule or inputs makes it false.
    if debt.empty or len(debt) < len(w) or expected.isna().any() or (expected == 0).any():
        return False
    return bool((((debt - expected) / expected).abs() <= cfg["exact_rel_tol"]).all())


def anonymised_countries(df: pd.DataFrame, pattern: str) -> bool:
    """Return True if every country label matches the anonymised-label pattern from the rules."""
    countries = df.loc[df["datapoint"] == COUNTRY, "value_str"].dropna()
    return bool(pattern and len(countries) and countries.str.match(pattern).all())
