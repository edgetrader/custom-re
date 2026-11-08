"""Shared fixtures: a small, fully valid synthetic extract in the raw KPI sheet format."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from custom_re.loader import load_rules

ROOT = Path(__file__).resolve().parents[1]
REPORT = pd.Timestamp("2026-06-30")

FUND_INFO_NUM = {
    "AIA Effective Interest in Fund": 0.25,
    "Assets under development": 0.2,
    "Bad debts": 100_000.0,
    "Base fee incurred to-date": 5_000_000.0,
    "Carried interest incurred to-date": 0.0,
    "Completed assets": 0.8,
    "Fund GAV": 100_000_000.0,
    "Fund LTV (current)": 0.3,
    "Fund LTV Cap": 0.5,
    "Fund NAV": 65_000_000.0,
    "Fund Since Inception Net IRR": 0.08,
    "Fund Target Net IRR": 0.10,
    "Portfolio average occupancy rate": 0.90,
    "Proportion of fixed rate debt": 0.6,
    "Weighted average cost of debt": 0.04,
}
FUND_INFO_DATE = {
    "Fund inception date": "2018-01-01",
    "Start of fund term": "2018-03-01",
    "Start of investment period": "2018-03-01",
    "End of investment period": "2021-03-01",
    "End of fund term": "2028-03-01",
}
GAV_COMP = {"Cash & Near Cash": 0.05, "Others": 0.02, "Real Estate Investments (Immovable Property)": 0.93}
# country -> (sub-region, continent, share)
COUNTRIES = {
    "Country 01": ("Western Europe", "Europe", 0.6),
    "Country 02": ("East Asia", "Asia Pacific", 0.4),
}
# sector L2 -> (L1, share, occupancy)
SECTORS = {
    "Office": ("Office", 0.5, 0.92),
    "Logistics": ("Industrial", 0.3, 0.95),
    "Data Centre": ("Industrial", 0.2, 0.80),
}


def _row(fund: str, cat: str, dp: str, **kw: Any) -> dict[str, Any]:
    """One raw-format row with the export's padding columns."""
    return {
        " ": None, "Region.Region": None, "Company": fund,
        "Datapoint.Category.Category": f"CustomRE\\{cat}", "Datapoint.Name": dp,
        "Date": kw.get("date", REPORT), "Period": None, "Reporting Date": REPORT,
        "Reporting Period": None, "Scenario": None, "Version": None, "Currency": "GBP",
        "Nature": None, "Analysis1": kw.get("a1"), "Analysis2": kw.get("a2"),
        "Analysis3": kw.get("a3"), "Analysis4": None, "Value Num": kw.get("num"),
        "Value Date": kw.get("vdate"), "Value String": kw.get("s"), "Value Memo": None,
    }


def build_fund(fund: str, monthly: bool = True) -> list[dict[str, Any]]:
    """All rows for one internally consistent fund."""
    rows = [_row(fund, "Fund Info", k, num=v) for k, v in FUND_INFO_NUM.items()]
    rows += [_row(fund, "Fund Info", k, vdate=pd.Timestamp(v)) for k, v in FUND_INFO_DATE.items()]
    rows += [_row(fund, "GAV Composition", k, num=v) for k, v in GAV_COMP.items()]

    for c, (sub, cont, pct) in COUNTRIES.items():
        rows += [
            _row(fund, "Exposure - Geography", "Country", a1=c, s=c),
            _row(fund, "Exposure - Geography", "Country Percentage", a1=c, a2=sub, a3=cont, num=pct),
            _row(fund, "Exposure - Geography", "Region Level 1", a1=c, s=sub),
            _row(fund, "Exposure - Geography", "Region Level 2", a1=c, s=cont),
        ]
    l1_pct: dict[str, float] = {}
    l1_occ: dict[str, list[tuple[float, float]]] = {}
    for s2, (s1, pct, occ) in SECTORS.items():
        l1_pct[s1] = l1_pct.get(s1, 0) + pct
        l1_occ.setdefault(s1, []).append((occ, pct))
        rows += [
            _row(fund, "Exposure - Sector", "Sector - level 2", a1=s2, s=s2),
            _row(fund, "Exposure - Sector", "Portfolio sector percentage (by GAV) - total portfolio",
                 a1=s2, a2=s1, num=pct),
            _row(fund, "Exposure - Sector", "Average occupancy rate (%) - completed assets Sector Level 2",
                 a1=s2, a2=s1, num=occ),
        ]
    for s1, pct in l1_pct.items():
        occ = sum(o * w for o, w in l1_occ[s1]) / sum(w for _, w in l1_occ[s1])
        rows += [
            _row(fund, "Exposure - Sector", "Sector - level 1", a1=s1, s=s1),
            _row(fund, "Exposure - Sector",
                 "Portfolio sector percentage (by GAV) - total portfolio Sector Level 1", a1=s1, num=pct),
            _row(fund, "Exposure - Sector", "Average occupancy rate (%) - completed assets Sector Level 1",
                 a1=s1, num=occ),
        ]

    # Debt maturities total exactly LTV x GAV.
    debt = FUND_INFO_NUM["Fund LTV (current)"] * FUND_INFO_NUM["Fund GAV"]
    for i, d in enumerate(pd.date_range("2026-12-31", periods=10, freq="YE")):
        rows.append(_row(fund, "Debt Maturity", "Debt maturity value", date=d, num=debt if i == 0 else 0.0))

    cal = (pd.date_range("2026-08-31", periods=18, freq="ME") if monthly
           else pd.date_range("2026-09-30", periods=6, freq="QE"))
    for d in cal:
        rows.append(_row(fund, "Projection", "Capital call amount", date=d, num=1000.0))
        rows.append(_row(fund, "Projection", "Distribution amount", date=d, num=500.0))
    return rows


@pytest.fixture
def rules() -> dict[str, Any]:
    """The project's real DQ rules, sized for the two-fund fixture."""
    rules = load_rules(ROOT / "config" / "dq_rules.yaml")
    rules["expected_fund_count"] = 2
    return rules


@pytest.fixture
def raw() -> pd.DataFrame:
    """Valid raw extract: one monthly-projection fund and one quarterly."""
    rows = build_fund("Fund A", monthly=True) + build_fund("Fund B", monthly=False)
    return pd.DataFrame(rows)
