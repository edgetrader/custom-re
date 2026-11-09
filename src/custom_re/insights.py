"""Derive the management report's content (verdict, findings, datapoint usability) for one quarter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from custom_re.checks import CHECK_GROUPS, calendar
from custom_re.completeness import (
    MISSING,
    NO_VALUE,
    NOT_REPORTED,
    PARTIAL,
    REPORTED,
    UNLISTED,
    datapoint_completeness,
    datapoint_gaps,
    fund_completeness,
    gp_completeness,
)
from custom_re.loader import RENAME, fund_info_wide

Rules = dict[str, Any]

READY, CARE, NOT_USABLE, EMPTY = "Ready", "Use with care", "Not usable", "Empty"
RATINGS = [READY, CARE, NOT_USABLE, EMPTY]
_RANK = {READY: 0, CARE: 1, NOT_USABLE: 2}
VALUE_COL = {"num": "value_num", "date": "value_date", "str": "value_str"}

# Category names of the forward-looking series (debt schedule, projections).
SERIES_CATEGORIES = ("Debt Maturity", "Projection")
# Findings list at most this many check names; the workbook has the full list.
TOP_CHECKS = 5
# Average days per month, to express date gaps in months.
DAYS_PER_MONTH = 30.44
# Long issue details are cut in the HTML; the workbook keeps the full text.
MAX_EXAMPLE_CHARS = 200

# Tones order findings and colour them: bad blocks use, warn needs a decision, info is a caveat.
TONE_ORDER = {"bad": 0, "warn": 1, "info": 2}

# Datapoints whose meaning drives several findings.
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


@dataclass
class DatapointUsability:
    """One row of the datapoint register."""

    category: str
    datapoint: str
    coverage: int
    total: int
    observed: str
    rating: str = READY
    notes: list[str] = field(default_factory=list)

    def flag(self, rating: str, note: str) -> None:
        """Add a note and keep the worst rating seen so far."""
        if _RANK[rating] > _RANK[self.rating]:
            self.rating = rating
        self.notes.append(note)


@dataclass
class Finding:
    """One numbered management finding."""

    title: str
    tone: str  # bad | warn | info
    tag: str
    body: str
    action: str


@dataclass
class ReportData:
    """Everything the HTML report shows for one quarter."""

    period_label: str
    reporting_date: pd.Timestamp
    source_name: str
    verdict_tone: str
    verdict: str
    verdict_detail: str
    figures: list[tuple[str, str]]
    datapoints: list[DatapointUsability]
    findings: list[Finding]
    checks: pd.DataFrame  # one row per check group
    flagged: pd.DataFrame  # non-PASS checks with an example detail
    counts: dict[str, int]  # issues by severity
    unassigned_errors: int  # errors not tied to one datapoint, so not reflected in register ratings
    gp_completeness: pd.DataFrame  # one row per GP, most complete first
    fund_completeness: pd.DataFrame  # one row per fund (extract or GP reference), most complete first
    datapoint_completeness: pd.DataFrame  # one row per datapoint, most complete first
    gaps: pd.DataFrame  # one row per datapoint and fund lacking a value (datapoint_gaps)
    top_n: int  # funds shown as most and least complete


# ---------------------------------------------------------------- formatting


def fmt_amount(v: float) -> str:
    """Compact amount: 60k, 165m, 42.9bn (three significant figures)."""
    a = abs(v)
    for scale, unit in [(1e9, "bn"), (1e6, "m"), (1e3, "k")]:
        # 0.9995 x scale already rounds to 1 of this unit at 3 significant figures (not "1e+03" of the
        # unit below).
        if a >= scale * 0.9995:
            x = v / scale
            return (f"{x:,.0f}" if abs(x) >= 999.5 else f"{x:.3g}") + unit
    return f"{v:,.0f}"


def fmt_pct(v: float, digits: int = 1) -> str:
    """Fraction as a percentage with a true minus sign, e.g. -0.019 -> −1.9%."""
    text = f"{v * 100:.{digits}f}%"
    if float(text[:-1]) == 0:
        text = text.lstrip("-")  # no "−0.0%" for tiny negatives
    return text.replace("-", "−")


def _range(lo: str, hi: str) -> str:
    return lo if lo == hi else f"{lo} – {hi}"


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


# ---------------------------------------------------------------- facts


def _col(w: pd.DataFrame, name: str) -> pd.Series:
    """Column of the fund frame, or all-NaN if the datapoint is absent."""
    return w[name] if name in w else pd.Series(np.nan, index=w.index)


def _date_col(w: pd.DataFrame, name: str) -> pd.Series:
    """Date column of the fund frame; all-NaT if absent, so comparisons with a Timestamp never fail."""
    return pd.to_datetime(_col(w, name), errors="coerce")


def _any_positive(df: pd.DataFrame, dp: str) -> pd.Series:
    """Per fund: does any period of series `dp` have a positive amount."""
    s = df[df["datapoint"] == dp].groupby("fund")["value_num"].apply(lambda v: bool((v > 0).any()))
    return s.astype(bool)


def _empty_source_fields(raw: pd.DataFrame) -> list[str]:
    """Named source columns that the loader ignores and that hold no values."""
    out = []
    for col in raw.columns:
        if col in RENAME:
            continue
        label = str(col).split(".")[0].strip()
        if not label:
            continue  # unnamed padding columns of the export
        s = raw[col]
        if not (s.notna() & (s.astype("string").str.strip() != "")).any():
            out.append(label)
    return out


def _projection_frequency(df: pd.DataFrame, rules: Rules) -> dict[str, tuple[int, list[pd.Timestamp]]]:
    """Calendar name -> (funds using it, its dates), judged on capital-call dates.

    A fund counts only if its dates equal a calendar exactly; partial matches are series_gap errors.
    """
    report = pd.Timestamp(rules["reporting_date"])
    cals = {n: calendar(report, spec) for n, spec in rules["projection_calendars"].items()}
    used: dict[str, tuple[int, list[pd.Timestamp]]] = {}
    for _, dates in df[df["datapoint"] == CALLS].groupby("fund")["date"]:
        for name, cal in cals.items():
            if set(dates.dropna()) == set(cal):
                used[name] = (used.get(name, (0, cal))[0] + 1, cal)
    return used


# ---------------------------------------------------------------- datapoint register


def datapoint_register(df: pd.DataFrame, raw: pd.DataFrame, issues: pd.DataFrame, rules: Rules) -> list[
        DatapointUsability]:
    """Coverage, observed range, usability rating and notes for every catalogued datapoint."""
    report = pd.Timestamp(rules["reporting_date"])
    cfg = rules["report"]
    funds = df["fund"].dropna().unique()
    n_funds = len(funds)
    amounts = set(rules["amount_datapoints"])
    none_when_missing = rules["none_when_missing"]
    n_ccy = df.groupby("fund")["currency"].first().nunique()
    w = fund_info_wide(df)
    sev = issues[issues["severity"].isin(["error", "warning"])]
    naming_note = (issues["check"] == "naming_note").any()
    mix = len(_projection_frequency(df, rules)) > 1

    rows: list[DatapointUsability] = []
    for category, dps in rules["datapoints"].items():
        for dp, kind in dps.items():
            sub = df[df["datapoint"] == dp]
            vals = sub[VALUE_COL[kind]].dropna()
            covered = sub.loc[sub[VALUE_COL[kind]].notna(), "fund"].nunique()
            row = DatapointUsability(category, dp, covered, n_funds, _observed(vals, kind, dp, amounts))
            rows.append(row)

            # Generic rules: missing funds and check findings outrank any caveat.
            if not n_funds:
                row.flag(NOT_USABLE, "No funds in the extract.")
            elif dp in none_when_missing:
                # Not reported, blank or zero is an answer here ("none"), so coverage is not a gap.
                n_none = n_funds - int(sub.loc[sub[VALUE_COL[kind]].fillna(0) != 0, "fund"].nunique())
                if n_none:
                    row.notes.append(f"{_plural(n_none, 'fund')}: {none_when_missing[dp].lower()} "
                                     "(not reported, blank or zero), treated as none.")
            elif covered < n_funds:
                row.flag(CARE if covered else NOT_USABLE,
                         f"Missing for {n_funds - covered} of {n_funds} funds.")
            errs = (sev["datapoint"] == dp) & (sev["severity"] == "error")
            warns = (sev["datapoint"] == dp) & (sev["severity"] == "warning")
            if errs.any():
                row.flag(NOT_USABLE, f"{_plural(int(errs.sum()), 'error')} in the checks (see workbook).")
            if warns.any():
                row.flag(CARE, f"{_plural(int(warns.sum()), 'warning')} in the checks (see workbook).")
            if vals.empty:
                continue
            if dp in amounts and n_ccy > 1:
                # One currency per fund is by design: fine per fund, so a note, not a lower rating.
                row.notes.append("In the fund's own currency; compare funds by ratios, not totals.")
            if dp in amounts and dp not in none_when_missing:
                zeros = int((vals == 0).sum())
                if category in SERIES_CATEGORIES and zeros:
                    row.notes.append(f"{zeros / len(vals):.0%} of periods are zero.")
                elif zeros:
                    row.notes.append(f"Zero for {_plural(zeros, 'fund')}.")
    by_dp = {r.datapoint: r for r in rows}

    def note(dp: str, text: str, rating: str | None = None) -> None:
        """Attach a specific caveat to a datapoint present in the catalogue."""
        if dp in by_dp and by_dp[dp].coverage:
            if rating:
                by_dp[dp].flag(rating, text)
            else:
                by_dp[dp].notes.append(text)

    # Fund-life dates: term and investment period relative to the reporting date.
    past = int((_date_col(w, END_TERM) < report).sum())
    if past:
        note(END_TERM, f"{_plural(past, 'fund')} already past this date but still reporting.", CARE)
    ip_ended = _date_col(w, END_IP) < report
    calls = _any_positive(df, CALLS).reindex(w.index, fill_value=False)
    if ip_ended.any():
        note(END_IP, f"Ended for {_plural(int(ip_ended.sum()), 'fund')}; "
                     f"{int((ip_ended & calls).sum())} of these still project capital calls.")

    # Leverage headroom and returns versus target, as context for single-fund use.
    headroom = cfg["ltv_headroom"]
    ltv, cap = _col(w, LTV).astype(float), _col(w, LTV_CAP).astype(float)
    near = int(((cap - ltv) <= headroom).sum())
    if near:
        note(LTV, f"{_plural(near, 'fund')} within {headroom * 100:.0f} points of the LTV cap.")
    below = int((_col(w, IRR).astype(float) < _col(w, IRR_TARGET).astype(float)).sum())
    if below:
        note(IRR, f"{_plural(below, 'fund')} below target.")

    # Label caveats: anonymised countries and geography levels named opposite to sector levels.
    pattern = cfg["anonymised_label_pattern"]
    countries = df.loc[df["datapoint"] == COUNTRY, "value_str"].dropna()
    if pattern and len(countries) and countries.str.match(pattern).all():
        note(COUNTRY, "Anonymised labels: show exposure by region unless a mapping is supplied.", CARE)
    if naming_note:
        note(REGION_L1, "Holds the sub-region despite the \"Level 1\" name.", CARE)
        note(REGION_L2, "Holds the continent despite the \"Level 2\" name.", CARE)

    # Occupancy exists only for completed assets, so not every sector position has one.
    for occ, pct in [(OCC_L2, SECTOR_PCT_L2), (OCC_L1, SECTOR_PCT_L1)]:
        n_pos = int(df.loc[df["datapoint"] == pct, "value_num"].notna().sum())
        n_occ = int(df.loc[df["datapoint"] == occ, "value_num"].notna().sum())
        if n_pos and n_occ < n_pos:
            note(occ, f"Completed assets only: reported for {n_occ} of {n_pos} fund-sector positions.", CARE)

    # Series caveats: mixed projection calendars and a debt schedule derived from LTV x GAV.
    if mix:
        for dp in (CALLS, DISTS):
            note(dp, "Monthly or quarterly depending on the fund: compare funds by quarter.")
    if _exact_debt(df, w, cfg):
        note(DEBT, f"Totals equal LTV × GAV exactly for all {n_funds} funds: likely derived.")

    rows += [
        DatapointUsability("Empty source fields", f, 0, n_funds, "—", EMPTY,
                           [t for t in [cfg["empty_field_notes"].get(f, "")] if t])
        for f in _empty_source_fields(raw)
    ]
    return rows


def _observed(vals: pd.Series, kind: str, dp: str, amounts: set[str]) -> str:
    """Observed range across all values, formatted for the datapoint's kind."""
    if vals.empty:
        return "—"
    if kind == "str":
        return _plural(int(vals.nunique()), "label")
    if kind == "date":
        return _range(f"{vals.min():%b %Y}", f"{vals.max():%b %Y}")
    lo, hi = float(vals.min()), float(vals.max())
    if dp in amounts:
        return _range(fmt_amount(lo), fmt_amount(hi))
    return _range(fmt_pct(lo), fmt_pct(hi))


