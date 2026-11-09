"""Data quality checks for the tidy Custom RE KPI frame.

Each check function returns zero or more issue rows (columns in ISSUE_COLUMNS).
Every issue names a check from CHECK_GROUPS, the single registry of check names.
`run_all` runs every check, isolating crashes so one failure never stops the report.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import Any

import numpy as np
import pandas as pd

from custom_re.loader import KEY, RENAME, fund_info_wide
from custom_re.reference import gp_map

ISSUE_COLUMNS = ["group", "check", "severity", "fund", "datapoint", "excel_row", "detail"]
SEVERITIES = ["error", "warning", "info"]

Rules = dict[str, Any]

# Every check name -> group. Issues take their group from here, and the summary
# uses it to list checks that passed with zero issues.
CHECK_GROUPS = {
    "check_crashed": "structure",
    "unused_column_populated": "structure",
    "reporting_date": "structure",
    "blank_key": "structure",
    "fund_count": "structure",
    "unknown_datapoint": "structure",
    "missing_datapoint": "structure",
    "datapoint_not_reported": "completeness",
    "value_missing": "completeness",
    "fund_level_count": "completeness",
    "series_date_missing": "completeness",
    "series_gap": "completeness",
    "projection_frequency_mix": "completeness",
    "missing_dimension": "completeness",
    "exposure_members_mismatch": "completeness",
    "occupancy_unknown_sector": "completeness",
    "value_field": "completeness",
    "non_numeric_value": "completeness",
    "duplicate_key": "uniqueness",
    "series_date_not_future": "validity",
    "invalid_currency": "validity",
    "mixed_currency": "validity",
    "fraction_out_of_range": "validity",
    "negative_amount": "validity",
    "irr_implausible": "validity",
    "sum_to_one": "consistency",
    "sector_rollup": "consistency",
    "hierarchy_conflict": "consistency",
    "naming_note": "consistency",
    "occupancy_weighted": "consistency",
    "rule_inputs_missing": "business",
    "gav_not_positive": "business",
    "nav_exceeds_gav": "business",
    "ltv_above_cap": "business",
    "ltv_vs_nav_gap": "business",
    "lifecycle_split": "business",
    "debt_maturity_vs_ltv": "business",
    "fees_high": "business",
    "bad_debts_high": "business",
    "zero_interest": "business",
    "date_order": "business",
    "iqr_outlier": "outlier",
    "reference_incomplete_row": "reference",
    "reference_duplicate_fund": "reference",
    "fund_not_in_reference": "reference",
    "fund_missing_from_extract": "reference",
}

# Datapoint names used by several checks.
COUNTRY = "Country"
COUNTRY_PCT = "Country Percentage"
REGION_L1 = "Region Level 1"
REGION_L2 = "Region Level 2"
SECTOR_L1 = "Sector - level 1"
SECTOR_L2 = "Sector - level 2"
SECTOR_PCT_L2 = "Portfolio sector percentage (by GAV) - total portfolio"
SECTOR_PCT_L1 = "Portfolio sector percentage (by GAV) - total portfolio Sector Level 1"
OCC_L1 = "Average occupancy rate (%) - completed assets Sector Level 1"
OCC_L2 = "Average occupancy rate (%) - completed assets Sector Level 2"
OCC_FUND = "Portfolio average occupancy rate"
GAV_COMPOSITION = ["Cash & Near Cash", "Others", "Real Estate Investments (Immovable Property)"]

# Label datapoints: the label is Value String. Their analysis columns may hold other values, so they are
# neither required nor compared with the label.
LABEL_DATAPOINTS = [COUNTRY, REGION_L1, REGION_L2, SECTOR_L1, SECTOR_L2]

# Analysis columns each exposure percentage datapoint must populate.
REQUIRED_DIMENSIONS = {
    COUNTRY_PCT: ["a1", "a2", "a3"],
    SECTOR_PCT_L1: ["a1"],
    SECTOR_PCT_L2: ["a1", "a2"],
    OCC_L1: ["a1"],
    OCC_L2: ["a1", "a2"],
}

BLANK = "<blank>"  # stands in for a missing label when comparing labels


def _issue(
    check: str,
    severity: str,
    detail: str,
    fund: Hashable | None = None,
    datapoint: str | None = None,
    excel_row: int | None = None,
) -> dict[str, Any]:
    """Build one issue record; the group comes from CHECK_GROUPS."""
    return {
        "group": CHECK_GROUPS[check],
        "check": check,
        "severity": severity,
        "fund": None if fund is None else str(fund),
        "datapoint": datapoint,
        "excel_row": excel_row,
        "detail": detail,
    }


def _frame(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Turn issue records into a frame with fixed columns and dtypes, even when empty."""
    out = pd.DataFrame(list(records), columns=ISSUE_COLUMNS)
    return out.astype({c: "object" for c in ISSUE_COLUMNS if c != "excel_row"}).astype(
        {"excel_row": "Int64"}
    )


