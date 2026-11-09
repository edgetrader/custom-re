"""Classify each fund's answer to each datapoint, without looking at amounts.

Funds report in their own currencies, so completeness is judged on whether values exist, never on their
size. For one fund and datapoint the state is: has a value, reported only as zero, a value in only some
records, no value (rows that are blank, null or NA), or not reported (no rows). Datapoints in the rules'
`none_when_missing` (e.g. Bad debts) mean "none" when not reported, empty or zero.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

Rules = dict[str, Any]
VALUE_COL = {"num": "value_num", "date": "value_date", "str": "value_str"}

# State of one datapoint for one fund.
VALUED, NONE = "Has a value", "None"
ZERO_ONLY, NO_VALUE, NOT_REPORTED = "Reported only as zero", "No value (blank, null or NA)", "Not reported"
PARTIAL = "No value in some records"  # e.g. a series with some periods blank


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

    State is VALUED, NONE (a "none when missing" datapoint with no positive value), PARTIAL, ZERO_ONLY,
    NO_VALUE or NOT_REPORTED.
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
