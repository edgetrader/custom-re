"""Measure data completeness per GP, per fund and per datapoint, without adding amounts across funds.

Funds report in their own currencies, so completeness is judged on whether values exist, never
on their size. A value "exists" when it is present and, for numbers, non-zero: a zero is a
reported value but carries no information (it may mean "none" or "not reported"), so it is
counted separately rather than as missing.

Funds are not expected to report every datapoint. For each fund and datapoint the gap, if any, is
one of: not reported (no rows), no value (rows that are blank, null or NA) or reported only as zero.
Datapoints in the rules' `none_when_missing` (e.g. Bad debts) mean "none" when not reported, empty
or zero, so for them that is an answer, not a gap.
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from custom_re.reference import UNMAPPED, add_gp, gp_map

Rules = dict[str, Any]
VALUE_COL = {"num": "value_num", "date": "value_date", "str": "value_str"}

# Fund status against the GP reference.
REPORTED, MISSING, UNLISTED = "Reported", "Missing from extract", "Not in reference"

# State of one datapoint for one fund, worst gap last.
VALUED, NONE = "Has a value", "None"
ZERO_ONLY, NO_VALUE, NOT_REPORTED = "Reported only as zero", "No value (blank, null or NA)", "Not reported"
PARTIAL = "No value in some records"  # e.g. a series with some periods blank
GAPS = [NOT_REPORTED, NO_VALUE, PARTIAL, ZERO_ONLY]

FUND_COLUMNS = [
    "rank", "gp", "fund", "status", "currency", "datapoints_reported", "datapoints_with_value",
    "datapoints_total", "records", "records_with_value", "value_share", "missing_datapoints",
    "zero_datapoints",
]
GP_COLUMNS = [
    "rank", "gp", "funds_expected", "funds_reported", "funds_missing", "funds_tier_1", "funds_tier_2",
    "funds_tier_3", "funds_tier_4", "funds_analysable", "analysable_share", "funds_all_valued",
    "min_datapoints_with_value", "mean_datapoints_with_value", "datapoints_total", "records",
    "records_with_value", "value_share", "errors", "warnings", "currencies", "least_complete_fund",
]
DATAPOINT_COLUMNS = [
    "category", "datapoint", "funds_reporting", "funds_with_value", "funds_total", "funds_not_reported",
    "funds_no_value", "funds_partial", "funds_zero_only", "funds_none", "records", "records_with_value",
    "value_share",
]
GAP_COLUMNS = ["category", "datapoint", "gap", "gp", "fund", "note"]


def _catalogue(rules: Rules) -> pd.DataFrame:
    """One row per catalogued datapoint with its category and value kind."""
    return pd.DataFrame(
        [(cat, dp, kind) for cat, dps in rules["datapoints"].items() for dp, kind in dps.items()],
        columns=["category", "datapoint", "kind"],
    )


def _flag_values(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Catalogued records with `present` (field filled) and `valued` (filled and, if numeric, non-zero).

    For "none when missing" datapoints a zero is a full answer (none), so it counts as valued. `positive`
    is the literal non-zero value and `blank` marks a record with nothing filled at all.
    """
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
    none = sub["datapoint"].isin(list(rules["none_when_missing"]))
    # Text that is not a number is an error in the checks, not a blank.
    blank = sub[list(VALUE_COL.values())].isna().all(axis=1) & sub["value_num_text"].isna()
    return sub.assign(present=present, valued=present & (~zero | none), positive=present & ~zero, blank=blank)