def _exact_debt(df: pd.DataFrame, w: pd.DataFrame, cfg: dict[str, Any]) -> bool:
    """Return True if every fund's debt maturities total LTV × GAV to within a tiny relative tolerance."""
    debt = df[df["datapoint"] == DEBT].groupby("fund")["value_num"].sum()
    expected = (_col(w, LTV).astype(float) * _col(w, GAV).astype(float)).reindex(debt.index)
    # The claim is about every fund, so a fund without a schedule or inputs makes it false.
    if debt.empty or len(debt) < len(w) or expected.isna().any() or (expected == 0).any():
        return False
    return bool((((debt - expected) / expected).abs() <= cfg["exact_rel_tol"]).all())


def _exact_occupancy_share(df: pd.DataFrame, w: pd.DataFrame, cfg: dict[str, Any]) -> bool:
    """Return True if, for every fund, GAV with sector occupancy equals the completed-assets share."""
    pct = df[df["datapoint"] == SECTOR_PCT_L2].set_index(["fund", "a1"])["value_num"]
    occ = df[df["datapoint"] == OCC_L2].dropna(subset=["value_num"]).set_index(["fund", "a1"])
    if pct.empty or occ.empty:
        return False
    share = pct[pct.index.isin(occ.index)].groupby(level="fund").sum()
    if share.empty or len(share) < len(w):
        return False  # .all() of nothing would be True
    completed = _col(w, COMPLETED).astype(float).reindex(share.index)
    return bool(((share - completed).abs() <= cfg["exact_share_tol"]).all())


