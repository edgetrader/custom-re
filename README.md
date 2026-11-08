# custom-re

Data quality checks (and, later, analysis) for the Custom RE quarterly KPI extract: a long-format Excel sheet of fund KPIs for a set of real estate funds, in several currencies, at one reporting date.

## Setup

Requires Python 3.11+ and pandas 3.0+. The editable install is required because `app/` imports the `custom_re` package.

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Usage

Run all checks and write an Excel report:

```
.venv/bin/python app/run_dq.py
.venv/bin/python app/run_dq.py --input data/<extract>.xlsx --rules config/dq_rules.yaml --output output/dq_report.xlsx
```

Defaults: input `data/Custom_RE_Extract_2026Q2.xlsx`, rules `config/dq_rules.yaml`, output `output/dq_report.xlsx`. The extract is read from its `KPI` sheet. The input data is not part of the repository.

The report workbook has three sheets: Summary (one row per check with PASS / INFO / FLAG), Issues (one row per finding with severity, fund, datapoint and Excel row) and Fund Metrics. The process exits with code 1 if any error-severity issue is found.

## Checks

The checks live in `src/custom_re/checks.py`; `CHECK_GROUPS` is the registry of all 45 check names. They are grouped as:

- structure: reporting date, blank keys, fund count, unknown or missing datapoints
- completeness: per-fund datapoint counts, time-series gaps, projection frequency, exposure members, value field used
- uniqueness: duplicate keys
- validity: currencies, fractions in [0, 1], non-negative amounts, plausible IRR, allowed labels
- consistency: mixes sum to 100%, sector roll-ups, region hierarchy, GAV-weighted occupancy
- business: GAV/NAV/LTV relationships, lifecycle split, debt maturities vs LTV x GAV, fees, bad debts, date order
- outlier: cross-fund IQR outliers

A check that crashes is reported as `check_crashed` and does not stop the others.

## Configuration

`config/dq_rules.yaml` holds the datapoint catalogue (with the expected value field: num, date or str), tolerances, allowed vocabularies, plausibility bounds, `expected_fund_count` and the forward-looking calendars.

## Rolling to a new quarter

1. Update `reporting_date` in `config/dq_rules.yaml`. The debt maturity and projection calendars are derived from it.
2. Review `projection_calendars` (`freq`, `periods`, `skip`) and `debt_maturity_calendar` against the new extract, and `expected_fund_count`.
3. Pass the new file with `--input`, or change the default in `app/run_dq.py`.

## Tests and lint

```
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy src app
```

The tests use a synthetic two-fund fixture with fault injection.

## Project layout

```
src/custom_re/
  loader.py     read and tidy the extract, fund_info_wide
  checks.py     named checks, run_all, summarise, fund_metrics
  report.py     Excel report writer
app/run_dq.py   command-line entry point
config/         dq_rules.yaml
tests/          pytest suite
notebooks/      exploration
docs/
data/           local only
output/         local only
```

## Status

Data quality checks are complete. Phase 2 (analysis) is pending: it needs FX rates to a base currency and decisions on tolerances and AIA-weighting.
