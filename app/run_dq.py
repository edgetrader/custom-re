"""Run all data quality checks on one quarter's KPI extract; write the Excel and HTML reports."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Run straight from a checkout: put src/ on the import path so custom_re does not need installing.
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from custom_re.checks import fund_metrics, run_all, summarise  # noqa: E402
from custom_re.completeness import (  # noqa: E402
    MISSING,
    UNLISTED,
    datapoint_completeness,
    datapoint_gaps,
    fund_completeness,
    gp_completeness,
)
from custom_re.html_report import write_html  # noqa: E402
from custom_re.insights import build_report  # noqa: E402
from custom_re.loader import load_rules, read_raw, tidy  # noqa: E402
from custom_re.reference import add_gp, gp_map, read_reference  # noqa: E402
from custom_re.report import write_report  # noqa: E402
from custom_re.settings import load_settings, parse_period  # noqa: E402
from custom_re.stages import NOT_ASSESSED, add_tiers, usability, usability_by_datapoint  # noqa: E402

DEFAULT_SETTINGS = ROOT / "config" / "settings.yaml"
REPORT_NAME = "dq_report.xlsx"
HTML_NAME = "dq_management_report.html"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options: the period is required, file locations come from the settings file."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("period", help='quarter to check, e.g. "Q2 2026" or 2026Q2')
    p.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS, help="folder and file-name settings")
    p.add_argument("--input", type=Path,
                   help="use this extract instead of the one named in the settings; the period still sets "
                        "the expected reporting date and the output folder")
    p.add_argument("--reference", type=Path,
                   help="use this GP reference workbook instead of the one named in the settings")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the checks and write both reports.

    Returns 0 when clean, 1 if any error-severity issue is found, 2 for bad input, and 3 when the data
    is clean but the HTML report could not be written.
    """
    args = parse_args(argv)
    try:
        period = parse_period(args.period)
        settings = load_settings(args.settings, ROOT)
        rules = load_rules(settings.rules_file)
        src = args.input or settings.input_path(period)
        if not src.is_file():
            raise ValueError(f"no extract for {period.label} at {src}")
        ref_path = args.reference or settings.reference_file
        if not ref_path.is_file():
            raise ValueError(f"no GP reference at {ref_path}")
        reference = read_reference(ref_path)
        raw = read_raw(src)
        df = tidy(raw)
    except (ValueError, OSError) as exc:
        # Bad arguments, config or input file: report cleanly instead of a traceback.
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # The period is the single source of the reporting date (any value in the rules YAML is
    # overridden), so check_reporting_date catches an extract from the wrong quarter.
    rules["reporting_date"] = period.end.date().isoformat()
    # Likewise the GP reference is the single list of expected funds.
    rules["expected_fund_count"] = len(gp_map(reference))

    # Checks run outside the try: run_all isolates their crashes as check_crashed issues.
    issues = run_all(raw, df, rules, reference)
    summary = summarise(issues)
    out_dir = settings.period_output_dir(period)
    # Stage 1 tiers funds by completeness; stage 2 rates the datapoints of the analysable ones.
    fc = add_tiers(fund_completeness(df, rules, reference), df, rules, issues)
    gc, dc = gp_completeness(fc, issues), datapoint_completeness(df, rules)
    u = usability(df, rules, issues, fc)
    grid = u[u["rating"] != NOT_ASSESSED].pivot(index=["gp", "fund"], columns="datapoint", values="rating")
    grid = grid.reindex(columns=list(dict.fromkeys(u["datapoint"]))).reset_index()
    # GP first, then fund: the workbook's fund-level sheets carry the GP for filtering.
    metrics = add_gp(fund_metrics(df, rules["none_when_missing"]).reset_index(), reference)
    write_report(out_dir / REPORT_NAME, summary, add_gp(issues, reference),
                 metrics.set_index(["gp", "fund"]),
                 {"S1 GP Tiers": gc, "S1 Fund Tiers": fc, "S1 Datapoint Completeness": dc,
                  "S1 Datapoint Gaps": datapoint_gaps(df, rules, reference),
                  "S2 By Datapoint": usability_by_datapoint(u), "S2 Usability Grid": grid,
                  "S2 Usability": u})
    # The HTML report is presentation: a bug there must not hide the Excel results or look like a
    # data-quality failure, so it is isolated here at the app boundary.
    html_ok = True
    try:
        report = build_report(raw, df, issues, summary, rules, reference, period.label, src.name)
        write_html(out_dir / HTML_NAME, report)
    except Exception as exc:  # noqa: BLE001
        html_ok = False
        traceback.print_exc()
        print(f"HTML report failed: {exc!r}", file=sys.stderr)

    counts = issues["severity"].value_counts().reindex(["error", "warning", "info"], fill_value=0)
    print(f"Period: {period.label}  Input: {src}  GP reference: {ref_path}")
    print(f"Rows: {len(df):,}  Funds: {df['fund'].nunique()}  Checks: {len(summary)}")
    print(f"Issues -> errors: {counts['error']}  warnings: {counts['warning']}  info: {counts['info']}")
    print(summary[summary["status"] != "PASS"].to_string(index=False))
    # Completeness of what was submitted; funds missing from the extract are counted on the GP line.
    sub = fc[fc["status"] != MISSING]
    if len(sub):
        total = int(sub["datapoints_total"].iloc[0])
        full = int((sub["datapoints_with_value"] == total).sum())
        print(f"Completeness: {full} of {len(sub)} funds have a "
              f"non-zero value in all {total} datapoints; lowest {int(sub['datapoints_with_value'].min())}.")
    if len(fc):
        tiers = fc["tier"].value_counts().reindex([1, 2, 3, 4], fill_value=0)
        rated = u[u["rating"] != NOT_ASSESSED]["rating"].value_counts()
        print(f"Stage 1 tiers (1-4): {' / '.join(str(int(n)) for n in tiers)}  "
              f"analysable: {int(fc['analysable'].sum())} of {len(fc)} funds")
        print("Stage 2 ratings: " + ", ".join(f"{k} {int(v)}" for k, v in rated.items()))
    if len(gc):
        print(f"GPs: {len(gc)}  Funds expected: {int(gc['funds_expected'].sum())}  "
              f"missing from extract: {int(gc['funds_missing'].sum())}  "
              f"not in reference: {int((fc['status'] == UNLISTED).sum())}")
    written = [REPORT_NAME] + ([HTML_NAME] if html_ok else [])
    print(f"Reports written to {out_dir}: {', '.join(written)}")
    if counts["error"]:
        return 1
    return 0 if html_ok else 3


if __name__ == "__main__":
    sys.exit(main())
