"""Measure data completeness per fund and per datapoint, without adding amounts across funds.

Funds report in their own currencies, so completeness is judged on whether values exist, never
on their size. A value "exists" when it is present and, for numbers, non-zero: a zero is a
reported value but carries no information (it may mean "none" or "not reported"), so it is
counted separately rather than as missing.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

Rules = dict[str, Any]
VALUE_COL = {"num": "value_num", "date": "value_date", "str": "value_str"}

FUND_COLUMNS = [
    "rank", "fund", "currency", "datapoints_reported", "datapoints_with_value", "datapoints_total",
    "records", "records_with_value", "value_share", "zero_datapoints",
]
DATAPOINT_COLUMNS = [
    "category", "datapoint", "funds_reporting", "funds_with_value", "funds_total",
    "records", "records_with_value", "value_share",
]


def _catalogue(rules: Rules) -> pd.DataFrame:
    """One row per catalogued datapoint with its category and value kind."""
    return pd.DataFrame(
        [(cat, dp, kind) for cat, dps in rules["datapoints"].items() for dp, kind in dps.items()],
        columns=["category", "datapoint", "kind"],
    )


def _flag_values(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Catalogued records with `present` (field filled) and `valued` (filled and, if numeric, non-zero)."""
    cat = _catalogue(rules)
    sub = df.merge(cat[["datapoint", "kind"]], on="datapoint", how="inner")
    # A value counts only in the field its kind expects; one filled in the wrong field is a
    # value_field error in the checks and is not "present" here. Strings are already blank-stripped
    # by the loader, so non-null means a real label.
    present = pd.Series(False, index=sub.index)
    for kind, col in VALUE_COL.items():
        is_kind = sub["kind"] == kind
        present |= is_kind & sub[col].notna()
    numeric = sub["kind"] == "num"
    zero = numeric & present & (sub["value_num"] == 0)
    return sub.assign(present=present, valued=present & ~zero)


def fund_completeness(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Rank funds by datapoints with a value, then by the share of records with a value.

    Returns one row per fund (FUND_COLUMNS); rank 1 is the most complete. Ties share a rank.
    """
    cat = _catalogue(rules)
    n_total = len(cat)
    funds = sorted(df["fund"].dropna().unique())
    if not funds:
        return pd.DataFrame(columns=FUND_COLUMNS)
    flags = _flag_values(df, rules)
    per_dp = flags.groupby(["fund", "datapoint"])[["present", "valued"]].any()
    # Every catalogued datapoint for every fund, so a datapoint absent from the extract counts as missing.
    grid = pd.MultiIndex.from_product([funds, cat["datapoint"]], names=["fund", "datapoint"])
    per_dp = per_dp.reindex(grid, fill_value=False)

    out = pd.DataFrame(index=pd.Index(funds, name="fund"))
    out["currency"] = df.groupby("fund")["currency"].first().reindex(funds)
    out["datapoints_reported"] = per_dp["present"].groupby(level="fund").sum()
    out["datapoints_with_value"] = per_dp["valued"].groupby(level="fund").sum()
    out["datapoints_total"] = n_total
    recs = flags.groupby("fund")[["present", "valued"]].sum().reindex(funds, fill_value=0)
    out["records"] = recs["present"].astype(int)
    out["records_with_value"] = recs["valued"].astype(int)
    out["value_share"] = (out["records_with_value"] / out["records"].where(out["records"] > 0)).fillna(0.0)
    # Datapoints reported for the fund but only ever as zero: the reason a fund ranks lower.
    zero_only = per_dp["present"] & ~per_dp["valued"]
    out["zero_datapoints"] = [
        ", ".join(zero_only.loc[f][zero_only.loc[f]].index) for f in funds
    ]
    # Rank and order use the same two keys; rounding stops float noise splitting genuine ties, and
    # the fund name gives a fixed order within a tie.
    share = out["value_share"].round(10)
    out = out.assign(_share=share).reset_index().sort_values(
        ["datapoints_with_value", "_share", "fund"], ascending=[False, False, True], kind="stable")
    out["rank"] = (
        out[["datapoints_with_value", "_share"]].apply(tuple, axis=1).rank(method="min", ascending=False)
        .astype(int)
    )
    return out[FUND_COLUMNS].reset_index(drop=True)


def datapoint_completeness(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Per datapoint: funds reporting it, funds with a non-zero value, and the share of valued records.

    Sorted most complete first (DATAPOINT_COLUMNS).
    """
    cat = _catalogue(rules)
    n_funds = df["fund"].nunique()
    flags = _flag_values(df, rules)
    by_fund = (
        flags.groupby(["datapoint", "fund"])[["present", "valued"]].any().groupby(level="datapoint").sum()
    )
    recs = flags.groupby("datapoint")[["present", "valued"]].sum()
    out = cat.set_index("datapoint")[["category"]]
    out["funds_reporting"] = by_fund["present"].reindex(out.index, fill_value=0).astype(int)
    out["funds_with_value"] = by_fund["valued"].reindex(out.index, fill_value=0).astype(int)
    out["funds_total"] = n_funds
    out["records"] = recs["present"].reindex(out.index, fill_value=0).astype(int)
    out["records_with_value"] = recs["valued"].reindex(out.index, fill_value=0).astype(int)
    out["value_share"] = (out["records_with_value"] / out["records"].where(out["records"] > 0)).fillna(0.0)
    out = out.reset_index().sort_values(
        ["funds_with_value", "value_share", "funds_reporting", "datapoint"],
        ascending=[False, False, False, True], kind="stable")
    return out[DATAPOINT_COLUMNS].reset_index(drop=True)
