"""Measure data completeness per GP, per fund and per datapoint, without adding amounts across funds.

Funds report in their own currencies, so completeness is judged on whether values exist, never
on their size. A value "exists" when it is present and, for numbers, non-zero: a zero is a
reported value but carries no information (it may mean "none" or "not reported"), so it is
counted separately rather than as missing.
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from custom_re.reference import UNMAPPED, gp_map

Rules = dict[str, Any]
VALUE_COL = {"num": "value_num", "date": "value_date", "str": "value_str"}

# Fund status against the GP reference.
REPORTED, MISSING, UNLISTED = "Reported", "Missing from extract", "Not in reference"

FUND_COLUMNS = [
    "rank", "gp", "fund", "status", "currency", "datapoints_reported", "datapoints_with_value",
    "datapoints_total",
    "records", "records_with_value", "value_share", "zero_datapoints",
]
GP_COLUMNS = [
    "rank", "gp", "funds_expected", "funds_reported", "funds_missing", "funds_all_valued",
    "min_datapoints_with_value", "mean_datapoints_with_value", "datapoints_total", "records",
    "records_with_value", "value_share", "errors", "warnings", "currencies", "least_complete_fund",
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


def fund_completeness(df: pd.DataFrame, rules: Rules, reference: pd.DataFrame) -> pd.DataFrame:
    """Rank funds by datapoints with a value, then by the share of records with a value.

    Covers every fund in the extract or the GP `reference`, so an expected fund that was not reported
    ranks last with nothing reported. Returns one row per fund (FUND_COLUMNS); rank 1 is the most
    complete and ties share a rank.
    """
    cat = _catalogue(rules)
    n_total = len(cat)
    gps = gp_map(reference)
    reported = set(df["fund"].dropna())
    funds = sorted(reported | set(gps))
    if not funds:
        return pd.DataFrame(columns=FUND_COLUMNS)
    flags = _flag_values(df, rules)
    per_dp = flags.groupby(["fund", "datapoint"])[["present", "valued"]].any()
    # Every catalogued datapoint for every fund, so a datapoint absent from the extract counts as missing.
    grid = pd.MultiIndex.from_product([funds, cat["datapoint"]], names=["fund", "datapoint"])
    per_dp = per_dp.reindex(grid, fill_value=False)

    out = pd.DataFrame(index=pd.Index(funds, name="fund"))
    out["gp"] = [gps.get(f, UNMAPPED) for f in funds]
    out["status"] = [REPORTED if f in gps and f in reported else MISSING if f in gps else UNLISTED
                     for f in funds]
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
    zero_names = {
        f: ", ".join(g.index.get_level_values("datapoint"))
        for f, g in zero_only[zero_only].groupby(level="fund")
    }
    out["zero_datapoints"] = [zero_names.get(f, "") for f in funds]
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


def gp_completeness(fc: pd.DataFrame, issues: pd.DataFrame) -> pd.DataFrame:
    """Roll the fund table (from fund_completeness) up to one row per GP (GP_COLUMNS).

    Only counts are combined, never amounts, so funds in different currencies can share a GP. GPs are
    ranked by the share of their expected funds reported, then the average datapoints with a value per
    reported fund, then the share of records with a value; funds missing from the reference are grouped
    last under UNMAPPED. Error and warning counts come from issues on the GP's funds.
    """
    if fc.empty:
        return pd.DataFrame(columns=GP_COLUMNS)
    sev = issues[issues["severity"].isin(["error", "warning"]) & issues["fund"].notna()]
    fund_gp = dict(zip(fc["fund"], fc["gp"], strict=True))
    by_gp_sev = (sev.assign(gp=sev["fund"].map(fund_gp)).groupby(["gp", "severity"], observed=True).size()
                 .unstack(fill_value=0).reindex(columns=["error", "warning"], fill_value=0))
    rows = []
    # fc is ordered most complete first, so the last fund of each GP is its least complete.
    for gp, funds in fc.groupby("gp", sort=False):
        rep = funds[funds["status"] != MISSING]
        records, valued = int(rep["records"].sum()), int(rep["records_with_value"].sum())
        rows.append({
            "gp": gp,
            "funds_expected": int((funds["status"] != UNLISTED).sum()),
            "funds_reported": len(rep),
            "funds_missing": int((funds["status"] == MISSING).sum()),
            "funds_all_valued": int((rep["datapoints_with_value"] == rep["datapoints_total"]).sum()),
            "min_datapoints_with_value": int(rep["datapoints_with_value"].min()) if len(rep) else 0,
            "mean_datapoints_with_value": float(rep["datapoints_with_value"].mean()) if len(rep) else 0.0,
            "datapoints_total": int(cast(int, funds["datapoints_total"].iloc[0])),
            "records": records,
            "records_with_value": valued,
            "value_share": valued / records if records else 0.0,
            "errors": int(cast(int, by_gp_sev.at[gp, "error"])) if gp in by_gp_sev.index else 0,
            "warnings": int(cast(int, by_gp_sev.at[gp, "warning"])) if gp in by_gp_sev.index else 0,
            "currencies": ", ".join(sorted(rep["currency"].dropna().unique())),
            "least_complete_fund": funds["fund"].iloc[-1],
        })
    out = pd.DataFrame(rows)
    # Same rounding as the fund ranking, so float noise never splits a genuine tie.
    keys = pd.DataFrame({
        "_unmapped": out["gp"] == UNMAPPED,
        "_coverage": (out["funds_reported"] / out["funds_expected"].where(out["funds_expected"] > 0))
        .fillna(0.0).round(10),
        "_mean": out["mean_datapoints_with_value"].round(10),
        "_share": out["value_share"].round(10),
    })
    out = pd.concat([out, keys], axis=1).sort_values(
        ["_unmapped", "_coverage", "_mean", "_share", "gp"], ascending=[True, False, False, False, True],
        kind="stable")
    out["rank"] = (
        out[["_unmapped", "_coverage", "_mean", "_share"]]
        .assign(_unmapped=lambda k: ~k["_unmapped"])  # rank() is descending: mapped GPs first
        .apply(tuple, axis=1).rank(method="min", ascending=False).astype(int)
    )
    return out[GP_COLUMNS].reset_index(drop=True)


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