def datapoint_states(df: pd.DataFrame, rules: Rules, funds: list[str]) -> pd.DataFrame:
    """One row per (fund, datapoint) for `funds` × the catalogue: present, valued and state.

    State is VALUED, NONE (a "none when missing" datapoint with no positive value) or a gap from GAPS.
    `valued` is True for VALUED, PARTIAL and NONE: the fund answers the datapoint. Funds absent from the
    extract get NOT_REPORTED everywhere; callers treat them separately.
    """
    cat = _catalogue(rules)
    flags = _flag_values(df, rules)
    per_dp = flags.groupby(["fund", "datapoint"])[["present", "valued", "positive", "blank"]].any()
    per_dp["rows"] = True
    grid = pd.MultiIndex.from_product([funds, cat["datapoint"]], names=["fund", "datapoint"])
    per_dp = per_dp.reindex(grid, fill_value=False)
    in_extract = per_dp.index.get_level_values("fund").isin(set(df["fund"].dropna()))
    none = per_dp.index.get_level_values("datapoint").isin(list(rules["none_when_missing"])) & in_extract
    state = pd.Series(VALUED, index=per_dp.index)
    state[per_dp["valued"] & per_dp["blank"]] = PARTIAL
    state[~per_dp["valued"]] = ZERO_ONLY
    state[~per_dp["present"]] = NO_VALUE
    state[~per_dp["rows"]] = NOT_REPORTED
    # Not reported, blank or zero all mean none here, which answers the datapoint.
    is_none = none & ~per_dp["positive"]
    state[is_none] = NONE
    state[none & per_dp["positive"]] = VALUED
    per_dp.loc[is_none, "valued"] = True
    return per_dp.assign(state=state)


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
    # Every catalogued datapoint for every fund, so a datapoint absent from the extract counts as missing.
    per_dp = datapoint_states(df, rules, funds)

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
    # Why a fund ranks lower: datapoints missing (not reported or no value) and those only ever zero.
    def names(mask: pd.Series) -> list[str]:
        by_fund = {f: ", ".join(g.index.get_level_values("datapoint"))
                   for f, g in mask[mask].groupby(level="fund")}
        return [by_fund.get(f, "") for f in funds]

    in_extract = out.index.isin(list(reported))
    out["missing_datapoints"] = names(per_dp["state"].isin([NOT_REPORTED, NO_VALUE, PARTIAL]))
    out.loc[~in_extract, "missing_datapoints"] = ""  # the whole fund is missing, shown by its status
    out["zero_datapoints"] = names(per_dp["state"] == ZERO_ONLY)
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
    """Roll the tiered fund table (fund_completeness, then stages.add_tiers) up to one row per GP.

    Only counts are combined, never amounts, so funds in different currencies can share a GP. Each GP
    gets its funds per completeness tier. GPs are ranked by the share of their funds that are analysable
    (tiers covered by stage 2), then the share of expected funds reported, then the average datapoints
    with a value, then the share of records with a value; funds missing from the reference are grouped
    last under UNMAPPED. Error and warning counts come from issues on the GP's funds. Returns GP_COLUMNS.
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
            **{f"funds_tier_{t}": int((funds["tier"] == t).sum()) for t in (1, 2, 3, 4)},
            "funds_analysable": int(funds["analysable"].sum()),
            # Missing funds count against the GP: the share is of all its funds, expected or not.
            "analysable_share": float(funds["analysable"].mean()),
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
        "_analysable": out["analysable_share"].round(10),
        # On equal shares, the GP with more analysable funds ranks first (2 of 2 above 1 of 1).
        "_n_analysable": out["funds_analysable"],
        "_coverage": (out["funds_reported"] / out["funds_expected"].where(out["funds_expected"] > 0))
        .fillna(0.0).round(10),
        "_mean": out["mean_datapoints_with_value"].round(10),
        "_share": out["value_share"].round(10),
    })
    out = pd.concat([out, keys], axis=1).sort_values(
        ["_unmapped", "_analysable", "_n_analysable", "_coverage", "_mean", "_share", "gp"],
        ascending=[True, False, False, False, False, False, True], kind="stable")
    out["rank"] = (
        out[["_unmapped", "_analysable", "_n_analysable", "_coverage", "_mean", "_share"]]
        .assign(_unmapped=lambda k: ~k["_unmapped"])  # rank() is descending: mapped GPs first
        .apply(tuple, axis=1).rank(method="min", ascending=False).astype(int)
    )
    return out[GP_COLUMNS].reset_index(drop=True)


def datapoint_completeness(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Per datapoint: funds reporting it, funds with a value, each kind of gap, and valued records.

    Covers the funds in the extract. "With value" includes funds whose "none when missing" datapoint
    means none (`funds_none`). Sorted most complete first (DATAPOINT_COLUMNS).
    """
    cat = _catalogue(rules)
    funds = sorted(df["fund"].dropna().unique())
    states = datapoint_states(df, rules, funds).reset_index()
    by_dp = states.groupby("datapoint").agg(
        funds_reporting=("present", "sum"), funds_with_value=("valued", "sum"))
    counts = states.groupby(["datapoint", "state"]).size().unstack(fill_value=0)
    flags = _flag_values(df, rules)
    recs = flags.groupby("datapoint")[["present", "valued"]].sum()

    out = cat.set_index("datapoint")[["category"]]
    out["funds_reporting"] = by_dp["funds_reporting"].reindex(out.index, fill_value=0).astype(int)
    out["funds_with_value"] = by_dp["funds_with_value"].reindex(out.index, fill_value=0).astype(int)
    out["funds_total"] = len(funds)
    for col, state in [("funds_not_reported", NOT_REPORTED), ("funds_no_value", NO_VALUE),
                       ("funds_partial", PARTIAL), ("funds_zero_only", ZERO_ONLY), ("funds_none", NONE)]:
        out[col] = (counts[state] if state in counts else pd.Series(dtype=int)).reindex(
            out.index, fill_value=0).astype(int)
    out["records"] = recs["present"].reindex(out.index, fill_value=0).astype(int)
    out["records_with_value"] = recs["valued"].reindex(out.index, fill_value=0).astype(int)
    out["value_share"] = (out["records_with_value"] / out["records"].where(out["records"] > 0)).fillna(0.0)
    out = out.reset_index().sort_values(
        ["funds_with_value", "value_share", "funds_reporting", "datapoint"],
        ascending=[False, False, False, True], kind="stable")
    return out[DATAPOINT_COLUMNS].reset_index(drop=True)


def datapoint_gaps(df: pd.DataFrame, rules: Rules, reference: pd.DataFrame) -> pd.DataFrame:
    """Every fund missing each datapoint or its value, one row per datapoint and fund (GAP_COLUMNS).

    Gaps are not reported, no value, no value in some records, or reported only as zero. Funds whose
    "none when missing" datapoint means none are listed too (gap NONE), with the rules'
    description as the note, so the assumption stays visible. Covers funds in the extract,
    in catalogue order then GP and fund.
    """
    cat = _catalogue(rules)
    funds = sorted(df["fund"].dropna().unique())
    states = datapoint_states(df, rules, funds).reset_index()
    rows = states[states["state"] != VALUED].rename(columns={"state": "gap"})
    rows = rows.merge(cat[["category", "datapoint"]], on="datapoint")
    rows["note"] = [
        rules["none_when_missing"][d] if g == NONE else ""
        for d, g in zip(rows["datapoint"], rows["gap"], strict=True)
    ]
    rows = add_gp(rows, reference)
    order = {d: i for i, d in enumerate(cat["datapoint"])}
    gap_order = {g: i for i, g in enumerate([*GAPS, NONE])}
    rows = rows.assign(_d=rows["datapoint"].map(order), _g=rows["gap"].map(gap_order)).sort_values(
        ["_d", "_g", "gp", "fund"], kind="stable")
    return rows[GAP_COLUMNS].reset_index(drop=True)
