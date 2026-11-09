"""Run all data quality checks on one quarter's KPI extract and write an Excel report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Run straight from a checkout: put src/ on the import path so custom_re does not need installing.
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from custom_re.checks import fund_metrics, run_all, summarise  # noqa: E402
from custom_re.loader import load_rules, read_raw, tidy  # noqa: E402
from custom_re.report import write_report  # noqa: E402
from custom_re.settings import load_settings, parse_period  # noqa: E402

DEFAULT_SETTINGS = ROOT / "config" / "settings.yaml"
REPORT_NAME = "dq_report.xlsx"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options: the period is required, file locations come from the settings file."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("period", help='quarter to check, e.g. "Q2 2026" or 2026Q2')
    p.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS, help="folder and file-name settings")
    p.add_argument("--input", type=Path,
                   help="use this extract instead of the one named in the settings; the period still sets "
                        "the expected reporting date and the output folder")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the checks and write the report.

    Returns 0 when clean, 1 if any error-severity issue is found, 2 for bad input.
    """
    args = parse_args(argv)
    try:
        period = parse_period(args.period)
        settings = load_settings(args.settings, ROOT)
        rules = load_rules(settings.rules_file)
        src = args.input or settings.input_path(period)
        if not src.is_file():
            raise ValueError(f"no extract for {period.label} at {src}")
        raw = read_raw(src)
        df = tidy(raw)
    except (ValueError, OSError) as exc:
        # Bad arguments, config or input file: report cleanly instead of a traceback.
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # The period is the single source of the reporting date (any value in the rules YAML is
    # overridden), so check_reporting_date catches an extract from the wrong quarter.
    rules["reporting_date"] = period.end.date().isoformat()

    # Checks run outside the try: run_all isolates their crashes as check_crashed issues.
    issues = run_all(raw, df, rules)
    summary = summarise(issues)
    out = settings.period_output_dir(period) / REPORT_NAME
    write_report(out, summary, issues, fund_metrics(df))

    counts = issues["severity"].value_counts().reindex(["error", "warning", "info"], fill_value=0)
    print(f"Period: {period.label}  Input: {src}")
    print(f"Rows: {len(df):,}  Funds: {df['fund'].nunique()}  Checks: {len(summary)}")
    print(f"Issues -> errors: {counts['error']}  warnings: {counts['warning']}  info: {counts['info']}")
    print(summary[summary["status"] != "PASS"].to_string(index=False))
    print(f"Report written to {out}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
