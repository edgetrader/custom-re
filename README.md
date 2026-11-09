# custom-re

Data quality checks (and, later, analysis) for the Custom RE quarterly KPI extract: a long-format Excel sheet of fund KPIs for a set of real estate funds, at one reporting date. Each fund reports in its own currency (different funds use different currencies); totals across funds are not a goal, so funds are compared by ratios.

## Setup

Requires Python 3.11+ and pandas 3.0+. The `custom_re` package is not installed: `app/run_dq.py` and the tests load it straight from `src/`. Activate the Python environment you want to use and install only the third-party dependencies:

```
pip install -r requirements.txt        # run only
pip install -r requirements-dev.txt    # plus pytest, ruff and mypy
```

## Usage

Run all checks for a quarter and write an Excel report and an HTML management report. The period is required; it can be written as `"Q2 2026"`, `2026Q2`, `"2026 Q2"` or `Q2-2026` (case-insensitive).

```
python app/run_dq.py "Q2 2026"
python app/run_dq.py 2026Q2 --input data/<extract>.xlsx --reference data/<reference>.xlsx --settings config/settings.yaml
```

The extract is looked up as `<data_dir>/<input_pattern>` (by default `data/Custom_RE_Extract_2026Q2.xlsx`) and read from its `KPI` sheet. The GP reference workbook (sheet `Fund Name`, columns `GP Name` and `Fund Name`) is the single list of expected funds and the GP of each; it is read from `reference_file` in the settings. `--input` and `--reference` override the files only; the period still sets the expected reporting date (the quarter end) and the output folder. `--settings` points at another settings file (default `config/settings.yaml`). The input data and the reference are not part of the repository.

Two files are written to `output/<period code>/`, e.g. `output/2026Q2/`:

The report has three steps, all per fund (amounts in different currencies are never combined):

1. Reference check: funds in the extract that are not in the GP reference, and reference funds missing from the extract. Unlisted funds still go through steps 2 and 3 (GP shown as "(not in reference)"). A fund listed with a blank GP counts as listed.
2. Completeness: for each fund, how many of the 36 datapoints have a value, placed in a band (>90%, >75%, >50%, 50% or less) with the missing datapoints listed. A zero counts as a value, and so does a blank or missing Bad debts (no bad debt). For the scheduled amounts (Debt maturity value, Capital call amount, Distribution amount) a blank, NA or zero amount on a schedule date means nothing falls on that date, so it is a value; a fund with no rows for one of them is still a gap (not reported).
3. Datapoint usability, for the funds in the first band (>90%): for each of the 36 datapoints, how many of those funds have a value (count and %), a rating of Good, Use with caution or Unusable (worst case across funds), the funds it is usable for, why their values are usable (what they were validated against, in plain words), and each problem fund with its reason in plain words.

- `dq_report.xlsx` has seven sheets: Summary, 1 Reference Check, 2 Completeness, 3 Datapoint Usability, 3 Fund Detail (one row per fund and datapoint, with reasons and notes), Issues (one row per finding with severity, GP, fund, datapoint and Excel row) and Checks (one row per check with PASS / INFO / FLAG). The console prints the one-line summary.
- `dq_management_report.html` is a self-contained management report generated from the data (no external requests, light and dark themes). It has a header (source, reporting date, error and warning counts), the one-line summary, Step 1, Step 2 (band counts, then one table per band, funds with gaps first), and Step 3 (the 36 datapoints by category: funds with value and %, rating, the funds it is usable for, and why).

Exit codes: 0 clean, 1 if any error-severity issue is found, 2 for a bad period, bad settings, or a missing or unreadable input or reference file, 3 if the data is clean but the HTML report failed (the Excel report is still written).

## Checks

The checks live in `src/custom_re/checks.py`; `CHECK_GROUPS` is the registry of all 47 check names. They are grouped as:

- structure: reporting date, blank keys, fund count, unknown datapoints, datapoints missing everywhere
- completeness: per-fund datapoint counts, datapoints not reported or without a value (info), time-series gaps for funds that report the series, projection frequency, exposure members, value field used
- uniqueness: duplicate keys
- validity: currencies, fractions in [0, 1], non-negative amounts, plausible IRR, allowed labels
- consistency: mixes sum to 100%, sector roll-ups, region hierarchy, GAV-weighted occupancy
- business: GAV/NAV/LTV relationships, lifecycle split, debt maturities vs LTV x GAV, fees, bad debts, date order
- outlier: cross-fund IQR outliers
- reference: incomplete or duplicate reference rows, funds in the extract but not in the reference, funds in the reference but missing from the extract

Label datapoints (Country, Region Level 1, Region Level 2, Sector - level 1, Sector - level 2) take their value from Value String. Their Analysis1-4 columns may hold other values, so they are neither required nor compared with the label. Labels are matched to the percentage rows by value: countries, sub-regions and continents against Country Percentage Analysis1, 2 and 3, and sectors against the sector percentage Analysis1. Region rows are not mapped country by country, since they carry no reliable country key; the region sets are compared instead (`region_label_mismatch` was removed). Analysis4 is loaded but must be blank outside label rows. Countries and sectors must be unique per fund by Value String; region labels may repeat, since several countries share a region.