# ---------------------------------------------------------------- findings


def findings(df: pd.DataFrame, raw: pd.DataFrame, issues: pd.DataFrame, rules: Rules,
             fc: pd.DataFrame, reference: pd.DataFrame) -> list[Finding]:
    """Return management findings that apply to this quarter, most severe first.

    `fc` is the fund completeness table (fund_completeness), which carries each fund's GP and status.
    """
    report = pd.Timestamp(rules["reporting_date"])
    cfg = rules["report"]
    w = fund_info_wide(df)
    out: list[Finding] = []

    # Check failures come first: they decide whether the data can be used at all.
    for severity, tone, tag in [("error", "bad", "Fix before use"), ("warning", "warn", "Review")]:
        sub = issues[issues["severity"] == severity]
        if sub.empty:
            continue
        top = sub["check"].value_counts().head(TOP_CHECKS)
        listed = ", ".join(f"{c} ({n})" for c, n in top.items())
        out.append(Finding(
            f"{_plural(len(sub), severity)} raised by the automated checks", tone, tag,
            f"{_plural(len(sub), 'issue')} across {_plural(sub['check'].nunique(), 'check')} and "
            f"{_plural(sub['fund'].nunique(), 'fund')}. Most frequent: {listed}.",
            "Correct at source and rerun. Every item, with its Excel row, is listed in dq_report.xlsx.",
        ))

    # Fund coverage against the GP reference: named funds, so managers can be chased directly.
    for status, title, action in [
        (MISSING, "{} in the GP reference {} missing from the extract",
         "Ask the GPs listed to submit these funds, or remove funds that have closed from the reference."),
        (UNLISTED, "{} in the extract {} not in the GP reference",
         "Add these funds to the GP reference (or correct the name) so they are attributed to a GP."),
    ]:
        sub = fc[fc["status"] == status]
        if len(sub):
            listed = "; ".join(f"{gp}: {', '.join(g['fund'])}" for gp, g in sub.groupby("gp", sort=True))
            out.append(Finding(title.format(_plural(len(sub), "fund"), "is" if len(sub) == 1 else "are"),
                               "bad", "Fund coverage", f"{listed}.", action))

    # Datapoints some funds do not report, or report with no value. Expected to happen, so info.
    gaps = datapoint_gaps(df, rules, reference)
    missing = gaps[gaps["gap"].isin([NOT_REPORTED, NO_VALUE, PARTIAL])]
    if len(missing):
        per_dp = missing.groupby("datapoint", sort=False)["fund"].nunique().sort_values(
            ascending=False, kind="stable")
        listed = ", ".join(f"{dp} ({_plural(int(n), 'fund')})" for dp, n in per_dp.head(TOP_CHECKS).items())
        more = f", and {len(per_dp) - TOP_CHECKS} more" if len(per_dp) > TOP_CHECKS else ""
        out.append(Finding(
            f"{_plural(len(per_dp), 'datapoint')} {'is' if len(per_dp) == 1 else 'are'} "
            "missing for some funds",
            "info", "Data gaps",
            f"{_plural(missing['fund'].nunique(), 'fund')} do not report a datapoint or report it "
            "with no value "
            f"(blank, null or NA) in some or all records: {listed}{more}. "
            "Not every fund is expected to report everything.",
            "Ask managers for the missing values or confirm they do not apply; the funds are listed by "
            "datapoint below and in the Datapoint Gaps sheet.",
        ))

    # Datapoints a fund reports, but only ever as zero.
    dc = datapoint_completeness(df, rules)
    zero_funds = fc[(fc["zero_datapoints"] != "") & (fc["status"] != MISSING)]
    zero_dps = dc.set_index("datapoint")["funds_zero_only"]
    zero_dps = zero_dps[zero_dps > 0].sort_values(ascending=False, kind="stable")
    if len(zero_funds):
        listed = ", ".join(f"{dp} ({_plural(int(n), 'fund')})" for dp, n in zero_dps.items())
        out.append(Finding(
            f"{_plural(len(zero_funds), 'fund')} report some datapoints only as zero", "info",
            "Confirm meaning",
            f"Reported as zero in every record: {listed}. A zero can be genuine (no development, no carry "
            "earned yet, no calls planned) or a placeholder for 'not reported'.",
            "Ask managers to confirm that zero means none for these datapoints.",
        ))

    # Funds past their stated term.
    end = _date_col(w, END_TERM)
    past = end < report
    if past.any():
        months = ((report - end[past]).dt.days / DAYS_PER_MONTH).round()
        calls = _any_positive(df, CALLS).reindex(w.index, fill_value=False)
        dists = _any_positive(df, DISTS).reindex(w.index, fill_value=False)
        n = int(past.sum())
        out.append(Finding(
            f"{_plural(n, 'fund')} {'is' if n == 1 else 'are'} past the stated end of fund term", "warn",
            "Confirm with managers",
            f"Term ended {int(months.min())} to {int(months.max())} months before the reporting date "
            f"(median {months.median() / 12:.1f} years). {int((past & dists).sum())} still project "
            f"distributions and {int((past & calls).sum())} project new capital calls. "
            "Term extensions may not have been updated in the data.",
            "Ask managers to confirm extensions and updated term dates.",
        ))

    # Capital calls after the investment period.
    ip_ended = _date_col(w, END_IP) < report
    calls = _any_positive(df, CALLS).reindex(w.index, fill_value=False)
    late = ip_ended & calls
    if late.any():
        out.append(Finding(
            f"{_plural(int(late.sum()), 'fund')} project capital calls after the investment period",
            "info", "Check plausibility",
            f"Of {int(ip_ended.sum())} funds whose investment period has ended, {int(late.sum())} still "
            "project capital calls. These can be legitimate (follow-on capex, fees, debt paydown) but are "
            "worth confirming.",
            "Ask the funds calling capital after their investment period what the calls are for.",
        ))

    # Projection calendars.
    freq = _projection_frequency(df, rules)
    if len(freq) > 1:
        parts = [f"{_plural(n, 'fund')} {name} ({len(dates)} periods, {dates[0]:%b %Y} to {dates[-1]:%b %Y})"
                 for name, (n, dates) in freq.items()]
        unmatched = w.index.size - sum(n for n, _ in freq.values())
        if unmatched > 0:
            parts.append(f"{_plural(unmatched, 'fund')} matching no calendar (see series_gap)")
        # A calendar that skips its first period(s) leaves a gap right after the reporting date.
        # Own name: `gaps` above is the datapoint-gaps DataFrame.
        skip_notes: list[str] = []
        for name, spec in rules["projection_calendars"].items():
            if spec.get("skip") and name in freq:
                skipped = calendar(report, {**spec, "periods": spec["skip"], "skip": 0})
                skip_notes.append(f" The {name} calendar skips {', '.join(f'{d:%B %Y}' for d in skipped)}.")
        out.append(Finding(
            "Cash-flow projections use different calendars", "info", "Timing differs",
            "; ".join(parts) + "." + "".join(skip_notes),
            "Compare funds' projections by quarter, and confirm any skipped month is by design.",
        ))

    # Empty source fields.
    empty = _empty_source_fields(raw)
    if empty:
        key = [f for f in ("Scenario", "Version") if f in empty]
        why = (f" Without {' and '.join(key)}, actuals, budgets and forecasts cannot be told apart."
               if key else "")
        out.append(Finding(
            f"{_plural(len(empty), 'source field')} {'is' if len(empty) == 1 else 'are'} never filled",
            "info", "Limits interpretation",
            f"Empty in every record: {', '.join(empty)}.{why}",
            "Ask for Scenario (at least) to be filled on projection records."
            if key else "Confirm the fields are intentionally unused.",
        ))

    # Labelling caveats.
    pattern = cfg["anonymised_label_pattern"]
    countries = df.loc[df["datapoint"] == COUNTRY, "value_str"].dropna()
    anon = bool(pattern and len(countries) and countries.str.match(pattern).all())
    naming = bool((issues["check"] == "naming_note").any())
    if anon or naming:
        parts = []
        if anon:
            parts.append(f"Countries are anonymised ({countries.nunique()} labels such as "
                         f"\"{sorted(countries.unique())[0]}\"), so exposure can only be shown by region.")
        if naming:
            parts.append("\"Region Level 1\" holds the sub-region and \"Region Level 2\" the continent, "
                         "the reverse of the sector levels.")
            # Only claim consistency when the hierarchy checks found nothing.
            if not issues["check"].isin(["hierarchy_conflict", "region_label_mismatch"]).any():
                parts.append("The checks found the mapping consistent across funds.")
        out.append(Finding(
            "Geography labels need care", "info", "Caveat", " ".join(parts),
            "Request the country key if country views are needed; label levels as Sub-region and Continent.",
        ))

    # Exact agreement suggests derived figures.
    exact = []
    if _exact_debt(df, w, cfg):
        exact.append("scheduled debt maturities equal LTV × GAV")
    if _exact_occupancy_share(df, w, cfg):
        exact.append("the GAV share with occupancy data matches the completed-assets share "
                     "to within 0.01 points")
    if exact:
        out.append(Finding(
            "Consistency is unusually exact", "info", "Verify at source",
            f"For all {len(w)} funds, {' and '.join(exact)}. "
            "That suggests some figures are derived from others. "
            "The checks confirm the data agrees with itself, not that it matches managers' reports.",
            f"Reconcile a sample of {cfg['reconcile_sample']} funds against quarterly manager reports.",
        ))

    return sorted(out, key=lambda f: TONE_ORDER[f.tone])


