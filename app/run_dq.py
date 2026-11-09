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

import pandas as pd  # noqa: E402

from custom_re.checks import run_all, summarise  # noqa: E402
from custom_re.html_report import write_html  # noqa: E402
from custom_re.insights import ReportData, build_report  # noqa: E402
from custom_re.loader import load_rules, read_raw, tidy  # noqa: E402
from custom_re.reference import add_gp, gp_map, read_reference  # noqa: E402
from custom_re.report import write_report  # noqa: E402
from custom_re.settings import load_settings, parse_period  # noqa: E402

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
    checks = summarise(issues)
    out_dir = settings.period_output_dir(period)
    report = build_report(df, issues, rules, reference, period.label, src.name)
    write_report(out_dir / REPORT_NAME, workbook_sheets(report, add_gp(issues, reference), checks))
    # The HTML page is presentation: a bug there must not hide the Excel results or look like a
    # data-quality failure, so it is isolated here at the app boundary.
    html_ok = True
    try:
        write_html(out_dir / HTML_NAME, report)
    except Exception as exc:  # noqa: BLE001
        html_ok = False
        traceback.print_exc()
        print(f"HTML report failed: {exc!r}", file=sys.stderr)

    counts = report.counts
    print(f"Period: {period.label}  Input: {src}  GP reference: {ref_path}")
    print(f"Rows: {len(df):,}  Issues -> errors: {counts['error']}  warnings: {counts['warning']}  "
          f"info: {counts['info']}")
    print(report.summary)
    written = [REPORT_NAME] + ([HTML_NAME] if html_ok else [])
    print(f"Reports written to {out_dir}: {', '.join(written)}")
    if counts["error"]:
        return 1
    return 0 if html_ok else 3


def _flat(usab: pd.DataFrame) -> pd.DataFrame:
    """Usability table with its list columns as text for Excel ("; " between funds, " | " between reasons)."""
    out = usab.copy()
    for col in ("usable_funds", "caution_funds", "unusable_funds", "no_value_funds"):
        out[col] = out[col].map("; ".join)
    for col in ("caution", "unusable"):
        out[col] = out[col].map(lambda groups: " | ".join(f"{why}: {'; '.join(f)}" for why, f in groups))
    return out


def workbook_sheets(
    report: ReportData, issues: pd.DataFrame, checks: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Build the workbook's sheets in order: summary, the three steps, then every check result."""
    summary = pd.DataFrame(
        [("Period", report.period_label), ("Source", report.source_name),
         ("Reporting date", f"{report.reporting_date:%Y-%m-%d}"), ("Summary", report.summary),
         *((f"Funds {b} complete", int((report.completeness["band"] == b).sum())) for b in report.bands),
         *((f"Issues: {k}", v) for k, v in report.counts.items())],
        columns=["item", "value"])
    return {
        "Summary": summary,
        "1 Reference Check": report.reference,
        "2 Completeness": report.completeness,
        "3 Datapoint Usability": _flat(report.usability),
        "3 Fund Detail": report.detail,
        "Issues": issues,
        "Checks": checks,
    }


if __name__ == "__main__":
    sys.exit(main())