Multiple currencies across funds are expected; only a fund reporting more than one currency (`mixed_currency`) or an invalid currency code is an issue. Funds with different projection calendars are reported as an info note.

### Steps and ratings

`src/custom_re/steps.py` holds the three steps (`reference_check`, `completeness`, `usability`). `src/custom_re/completeness.py` holds `datapoint_states`, which classifies each fund and datapoint as Has a value, None, Not reported, No value, No value in some records (partly blank series), Reported only as zero, or No amount on any scheduled date (a scheduled amount reported with every amount blank or zero). Only Not reported and No value count as gaps in step 2. The bands come from `completeness_bands` in `config/dq_rules.yaml`: a fund goes in the first band whose `above` its share strictly exceeds.

In step 3 a check error on a datapoint makes it Unusable, a warning makes it Use with caution, and anything else is Good. A fund-wide error (for example `mixed_currency`) makes every value of that fund Unusable. An error or warning on a datapoint across funds rates the datapoint itself ("across funds"). Zeros, partly blank series, past term end, capital calls after the investment period, anonymised country labels, region level naming, debt equal to LTV x GAV, outliers and other info findings are notes only (sheet 3 Fund Detail). A missing value names the fund but does not change the rating, since step 2 counts it. Reference and gap checks do not rate values. Reasons are written in plain words (`CHECK_TEXT` for problems, `why_usable` / `VALIDATIONS` for what a Good value was checked against); the Issues sheet keeps the check names and full detail. `src/custom_re/facts.py` holds the datapoint constants and helpers used for the notes.

Funds are not expected to populate every datapoint. A datapoint a fund does not report (`datapoint_not_reported`) or reports without a value (`value_missing`) is an info gap, not an error; the only exception is a missing Fund GAV, NAV or LTV, which raises a warning because the core rules cannot run (`rule_inputs_missing`). Text `NA`, `N/A`, `null`, `nan` or `none` (any case) in `Value Num` counts as no value; other text there is still a `non_numeric_value` error. Datapoints listed under `none_when_missing` in `config/dq_rules.yaml` (currently Bad debts) are different: not reported, blank, null/NA or zero all mean "none" (no bad debt), an answer rather than a gap. Datapoints under `scheduled_amounts` (Debt maturity value, Capital call amount, Distribution amount) have a date per row: a blank, null/NA or zero amount means no amount on that date, so it raises no `value_missing`, and counts as 0 when the debt schedule is compared with LTV x GAV. A fund with no rows for one of them is still `datapoint_not_reported`.

A check that crashes is reported as `check_crashed` and does not stop the others.

## Configuration

`config/settings.yaml` holds `data_dir`, `output_dir`, `rules_file` (relative paths resolve against the project root, absolute paths are used as is) and `input_pattern` (default `Custom_RE_Extract_{period}.xlsx`, where `{period}` is the period code such as `2026Q2`) and `reference_file` (required; relative or full path to the GP reference workbook).

`config/dq_rules.yaml` holds the datapoint catalogue (with the expected value field: num, date or str), tolerances, allowed vocabularies, `none_when_missing`, `scheduled_amounts`, plausibility bounds and the forward-looking calendars. Its `completeness_bands` list (`label`, `above`; the last has `above: null`) sets the step 2 bands. Its `report:` section (all keys required) holds `exact_rel_tol` and `anonymised_label_pattern`, used for the step 3 notes. It has no `reporting_date` or `expected_fund_count`: the CLI sets them to the quarter end of the period and to the number of funds in the GP reference.

## Rolling to a new quarter

1. Put the new extract in `data/`, named according to `input_pattern`.
2. Run `python app/run_dq.py "<period>"`. Running the wrong file for a period raises reporting_date errors.
3. Review `projection_calendars` (`freq`, `periods`, `skip`), and `debt_maturity_calendar` in `config/dq_rules.yaml` against the new extract.
4. Update the GP reference if funds were added or closed.

## Tests and lint

Requires `requirements-dev.txt`. Run from the project root.

```
pytest
ruff check .
mypy src app
```

The tests use a synthetic two-fund fixture with fault injection.

## Project layout

```
src/custom_re/
  loader.py     read and tidy the extract, fund_info_wide
  checks.py     named checks, run_all, summarise, fund_metrics
  report.py     Excel report writer
  completeness.py  datapoint_states: per fund and datapoint value state
  steps.py      reference_check, completeness (bands), usability (ratings)
  facts.py      datapoint names and helpers for the step 3 notes
  reference.py  read_reference, gp_map, add_gp (GP reference workbook)
  insights.py   build_report: summary and the three step tables (ReportData)
  html_report.py  render_html, write_html: the HTML management report
  settings.py   Period parsing and settings loading
app/run_dq.py   command-line entry point
config/         settings.yaml, dq_rules.yaml
tests/          pytest suite
notebooks/      exploration
docs/
data/           local only
output/         local only
```

## Status

Data quality checks are complete. Phase 2 (analysis) is pending: it will work per fund or with currency-free ratios (no FX rates needed) and still needs decisions on tolerances and AIA-weighting.