# ---------------------------------------------------------------- assembly


GROUP_TEXT = {
    "structure": "Expected columns, one reporting date, fund count, no unknown or missing datapoints.",
    "completeness": "Every fund has each item once, full debt and projection calendars, exposure members.",
    "uniqueness": "No duplicate records for the same fund, item, date and dimensions.",
    "validity": "Currency codes, percentages within 0–100%, no negative amounts, plausible IRRs, labels.",
    "consistency": "Mixes sum to 100%, sector and region hierarchies agree, occupancy matches sectors.",
    "business": "NAV ≤ GAV, LTV within cap, lifecycle split, debt = LTV × GAV, fees, bad debts, dates.",
    "outlier": "No fund outside the normal cross-fund range on currency-free metrics.",
    "reference": "GP reference rows complete, each fund listed once, every expected fund reported "
                 "and mapped.",
}


def _check_groups(summary: pd.DataFrame) -> pd.DataFrame:
    """Per check group: number of checks, PASS / INFO / FLAG counts and issues."""
    order = list(dict.fromkeys(CHECK_GROUPS.values()))
    g = summary.assign(n=1).pivot_table(index="group", columns="status", values="n", aggfunc="sum",
                                        fill_value=0)
    g = g.reindex(index=order, columns=["PASS", "INFO", "FLAG"], fill_value=0)
    g["checks"] = g.sum(axis=1)
    g["issues"] = summary.groupby("group")["issues"].sum().reindex(order, fill_value=0)
    g["description"] = [GROUP_TEXT.get(x, "") for x in g.index]
    return g.reset_index()


