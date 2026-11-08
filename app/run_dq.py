"""Run all data quality checks on a KPI extract and write an Excel report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from custom_re.checks import fund_metrics, run_all, summarise
from custom_re.loader import load_rules, read_raw, tidy
from custom_re.report import write_report

ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options; defaults point at the project's data, config and output folders."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=ROOT / "data" / "Custom_RE_Extract_2026Q2.xlsx")
    p.add_argument("--rules", type=Path, default=ROOT / "config" / "dq_rules.yaml")
    p.add_argument("--output", type=Path, default=ROOT / "output" / "dq_report.xlsx")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the checks and write the report; exit code 1 if any error-severity issue is found."""
    args = parse_args(argv)
    rules = load_rules(args.rules)
    raw = read_raw(args.input)
    df = tidy(raw)

    issues = run_all(raw, df, rules)
    summary = summarise(issues)
    write_report(args.output, summary, issues, fund_metrics(df))

    counts = issues["severity"].value_counts().reindex(["error", "warning", "info"], fill_value=0)
    print(f"Rows: {len(df):,}  Funds: {df['fund'].nunique()}  Checks: {len(summary)}")
    print(f"Issues -> errors: {counts['error']}  warnings: {counts['warning']}  info: {counts['info']}")
    print(summary[summary["status"] != "PASS"].to_string(index=False))
    print(f"Report written to {args.output}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