def _dp(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Rows for one datapoint."""
    return df[df["datapoint"] == name]


def _row_issues(
    rows: pd.DataFrame, check: str, severity: str, detail: Callable[[pd.Series], str]
) -> list[dict[str, Any]]:
    """One issue per offending source row, keeping fund, datapoint and Excel row."""
    return [
        _issue(check, severity, detail(r), r["fund"], r["datapoint"], int(r["excel_row"]))
        for _, r in rows.iterrows()
    ]


def _unique(rows: pd.DataFrame, keys: list[str], value: str) -> pd.Series:
    """First value per key, keeping blank keys.

    Duplicates are reported by `check_duplicate_keys`; deduplicating here keeps
    join-based checks from crashing on them.
    """
    return rows.groupby(keys, dropna=False)[value].first()


def _fmt_dates(dates: Iterable[pd.Timestamp], limit: int = 5) -> str:
    """Short comma-separated date list for issue text."""
    ds = sorted(dates)
    shown = ", ".join(str(d.date()) for d in ds[:limit])
    return shown + (f" (+{len(ds) - limit} more)" if len(ds) > limit else "")


def calendar(reporting_date: str | pd.Timestamp, spec: dict[str, Any]) -> list[pd.Timestamp]:
    """Period-end dates strictly after the reporting date, per a calendar spec in the rules."""
    report = pd.Timestamp(reporting_date)
    skip, periods = int(spec.get("skip", 0)), int(spec["periods"])
    # date_range includes the start if it is itself a period end, hence the extra period.
    dates = pd.date_range(report, periods=periods + skip + 1, freq=spec["freq"])
    return list(dates[dates > report][skip : skip + periods])


# ---------------------------------------------------------------- structure


def check_unused_columns(raw: pd.DataFrame) -> pd.DataFrame:
    """Columns the loader drops must be empty, otherwise data would be lost silently.

    Analysis4 is loaded but used by no check: it may hold other values on label rows only, so on any
    other row it is reported like an unused column.
    """
    out = []
    a4 = raw["Analysis4"].notna() & (raw["Analysis4"].astype("string").str.strip() != "")
    a4 &= ~raw["Datapoint.Name"].astype("string").str.strip().isin(LABEL_DATAPOINTS)
    if a4.any():
        out.append(_issue("unused_column_populated", "error",
                          f"Column 'Analysis4' has {int(a4.sum())} values on non-label rows but is not used"))
    for col in raw.columns:
        if col in RENAME:
            continue
        s = raw[col]
        populated = s.notna() & (s.astype("string").str.strip() != "")
        if populated.any():
            out.append(_issue("unused_column_populated", "error",
                              f"Column {col!r} has {int(populated.sum())} values but is not loaded"))
    return _frame(out)


def check_reporting_date(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Every row must carry the expected reporting date (NaT counts as wrong)."""
    expected = pd.Timestamp(rules["reporting_date"])
    bad = df[df["reporting_date"].ne(expected) | df["reporting_date"].isna()]
    return _frame(_row_issues(bad, "reporting_date", "error",
                              lambda r: f"Reporting date {r['reporting_date']} != {expected.date()}"))


def check_keys_and_funds(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """No row lacks fund/category/datapoint, and the fund universe has the expected size."""
    blank = df[df[["fund", "category", "datapoint"]].isna().any(axis=1)]
    out = _row_issues(blank, "blank_key", "error", lambda r: "Fund, category or datapoint is blank")
    n, expected = df["fund"].nunique(), rules["expected_fund_count"]
    if n != expected:
        out.append(_issue("fund_count", "error", f"{n} funds in extract, expected {expected}"))
    return _frame(out)


def check_datapoint_catalogue(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Every (category, datapoint) must be known, and every known one must appear."""
    expected = {(c, d) for c, dps in rules["datapoints"].items() for d in dps}
    keys = df[["category", "datapoint"]].dropna().drop_duplicates()
    found = set(map(tuple, keys.to_numpy()))
    out = [
        _issue("unknown_datapoint", "error", f"Category {c!r} / datapoint {d!r} is not in the rules",
               datapoint=d)
        for c, d in sorted(found - expected)
    ]
    out += [
        _issue("missing_datapoint", "error", f"Category {c!r} / datapoint {d!r} has no rows at all",
               datapoint=d)
        for c, d in sorted(expected - found)
        # No rows anywhere for a "none when missing" datapoint just means no fund has any.
        if d not in rules["none_when_missing"]
    ]
    return _frame(out)


# ---------------------------------------------------------------- completeness


def check_datapoint_coverage(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """List each catalogued datapoint a fund does not report at all (a gap, not an error).

    Funds are not expected to report everything. Datapoints in `none_when_missing` (e.g. Bad debts)
    mean "none" when absent, so they are not listed.
    """
    none = set(rules["none_when_missing"])
    reported = set(map(tuple, df[["fund", "datapoint"]].dropna().drop_duplicates().to_numpy()))
    return _frame(
        _issue("datapoint_not_reported", "info", f"{category}: not reported for this fund", fund, dp)
        for fund in sorted(df["fund"].dropna().unique())
        for category, dps in rules["datapoints"].items()
        for dp in dps
        if dp not in none and (fund, dp) not in reported
    )


def check_fund_level_completeness(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """No fund has more than one row for a Fund Info or GAV Composition datapoint.

    A datapoint with no row is a gap, listed by `check_datapoint_coverage`.
    """
    out = []
    funds = sorted(df["fund"].dropna().unique())
    for category in ["Fund Info", "GAV Composition"]:
        counts = df[df["category"] == category].groupby(["fund", "datapoint"]).size()
        for fund in funds:
            for dp in rules["datapoints"][category]:
                n = int(counts.get((fund, dp), 0))
                if n > 1:
                    out.append(_issue("fund_level_count", "error",
                                      f"{category}: expected 1 row, found {n}", fund, dp))
    return _frame(out)


def check_time_series_completeness(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Debt maturity and projection series are dated, forward-looking and complete per reporting fund.

    Expected calendars are derived from the reporting date, so a gap shared by every
    fund is still caught. Projections may follow any one of the allowed calendars.
    """
    out = []
    report = pd.Timestamp(rules["reporting_date"])
    series = df[df["category"].isin(["Debt Maturity", "Projection"])]

    out += _row_issues(series[series["date"].isna()], "series_date_missing", "error",
                       lambda r: "Date is missing or unparseable")
    out += _row_issues(series[series["date"] <= report], "series_date_not_future", "error",
                       lambda r: f"Forward-looking date {r['date'].date()} is not after reporting date")

    # Debt maturity: one fixed yearly calendar.
    debt = _dp(series, "Debt maturity value")
    debt_cal = set(calendar(report, rules["debt_maturity_calendar"]))
    # A fund with no rows at all for a series does not report it: a gap listed by
    # check_datapoint_coverage, not a partial calendar.
    for fund in sorted(debt["fund"].dropna().unique()):
        dates = set(debt.loc[debt["fund"] == fund, "date"].dropna())
        missing, extra = debt_cal - dates, dates - debt_cal
        if missing or extra:
            detail = "; ".join(
                part for part in [
                    f"missing {_fmt_dates(missing)}" if missing else "",
                    f"unexpected {_fmt_dates(extra)}" if extra else "",
                ] if part
            )
            out.append(_issue("series_gap", "error", detail, fund, "Debt maturity value"))

    # Projections: each fund must match exactly one allowed calendar.
    calendars = {name: set(calendar(report, spec)) for name, spec in rules["projection_calendars"].items()}
    fund_freq: dict[str, set[str]] = {}  # calendar name -> funds using it
    for dp in rules["datapoints"]["Projection"]:
        sub = _dp(series, dp)
        for fund in sorted(sub["fund"].dropna().unique()):
            dates = set(sub.loc[sub["fund"] == fund, "date"].dropna())
            match = [name for name, cal in calendars.items() if dates == cal]
            if match:
                fund_freq.setdefault(match[0], set()).add(fund)
                continue
            # Report against the closest calendar to make the gap actionable.
            name, cal = min(calendars.items(), key=lambda kv: len(kv[1] ^ dates))
            missing, extra = cal - dates, dates - cal
            detail = f"Matches no calendar; closest is {name}"
            if missing:
                detail += f"; missing {_fmt_dates(missing)}"
            if extra:
                detail += f"; unexpected {_fmt_dates(extra)}"
            out.append(_issue("series_gap", "error", detail, fund, dp))
    if len(fund_freq) > 1:
        # Mixed frequencies are valid but matter for aggregation in the analysis phase.
        mix = ", ".join(f"{k}: {len(v)} funds" for k, v in sorted(fund_freq.items()))
        out.append(_issue("projection_frequency_mix", "info",
                          f"Projection frequency differs across funds ({mix})",
                          datapoint="Capital call amount"))
    return _frame(out)


def check_dimensions(df: pd.DataFrame) -> pd.DataFrame:
    """Exposure rows populate every analysis column their datapoint relies on."""
    out = []
    for dp, cols in REQUIRED_DIMENSIONS.items():
        rows = _dp(df, dp)
        for col in cols:
            # Typed default arg binds the loop variable (a bare lambda defeats mypy and B023).
            def blank_msg(r: pd.Series, col: str = col) -> str:
                return f"Analysis column {col} is blank"

            out += _row_issues(rows[rows[col].isna()], "missing_dimension", "error", blank_msg)
    return _frame(out)


def check_exposure_completeness(df: pd.DataFrame) -> pd.DataFrame:
    """Exposure label rows and percentage rows describe the same members per fund.

    Labels are the Value String of the label rows; the percentage rows name their member in an analysis
    column (country in a1, sub-region in a2, continent in a3; sector in a1).
    """
    out = []
    # (label datapoint, percentage datapoint, analysis column of the percentage rows holding the member)
    pairs = [
        (COUNTRY, COUNTRY_PCT, "a1"),
        (REGION_L1, COUNTRY_PCT, "a2"),
        (REGION_L2, COUNTRY_PCT, "a3"),
        (SECTOR_L2, SECTOR_PCT_L2, "a1"),
        (SECTOR_L1, SECTOR_PCT_L1, "a1"),
    ]
    for fund, g in df.groupby("fund"):
        for label_dp, pct_dp, pct_col in pairs:
            labels = set(_dp(g, label_dp)["value_str"].dropna())
            pcts = set(_dp(g, pct_dp)[pct_col].dropna())
            # A side with no rows is a datapoint the fund does not report (a gap, listed separately).
            if labels and pcts and labels != pcts:
                out.append(_issue("exposure_members_mismatch", "error",
                                  f"{label_dp} members {sorted(labels ^ pcts)} not matched in {pct_dp}",
                                  fund, label_dp))
        # Occupancy covers completed assets only, so a subset of sectors is fine.
        for occ_dp, pct_dp in [(OCC_L2, SECTOR_PCT_L2), (OCC_L1, SECTOR_PCT_L1)]:
            extra = set(_dp(g, occ_dp)["a1"].dropna()) - set(_dp(g, pct_dp)["a1"].dropna())
            if extra:
                out.append(_issue("occupancy_unknown_sector", "error",
                                  f"Occupancy reported for sectors with no GAV share: {sorted(extra)}",
                                  fund, occ_dp))
    return _frame(out)


def check_value_type(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Each row populates exactly the one value field its datapoint expects.

    A row with no value at all (blank, null or NA) is a gap (`value_missing`, info), except for
    datapoints in `none_when_missing`, where it means "none", and `scheduled_amounts`, where it means
    nothing falls on that row's date. A value in the wrong field is an error.
    """
    kind_of = {d: k for dps in rules["datapoints"].values() for d, k in dps.items()}
    field = {"num": "value_num", "date": "value_date", "str": "value_str"}
    populated = df[list(field.values())].notna()
    expected = df["datapoint"].map(kind_of).map(field)

    # A row is wrong if any field's populated-state differs from "is the expected field".
    wrong = pd.concat({f: populated[f] != expected.eq(f) for f in field.values()}, axis=1).any(axis=1)
    # Non-numeric text is reported as non_numeric_value, so it is not a blank.
    empty = ~populated.any(axis=1) & expected.notna() & df["value_num_text"].isna()
    none = df["datapoint"].isin([*rules["none_when_missing"], *rules["scheduled_amounts"]])
    bad = df[wrong & expected.notna() & ~empty]  # unknown datapoints are reported by the catalogue check

    def detail(r: pd.Series) -> str:
        filled = [f for f in field.values() if populated.at[r.name, f]] or ["nothing"]
        return f"Expected only {expected.at[r.name]}, found {', '.join(filled)}"

    out = _row_issues(bad, "value_field", "error", detail)
    out += _row_issues(df[empty & ~none], "value_missing", "info",
                       lambda r: "Reported with no value (blank, null or NA)")
    text = df[df["value_num_text"].notna()]
    out += _row_issues(text, "non_numeric_value", "error",
                       lambda r: f"Value Num holds non-numeric text {r['value_num_text']!r}")
    return _frame(out)


# ---------------------------------------------------------------- uniqueness


def check_duplicate_keys(df: pd.DataFrame) -> pd.DataFrame:
    """No two rows share fund, datapoint, date and analysis dimensions.

    Label rows have no reliable analysis key, so a country or sector label must instead be unique by its
    Value String. Region labels repeat by design (several countries share a region), so they are not
    checked here.
    """
    labels = df["datapoint"].isin(LABEL_DATAPOINTS)
    other_dups = df[~labels & df.duplicated(KEY, keep=False)]
    # Blank labels are value_missing gaps, not duplicates of each other.
    named = df[df["datapoint"].isin([COUNTRY, SECTOR_L1, SECTOR_L2]) & df["value_str"].notna()]
    label_dups = named[named.duplicated(["fund", "datapoint", "date", "value_str"], keep=False)]
    dup = pd.concat([other_dups, label_dups])
    return _frame(_row_issues(dup, "duplicate_key", "error",
                              lambda r: f"Duplicate key on {r['date']} / "
                              f"{r['value_str'] if r['datapoint'] in LABEL_DATAPOINTS else r['a1']}"))


# ---------------------------------------------------------------- validity


def check_currency(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Currencies are valid and constant within a fund."""
    out = _row_issues(df[~df["currency"].isin(rules["valid_currencies"])], "invalid_currency", "error",
                      lambda r: f"Currency {r['currency']!r} not allowed")
    n_cur = df.groupby("fund")["currency"].nunique()
    out += [_issue("mixed_currency", "error", f"{n} currencies in one fund", fund)
            for fund, n in n_cur[n_cur > 1].items()]
    return _frame(out)


def check_ranges(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Fractions lie in [0, 1], amounts are non-negative, IRRs are plausible."""
    frac = df[df["datapoint"].isin(rules["fraction_datapoints"])]
    out = _row_issues(frac[(frac["value_num"] < 0) | (frac["value_num"] > 1)], "fraction_out_of_range",
                      "error", lambda r: f"Value {r['value_num']} outside [0, 1]")

    amt = df[df["datapoint"].isin(rules["amount_datapoints"])]
    out += _row_issues(amt[amt["value_num"] < 0], "negative_amount", "error",
                       lambda r: f"Negative amount {r['value_num']:,.2f}")

    lo, hi = rules["irr_bounds"]
    irr = df[df["datapoint"].isin(["Fund Since Inception Net IRR", "Fund Target Net IRR"])]
    out += _row_issues(irr[(irr["value_num"] < lo) | (irr["value_num"] > hi)], "irr_implausible",
                       "warning", lambda r: f"IRR {r['value_num']:.2%} outside [{lo:.0%}, {hi:.0%}]")
    return _frame(out)


# ---------------------------------------------------------------- consistency


def _sum_check(df: pd.DataFrame, datapoints: list[str], label: str, tol: float) -> list[dict[str, Any]]:
    """Per fund, the values of the given datapoints must add up to 1.

    A fund with no value in any of them does not report the mix (a gap, listed elsewhere) and is skipped.
    """
    rows = df[df["datapoint"].isin(datapoints) & df["value_num"].notna()]
    totals = rows.groupby("fund")["value_num"].sum()
    return [
        _issue("sum_to_one", "error", f"{label} sums to {total:.4%} (off by {total - 1:+.4%})", fund,
               datapoints[0] if len(datapoints) == 1 else label)
        for fund, total in totals.items()
        if not abs(total - 1) <= tol
    ]


def check_sums_to_one(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Country mix, sector mixes and GAV composition each total 100%."""
    tol = rules["tolerances"]["sum_to_one"]
    out = _sum_check(df, [COUNTRY_PCT], "Country mix", tol)
    out += _sum_check(df, [SECTOR_PCT_L2], "Sector L2 mix", tol)
    out += _sum_check(df, [SECTOR_PCT_L1], "Sector L1 mix", tol)
    out += _sum_check(df, GAV_COMPOSITION, "GAV composition", tol)
    return _frame(out)


def check_sector_rollup(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Sector L2 percentages, grouped by their L1 parent (a2), equal the L1 percentages."""
    tol = rules["tolerances"]["rollup"]
    # dropna=False keeps L2 rows with a blank parent, so their share shows up as a mismatch.
    l2 = _dp(df, SECTOR_PCT_L2).groupby(["fund", "a2"], dropna=False)["value_num"].sum()
    l1 = _unique(_dp(df, SECTOR_PCT_L1), ["fund", "a1"], "value_num")
    l2.index.names = l1.index.names = ["fund", "sector"]
    # A sector present on only one side counts as 0% on the other.
    both = pd.concat({"l2_sum": l2, "l1": l1}, axis=1).fillna(0)
    bad = both[~((both["l2_sum"] - both["l1"]).abs() <= tol)]
    return _frame(
        _issue("sector_rollup", "error",
               f"{sector if pd.notna(sector) else BLANK}: L2 sum {r['l2_sum']:.4%} vs L1 {r['l1']:.4%}",
               fund, SECTOR_PCT_L1)
        for (fund, sector), r in bad.iterrows()
    )


def check_hierarchies(df: pd.DataFrame) -> pd.DataFrame:
    """Country->region and sector L2->L1 mappings are consistent within and across funds."""
    out = []
    geo = _dp(df, COUNTRY_PCT)
    # Region label rows have no reliable country key (their analysis columns may hold other values), so
    # they are not mapped country by country; check_exposure_completeness compares the region sets.

    # Across funds, each child maps to exactly one parent (blank counts as a parent value).
    maps = [
        (geo, "a1", "a2", "country -> sub-region", COUNTRY_PCT),
        (geo, "a2", "a3", "sub-region -> continent", COUNTRY_PCT),
        (_dp(df, SECTOR_PCT_L2), "a1", "a2", "sector L2 -> L1", SECTOR_PCT_L2),
        (_dp(df, OCC_L2), "a1", "a2", "occupancy sector L2 -> L1", OCC_L2),
    ]
    for rows, child, parent, label, dp in maps:
        parents = rows[parent].fillna(BLANK).groupby(rows[child]).unique()
        for key, vals in parents[parents.map(len) > 1].items():
            out.append(_issue("hierarchy_conflict", "error",
                              f"{label}: {key!r} maps to {sorted(map(str, vals))}", datapoint=dp))

    # Naming note: for geography "Level 1" is finer than "Level 2", the reverse of sectors.
    if not geo.empty:
        out.append(_issue("naming_note", "info",
                          "Region Level 1 holds sub-regions and Region Level 2 holds continents; "
                          "sector levels run the other way (L1 = broad)", datapoint=REGION_L1))
    return _frame(out)


def check_occupancy_weighted(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Fund occupancy is close to the GAV-weighted average of sector L2 occupancy.

    Weights are sector shares of total GAV, renormalised over the sectors that report
    occupancy: occupancy covers completed assets only, so sectors without it are excluded.
    GAV under development inside those sectors cannot be separated, hence a warning only.
    """
    tol = rules["tolerances"]["occupancy_weighted"]
    occ = _unique(_dp(df, OCC_L2), ["fund", "a1"], "value_num")
    gav = _unique(_dp(df, SECTOR_PCT_L2), ["fund", "a1"], "value_num")
    joined = pd.concat({"occ": occ, "w": gav}, axis=1).dropna()
    weight_sum = joined["w"].groupby(level="fund").sum()
    weighted = (joined["occ"] * joined["w"]).groupby(level="fund").sum() / weight_sum.where(weight_sum > 0)
    reported = _unique(_dp(df, OCC_FUND), ["fund"], "value_num")

    out = []
    for fund in sorted(df["fund"].dropna().unique()):
        rep, w = reported.get(fund, np.nan), weighted.get(fund, np.nan)
        if pd.isna(rep) or pd.isna(w):
            # Missing inputs are a data gap: noted, not passed silently and not a warning.
            out.append(_issue("occupancy_weighted", "info",
                              "Cannot compare: fund occupancy or weighted sector occupancy missing",
                              fund, OCC_FUND))
        elif not abs(rep - w) <= tol:
            out.append(_issue("occupancy_weighted", "warning",
                              f"Reported {rep:.2%} vs GAV-weighted sector {w:.2%}", fund, OCC_FUND))
    return _frame(out)


# ---------------------------------------------------------------- business rules

# Fund-level inputs the business rules depend on.
RULE_INPUTS = [
    "Fund GAV", "Fund NAV", "Fund LTV (current)", "Fund LTV Cap", "Completed assets",
    "Assets under development", "Base fee incurred to-date", "Carried interest incurred to-date",
    "Bad debts", "AIA Effective Interest in Fund",
]


# Inputs without which the balance-sheet rules (NAV vs GAV, LTV vs cap, debt) cannot run.
CORE_INPUTS = ["Fund GAV", "Fund NAV", "Fund LTV (current)"]


def check_business_rules(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Fund-level relationships: NAV vs GAV, LTV vs cap, lifecycle split, debt, fees, dates."""
    w = fund_info_wide(df)
    tol = rules["tolerances"]
    out = []

    def col(name: str) -> pd.Series:
        """Column, or all-NaN if a datapoint is absent (absence is reported elsewhere)."""
        return w[name] if name in w else pd.Series(np.nan, index=w.index)

    # Comparisons below are False on NaN, so name skipped funds explicitly. Missing inputs are data
    # gaps (info), except GAV, NAV and LTV: without them the core balance-sheet rules cannot run at all,
    # so that is a warning. "None when missing" datapoints such as Bad debts are never missing inputs.
    none = set(rules["none_when_missing"])
    needed = [n for n in [*RULE_INPUTS, *{d for pair in rules["date_order"] for d in pair}] if n not in none]
    for fund in w.index:
        gaps = [n for n in needed if pd.isna(col(n)[fund])]
        if gaps:
            core = set(gaps) & set(CORE_INPUTS)
            out.append(_issue("rule_inputs_missing", "warning" if core else "info",
                              f"Business rules partly skipped; missing: {', '.join(gaps)}", fund))

    gav, nav = col("Fund GAV"), col("Fund NAV")
    ltv, cap = col("Fund LTV (current)"), col("Fund LTV Cap")
    # GAV is the denominator of every ratio below; non-positive GAV is the root cause to report.
    for fund in w.index[gav <= 0]:
        out.append(_issue("gav_not_positive", "error", f"GAV is {gav[fund]:,.0f}", fund, "Fund GAV"))
    safe_gav = gav.where(gav > 0)

    for fund in w.index[nav > gav]:
        out.append(_issue("nav_exceeds_gav", "error",
                          f"NAV {nav[fund]:,.0f} > GAV {gav[fund]:,.0f}", fund, "Fund NAV"))
    for fund in w.index[ltv > cap]:
        out.append(_issue("ltv_above_cap", "warning",
                          f"LTV {ltv[fund]:.2%} above cap {cap[fund]:.2%}", fund, "Fund LTV (current)"))

    # GAV = debt + other liabilities + NAV, so debt/GAV (LTV) can be at most 1 - NAV/GAV.
    implied_max = 1 - nav / safe_gav
    for fund in w.index[ltv > implied_max + tol["ltv_vs_nav_gap"]]:
        out.append(_issue("ltv_vs_nav_gap", "warning",
                          f"LTV {ltv[fund]:.2%} exceeds 1 - NAV/GAV = {implied_max[fund]:.2%}",
                          fund, "Fund LTV (current)"))

    split = col("Completed assets") + col("Assets under development")
    for fund in w.index[(split - 1).abs() > tol["lifecycle_split"]]:
        out.append(_issue("lifecycle_split", "error",
                          f"Completed + under development = {split[fund]:.2%}", fund, "Completed assets"))

    # Scheduled debt maturities should total current debt (LTV x GAV). A blank amount on a schedule date
    # is zero; funds with no schedule rows do not report it (a gap), so they stay NaN and are not compared.
    debt = _dp(df, "Debt maturity value").groupby("fund")["value_num"].sum().reindex(w.index)
    expected_debt = ltv * safe_gav
    ratio = debt / expected_debt.where(expected_debt > 0)
    off = (ratio < tol["debt_vs_ltv_low"]) | (ratio > tol["debt_vs_ltv_high"])
    # Zero LTV with a non-zero schedule is also inconsistent.
    off |= (expected_debt == 0) & (debt > 0)
    for fund in w.index[off]:
        out.append(_issue("debt_maturity_vs_ltv", "warning",
                          f"Debt maturities total {debt[fund]:,.0f} vs LTV x GAV {expected_debt[fund]:,.0f}",
                          fund, "Debt maturity value"))

    fees = (col("Base fee incurred to-date") + col("Carried interest incurred to-date")) / safe_gav
    for fund in w.index[fees > rules["max_fees_to_gav"]]:
        out.append(_issue("fees_high", "warning", f"Fees to date are {fees[fund]:.2%} of GAV", fund,
                          "Base fee incurred to-date"))
    bad_debt = col("Bad debts") / safe_gav
    for fund in w.index[bad_debt > rules["max_bad_debt_to_gav"]]:
        out.append(_issue("bad_debts_high", "warning", f"Bad debts are {bad_debt[fund]:.2%} of GAV", fund,
                          "Bad debts"))

    aia = col("AIA Effective Interest in Fund")
    for fund in w.index[aia <= 0]:
        out.append(_issue("zero_interest", "warning", "AIA effective interest is zero or negative", fund,
                          "AIA Effective Interest in Fund"))

    for earlier, later in rules["date_order"]:
        a, b = col(earlier), col(later)
        for fund in w.index[a > b]:
            out.append(_issue("date_order", "error",
                              f"{earlier} {a[fund].date()} is after {later} {b[fund].date()}",
                              fund, earlier))
    return _frame(out)


# ---------------------------------------------------------------- outliers


def fund_metrics(df: pd.DataFrame, none_when_missing: Iterable[str] = ()) -> pd.DataFrame:
    """Fund-level frame plus derived, currency-free ratios used for outlier checks.

    Datapoints in `none_when_missing` (from the rules) are 0 where a fund does not report them.
    """
    w = fund_info_wide(df)
    for name in none_when_missing:
        if name in w:
            w[name] = w[name].fillna(0.0)

    def col(name: str) -> pd.Series:
        """Numeric column, or all-NaN if the datapoint is absent."""
        return w[name].astype(float) if name in w else pd.Series(np.nan, index=w.index)

    gav = col("Fund GAV").where(lambda s: s > 0)  # avoid inf ratios from zero GAV
    w["nav_to_gav"] = col("Fund NAV") / gav
    w["fees_to_gav"] = (col("Base fee incurred to-date") + col("Carried interest incurred to-date")) / gav
    w["bad_debt_to_gav"] = col("Bad debts") / gav
    return w


def check_outliers(df: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Flag funds outside the Tukey fences on currency-free metrics."""
    k = rules["outlier_iqr_k"]
    m = fund_metrics(df, rules["none_when_missing"])
    unknown = [x for x in rules["outlier_metrics"] if x not in m]
    if unknown:
        # A typo in the rules would otherwise silently disable the check.
        raise ValueError(f"Unknown outlier metrics in rules: {unknown}")
    out = []
    for metric in rules["outlier_metrics"]:
        s = m[metric].astype(float).replace([np.inf, -np.inf], np.nan).dropna()
        q1, q3 = s.quantile([0.25, 0.75])
        lo, hi = q1 - k * (q3 - q1), q3 + k * (q3 - q1)
        for fund, v in s[(s < lo) | (s > hi)].items():
            out.append(_issue("iqr_outlier", "info",
                              f"{metric} = {v:.4f} outside [{lo:.4f}, {hi:.4f}] (median {s.median():.4f})",
                              fund, metric))
    return _frame(out)


# ---------------------------------------------------------------- GP reference


def check_reference(df: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Check that the GP reference is well formed and lists exactly the funds in the extract."""
    out = []
    # Reference rows are not extract rows, so their row number goes in the detail, not excel_row.
    for r in reference[reference[["gp", "fund"]].isna().any(axis=1)].to_dict("records"):
        blank = "GP Name" if pd.isna(r["gp"]) else "Fund Name"
        out.append(_issue("reference_incomplete_row", "error",
                          f"Reference row {r['excel_row']} has no {blank}",
                          None if pd.isna(r["fund"]) else r["fund"]))
    listed = reference.dropna(subset=["gp", "fund"])
    for fund, rows in listed[listed["fund"].duplicated(keep=False)].groupby("fund"):
        gps = sorted(rows["gp"].unique())
        rows_text = ", ".join(str(x) for x in rows["excel_row"])
        if len(gps) > 1:
            out.append(_issue("reference_duplicate_fund", "error",
                              f"Listed under {len(gps)} GPs ({', '.join(gps)}) in reference rows "
                              f"{rows_text}; "
                              f"using {rows['gp'].iloc[0]}", fund))
        else:
            out.append(_issue("reference_duplicate_fund", "warning",
                              f"Listed {len(rows)} times under {gps[0]} (reference rows {rows_text})", fund))

    # Names must match exactly; a near miss (case or spacing) is pointed out to speed up the fix.
    def norm(name: str) -> str:
        return " ".join(name.split()).casefold()

    in_ref, in_extract = set(listed["fund"]), set(df["fund"].dropna())
    ref_by_norm = {norm(f): f for f in in_ref - in_extract}
    for fund in sorted(in_extract - in_ref):
        hint = ref_by_norm.get(norm(fund))
        out.append(_issue("fund_not_in_reference", "error",
                          "Fund is not in the GP reference, so its GP is unknown"
                          + (f"; the reference has {hint!r}" if hint else ""), fund))
    gp = gp_map(reference)
    for fund in sorted(in_ref - in_extract):
        out.append(_issue("fund_missing_from_extract", "error",
                          f"Expected from {gp[fund]} per the GP reference, but not in the extract", fund))
    return _frame(out)


# ---------------------------------------------------------------- runner


def run_all(raw: pd.DataFrame, df: pd.DataFrame, rules: Rules, reference: pd.DataFrame) -> pd.DataFrame:
    """Run every check, including those against the GP `reference`, and return all issues, most severe first.

    A check that raises becomes a `check_crashed` error instead of aborting the run.
    """
    checks: list[tuple[str, Callable[[], pd.DataFrame]]] = [
        ("check_unused_columns", lambda: check_unused_columns(raw)),
        ("check_reporting_date", lambda: check_reporting_date(df, rules)),
        ("check_keys_and_funds", lambda: check_keys_and_funds(df, rules)),
        ("check_datapoint_catalogue", lambda: check_datapoint_catalogue(df, rules)),
        ("check_datapoint_coverage", lambda: check_datapoint_coverage(df, rules)),
        ("check_fund_level_completeness", lambda: check_fund_level_completeness(df, rules)),
        ("check_time_series_completeness", lambda: check_time_series_completeness(df, rules)),
        ("check_dimensions", lambda: check_dimensions(df)),
        ("check_exposure_completeness", lambda: check_exposure_completeness(df)),
        ("check_value_type", lambda: check_value_type(df, rules)),
        ("check_duplicate_keys", lambda: check_duplicate_keys(df)),
        ("check_currency", lambda: check_currency(df, rules)),
        ("check_ranges", lambda: check_ranges(df, rules)),
        ("check_sums_to_one", lambda: check_sums_to_one(df, rules)),
        ("check_sector_rollup", lambda: check_sector_rollup(df, rules)),
        ("check_hierarchies", lambda: check_hierarchies(df)),
        ("check_occupancy_weighted", lambda: check_occupancy_weighted(df, rules)),
        ("check_business_rules", lambda: check_business_rules(df, rules)),
        ("check_outliers", lambda: check_outliers(df, rules)),
        ("check_reference", lambda: check_reference(df, reference)),
    ]
    results = []
    for name, fn in checks:
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001 - any failure must still produce a report
            results.append(_frame([_issue("check_crashed", "error", f"{name} raised {exc!r}")]))

    non_empty = [r for r in results if not r.empty]
    issues = pd.concat(non_empty, ignore_index=True) if non_empty else _frame([])
    issues["severity"] = pd.Categorical(issues["severity"], SEVERITIES, ordered=True)
    return issues.sort_values(["severity", "group", "check", "fund"], ignore_index=True)


def summarise(issues: pd.DataFrame) -> pd.DataFrame:
    """One row per check: status (PASS / INFO / FLAG), worst severity, issues and funds affected."""
    grouped = issues.groupby("check", observed=True).agg(
        severity=("severity", "min"),
        issues=("check", "size"),
        funds_affected=("fund", "nunique"),
    )
    summary = grouped.reindex(list(CHECK_GROUPS))
    summary.index.name = "check"
    summary["group"] = [CHECK_GROUPS[c] for c in summary.index]
    summary["issues"] = summary["issues"].fillna(0).astype(int)
    summary["funds_affected"] = summary["funds_affected"].fillna(0).astype(int)
    # Info-only checks are notes, not problems.
    summary["status"] = np.select(
        [
            (summary["issues"] == 0).to_numpy(dtype=bool),
            (summary["severity"].astype("string") == "info").fillna(False).to_numpy(dtype=bool),
        ],
        ["PASS", "INFO"],
        default="FLAG",
    )
    cols = ["check", "group", "status", "severity", "issues", "funds_affected"]
    return summary.reset_index()[cols]
