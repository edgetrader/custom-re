"""Assemble the management report for one quarter: a one-line summary, the three steps and the actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from custom_re.steps import (
    CAUTION,
    GOOD,
    MISSING_FROM_EXTRACT,
    NOT_IN_REFERENCE,
    UNUSABLE,
    completeness,
    plain,
    reference_check,
    usability,
)

Rules = dict[str, Any]


@dataclass
class ReportData:
    """Everything the report shows for one quarter."""

    period_label: str
    reporting_date: pd.Timestamp
    source_name: str
    summary: str  # the one-line headline
    reference: pd.DataFrame  # step 1 (steps.reference_check)
    completeness: pd.DataFrame  # step 2 (steps.completeness)
    bands: list[str]  # band labels, best first; step 3 covers the first
    usability: pd.DataFrame  # step 3, one row per datapoint
    detail: pd.DataFrame  # step 3, one row per fund and datapoint (workbook only)
    actions: list[str]
    counts: dict[str, int]  # issues by severity


def reasons_text(groups: list[tuple[str, list[str]]]) -> str:
    """Return "reason: Fund A, Fund B | other reason: Fund C" for (reason, [funds]) pairs."""
    return " | ".join(f"{why}: {', '.join(funds)}" for why, funds in groups)


def plural(n: int, word: str, many: str | None = None) -> str:
    """Return "1 fund" / "2 funds"."""
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _by_gp(rows: pd.DataFrame) -> str:
    """Return "GP A: Fund 1, Fund 2; GP B: Fund 3" for rows with gp and fund columns."""
    return "; ".join(f"{gp}: {', '.join(g['fund'])}" for gp, g in rows.groupby("gp", sort=True))


def _actions(ref: pd.DataFrame, comp: pd.DataFrame, usab: pd.DataFrame, issues: pd.DataFrame,
             top: str) -> list[str]:
    """Short list of what to ask GPs to fix or supply, in step order; empty when nothing is needed."""
    out = []
    unlisted = ref[ref["status"] == NOT_IN_REFERENCE]
    if len(unlisted):
        out.append(f"Add {plural(len(unlisted), 'fund')} to the GP reference, or correct the name: "
                   f"{', '.join(unlisted['fund'])}.")
    missing = ref[ref["status"] == MISSING_FROM_EXTRACT]
    if len(missing):
        out.append(f"Ask GPs for {plural(len(missing), 'fund')} missing from the extract: {_by_gp(missing)}.")
    gaps = comp[comp["missing_datapoints"] != ""]
    if len(gaps):
        below = comp[comp["band"] != top]
        extra = (f" {plural(len(below), 'fund')} below {top} {'is' if len(below) == 1 else 'are'} not "
                 "assessed in step 3 until completed." if len(below) else "")
        out.append(f"Ask GPs to supply the missing datapoints of {plural(len(gaps), 'fund')} (listed in "
                   f"step 2).{extra}")
    for verb, col in [("Correct at source", "unusable"), ("Review", "caution")]:
        rows = usab[usab[col].map(len) > 0]
        if len(rows):
            items = "; ".join(f"{r['datapoint']} ({reasons_text(r[col])})" for r in rows.to_dict("records"))
            out.append(f"{verb}: {items}.")
    other = _unshown_errors(issues, comp, usab, top)
    if len(other):
        why = ", ".join(sorted({plain(str(c)) for c in other["check"]}))
        out.append(f"Fix {plural(len(other), 'other error')} not shown above ({why}); see the Issues sheet.")
    return out


# Errors that step 1 already explains by fund name.
STEP1_CHECKS = {"fund_not_in_reference", "fund_missing_from_extract", "fund_count"}


def _unshown_errors(issues: pd.DataFrame, comp: pd.DataFrame, usab: pd.DataFrame, top: str) -> pd.DataFrame:
    """Errors the three steps do not show: on funds outside step 3, unknown datapoints, the reference itself.

    So the actions never read "none" while the run has errors.
    """
    errs = issues[(issues["severity"] == "error") & ~issues["check"].isin(STEP1_CHECKS)]
    assessed = set(comp.loc[comp["band"] == top, "fund"])
    catalogued = set(usab["datapoint"])
    # Shown in step 3: on an assessed fund (any or no datapoint), or on a catalogued datapoint across funds.
    shown = ((errs["fund"].isin(assessed) & (errs["datapoint"].isin(catalogued) | errs["datapoint"].isna()))
             | (errs["fund"].isna() & errs["datapoint"].isin(catalogued)))
    gap_checks = {"datapoint_not_reported", "value_missing"}
    return errs[~shown & ~errs["check"].isin(gap_checks)]


def build_report(df: pd.DataFrame, issues: pd.DataFrame, rules: Rules, reference: pd.DataFrame,
                 period_label: str, source_name: str) -> ReportData:
    """Run the three steps on one quarter's extract and its GP reference, and assemble the report."""
    ref = reference_check(df, reference)
    comp = completeness(df, rules, reference)
    usab, detail = usability(df, rules, issues, comp)
    bands = [str(b["label"]) for b in rules["completeness_bands"]]
    top = bands[0]

    n_ext = int((ref["status"] != MISSING_FROM_EXTRACT).sum())
    n_unlisted = int((ref["status"] == NOT_IN_REFERENCE).sum())
    n_missing = int((ref["status"] == MISSING_FROM_EXTRACT).sum())
    n_top = int((comp["band"] == top).sum())
    rated = {r: int((usab["rating"] == r).sum()) for r in (GOOD, CAUTION, UNUSABLE)}
    summary = (f"{plural(n_ext, 'fund')} in the extract · {n_unlisted} not in the reference · "
               f"{n_missing} reference {'fund' if n_missing == 1 else 'funds'} missing · "
               f"{n_top} of {n_ext} {top} complete · ")
    summary += (f"{plural(rated[GOOD], 'datapoint')} good, {rated[CAUTION]} use with caution, "
                f"{rated[UNUSABLE]} unusable" if len(usab) else f"no fund is {top} complete, so no datapoint "
                "was assessed")
    counts = {s: int((issues["severity"] == s).sum()) for s in ("error", "warning", "info")}
    return ReportData(period_label, pd.Timestamp(rules["reporting_date"]), source_name, summary, ref, comp,
                      bands, usab, detail, _actions(ref, comp, usab, issues, top), counts)
