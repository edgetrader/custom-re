"""Assemble the management report for one quarter: a one-line summary and the three steps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from custom_re.steps import (
    CLASSES,
    MISSING_FROM_EXTRACT,
    NOT_IN_REFERENCE,
    completeness,
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
    counts: dict[str, int]  # issues by severity
    usability_thresholds: dict[str, float]  # the rules' `usability` section, stated on the page


def plural(n: int, word: str, many: str | None = None) -> str:
    """Return "1 fund" / "2 funds"."""
    return f"{n} {word if n == 1 else (many or word + 's')}"


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
    rated = {r: int((usab["rating"] == r).sum()) for r in CLASSES}
    summary = (f"{plural(n_ext, 'fund')} in the extract · {n_unlisted} not in the reference · "
               f"{n_missing} reference {'fund' if n_missing == 1 else 'funds'} missing · "
               f"{n_top} of {n_ext} {top} complete · ")
    rated_text = ", ".join(f"{rated[c]} {c.lower()}" for c in CLASSES)
    summary += (f"datapoints: {rated_text}" if len(usab)
                else f"no fund is {top} complete, so no datapoint was assessed")
    counts = {s: int((issues["severity"] == s).sum()) for s in ("error", "warning", "info")}
    return ReportData(period_label, pd.Timestamp(rules["reporting_date"]), source_name, summary, ref, comp,
                      bands, usab, detail, counts, dict(rules["usability"]))