def build_report(raw: pd.DataFrame, df: pd.DataFrame, issues: pd.DataFrame, summary: pd.DataFrame,
                 rules: Rules, reference: pd.DataFrame, period_label: str, source_name: str) -> ReportData:
    """Assemble all report content for one quarter's extract and its GP `reference`."""
    report = pd.Timestamp(rules["reporting_date"])
    counts = {s: int((issues["severity"] == s).sum()) for s in ["error", "warning", "info"]}
    register = datapoint_register(df, raw, issues, rules)
    fc = fund_completeness(df, rules, reference)
    gc = gp_completeness(fc, issues)
    found = findings(df, raw, issues, rules, fc, reference)
    n_funds = df["fund"].nunique()
    catalogue = [dp for dps in rules["datapoints"].values() for dp in dps]
    # INFO checks are observations, not defects, so they count as passed.
    n_pass = int((summary["status"] != "FLAG").sum())
    n_ccy = df.groupby("fund")["currency"].first().nunique()
    dc = datapoint_completeness(df, rules)
    # Filled for every fund; a "none when missing" datapoint counts, since no value there means none.
    missing_per_dp = dc["funds_not_reported"] + dc["funds_no_value"] + dc["funds_partial"]
    full = int((missing_per_dp == 0).sum()) if n_funds else 0
    has_gaps = bool(missing_per_dp.sum())
    state = ("Consistent, with some datapoints missing for some funds" if has_gaps
             else "Complete and consistent")
    # Fund coverage against the reference; funds not in the reference are reported but not expected.
    n_expected = int((fc["status"] != UNLISTED).sum())
    n_expected_reported = int((fc["status"] == REPORTED).sum())
    n_unlisted = int((fc["status"] == UNLISTED).sum())
    n_all_valued = int((fc["datapoints_with_value"] == fc["datapoints_total"]).sum())

    blockers = [f.title.lower() for f in found if f.tone in ("bad", "warn")]
    if counts["error"]:
        tone = "bad"
        verdict = f"Not ready: {_plural(counts['error'], 'error')} must be fixed before the data is used."
    elif counts["warning"]:
        tone = "warn"
        verdict = f"Usable with caveats: {_plural(counts['warning'], 'warning')} need review."
    elif blockers:
        # Clean checks, but some findings need confirming before the affected funds are relied on.
        tone = "warn"
        points = f"{_plural(len(blockers), 'point')} to confirm with managers"
        verdict = (f"Consistent, with {points} and some datapoints missing for some funds." if has_gaps
                   else f"Complete and consistent, with {points}.")
    else:
        tone = "good"
        verdict = (f"{state}. Fit for fund-level reporting and comparison"
                   + (" where reported." if has_gaps else "."))
    detail = (f"{n_pass} of {len(summary)} automated checks passed, with {_plural(counts['error'], 'error')} "
              f"and {_plural(counts['warning'], 'warning')}. {full} of {len(catalogue)} datapoints are "
              f"filled for all {n_funds} funds.")
    if blockers:
        detail += " To confirm: " + "; ".join(blockers) + "."

    flagged = summary[summary["status"] != "PASS"].copy()
    first_detail = issues.groupby("check", observed=True)["detail"].first()
    flagged["example"] = flagged["check"].map(first_detail).fillna("").map(
        lambda s: s if len(s) <= MAX_EXAMPLE_CHARS else s[: MAX_EXAMPLE_CHARS - 1] + "…")
    errors = issues[issues["severity"] == "error"]
    unassigned = int(errors["datapoint"].isna().sum())

    figures = [
        (f"{len(df):,}", "records reviewed"),
        (f"{reference['gp'].dropna().nunique()}", "GPs in the reference"),
        (f"{n_expected_reported} / {n_expected}", "expected funds reported"
         + (f" (+{n_unlisted} not in the reference)" if n_unlisted else "")),
        (f"{len(catalogue)}", f"datapoints in {len(rules['datapoints'])} categories"),
        (f"{n_pass} / {len(summary)}", "checks passed"),
        (f"{n_ccy}", "currencies, one per fund"),
        (f"{n_all_valued} / {n_funds}", f"funds with a value in all {len(catalogue)} datapoints"),
    ]
    return ReportData(period_label, report, source_name, tone, verdict, detail, figures, register, found,
                      _check_groups(summary), flagged, counts, unassigned, gc, fc, dc,
                      datapoint_gaps(df, rules, reference),
                      int(rules["report"]["completeness_top_n"]))


def ratings_count(rows: list[DatapointUsability]) -> dict[str, int]:
    """Return the number of register rows per usability rating, in display order."""
    return {r: sum(x.rating == r for x in rows) for r in RATINGS}


def slug(text: str) -> str:
    """Lower-case token safe for HTML attributes."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
