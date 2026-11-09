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

The report has two stages. Stage 1 is completeness by GP and fund: each fund gets a tier from 1 to 4 (Complete, Substantially complete, Partial, Insufficient). Stage 2 is usability by fund and datapoint, for the funds in tiers 1-2 (the analysable funds).

- `dq_report.xlsx` has ten sheets: Summary (one row per check with PASS / INFO / FLAG), Issues (one row per finding with severity, GP, fund, datapoint and Excel row), Fund Metrics (with a GP column), S1 GP Tiers, S1 Fund Tiers, S1 Datapoint Completeness, S1 Datapoint Gaps (one row per fund and datapoint that is not reported, has no value, or is reported only as zero), S2 By Datapoint, S2 Usability Grid (fund by datapoint) and S2 Usability (one row per fund and datapoint, with reasons). The console also prints the Stage 1 tier counts and the Stage 2 rating counts.
- `dq_management_report.html` is a self-contained management report generated from the data (no external requests, light and dark themes, full-width layout). Sections: a verdict (red "Not ready" if there are errors; amber if there are warnings or findings to confirm, shown as "N points to confirm with managers"; green otherwise), key figures (including how many funds are analysable and the share of their datapoint values that are usable), ranked findings (including a warning when funds are too incomplete for datapoint analysis), Stage 1 (tier bar and definitions, GP table with tier profile, funds below the analysis tier with reasons, all funds by GP), Stage 1 datapoint completeness (funds missing each datapoint), Stage 2 (rating bar, by-datapoint table, fund by datapoint heatmap with reasons on hover), the check-group summary, a collapsed cross-fund datapoint register (Ready / Use with care / Not usable / Empty; amounts carry a neutral "own currency; compare by ratios" note), next steps and scope. Findings appear only when they apply.

Exit codes: 0 clean, 1 if any error-severity issue is found, 2 for a bad period, bad settings, or a missing or unreadable input or reference file, 3 if the data is clean but the HTML report failed (the Excel report is still written).

## Checks

The checks live in `src/custom_re/checks.py`; `CHECK_GROUPS` is the registry of all 49 check names. They are grouped as:

- structure: reporting date, blank keys, fund count, unknown datapoints, datapoints missing everywhere
- completeness: per-fund datapoint counts, datapoints not reported or without a value (info), time-series gaps for funds that report the series, projection frequency, exposure members, value field used
- uniqueness: duplicate keys
- validity: currencies, fractions in [0, 1], non-negative amounts, plausible IRR, allowed labels
- consistency: mixes sum to 100%, sector roll-ups, region hierarchy, GAV-weighted occupancy
- business: GAV/NAV/LTV relationships, lifecycle split, debt maturities vs LTV x GAV, fees, bad debts, date order
- outlier: cross-fund IQR outliers
- reference: incomplete or duplicate reference rows, funds in the extract but not in the reference, funds in the reference but missing from the extract

Multiple currencies across funds are expected; only a fund reporting more than one currency (`mixed_currency`) or an invalid currency code is an issue. Funds with different projection calendars are reported as an info note.

`src/custom_re/completeness.py` classifies each fund and datapoint as Has a value, None, Not reported, No value, No value in some records (partly blank series) or Reported only as zero, and ranks funds by how many of the 36 catalogued datapoints have a value (present and, for numbers, non-zero), tie-broken by share of non-zero records, then fund name; tied funds share a rank. Against the GP reference it covers every fund in the extract or the reference and gives each a status (Reported / Missing from extract / Not in reference). A per-GP roll-up (counts only, no amounts) shows funds expected, reported and missing, funds with all 36 datapoints valued, mean datapoints with value and errors and warnings; unmapped funds come last in the GP roll-up. It also lists datapoints reported only as zero and gives per-datapoint coverage. A zero counts as reported but without value, which may be genuine; zero-only datapoints are raised as an info finding to confirm with managers.

### Tiers and usability

`src/custom_re/stages.py` holds both stages. `add_tiers` assigns the tier from the `tiers` section of `config/dq_rules.yaml`. A datapoint counts as answered if it has a value, a zero, "none" (Bad debts) or is partly blank. Tier 1 needs every datapoint answered and no datapoint with an error; tier 2 needs at least 90% answered, at most one error datapoint and every category represented; tier 3 needs at least 70% answered; everything else, and every fund missing from the extract, is tier 4. Only catalogued datapoints count as error datapoints, and a fund-wide error (for example `mixed_currency`) keeps a fund out of tiers 1-2. When `core_datapoints` is set, a fund missing any of them cannot be tier 1 or 2 (the list is empty until agreed). Each fund has a `tier_reason` naming what keeps it from the tier above, and funds are ranked by tier. GPs are ranked by share of analysable funds, then number analysable, then coverage, mean datapoints with value and value share.

`usability` rates each analysable fund and datapoint as Usable, Use with care or Not usable (Not assessed for lower tiers), with reasons. Not usable: not reported, no value, an error, or a fund-wide error. Use with care: warnings, outlier or occupancy caveats, partly blank, only zero, past term end, capital calls after the investment period, anonymised country labels, region level naming, or debt equal to LTV x GAV. `src/custom_re/facts.py` holds the datapoint constants and helpers shared with `insights.py`.

Funds are not expected to populate every datapoint. A datapoint a fund does not report (`datapoint_not_reported`) or reports without a value (`value_missing`) is an info gap, not an error; the only exception is a missing Fund GAV, NAV or LTV, which raises a warning because the core rules cannot run (`rule_inputs_missing`). Text `NA`, `N/A`, `null`, `nan` or `none` (any case) in `Value Num` counts as no value; other text there is still a `non_numeric_value` error. Datapoints listed under `none_when_missing` in `config/dq_rules.yaml` (currently Bad debts) are different: not reported, blank, null/NA or zero all mean "none" (no bad debt), an answer rather than a gap.

A check that crashes is reported as `check_crashed` and does not stop the others.

## Configuration

`config/settings.yaml` holds `data_dir`, `output_dir`, `rules_file` (relative paths resolve against the project root, absolute paths are used as is) and `input_pattern` (default `Custom_RE_Extract_{period}.xlsx`, where `{period}` is the period code such as `2026Q2`) and `reference_file` (required; relative or full path to the GP reference workbook).

`config/dq_rules.yaml` holds the datapoint catalogue (with the expected value field: num, date or str), tolerances, allowed vocabularies, `none_when_missing`, plausibility bounds and the forward-looking calendars. Its `report:` section (all keys required) tunes the HTML report: `ltv_headroom`, `exact_rel_tol`, `exact_share_tol`, `anonymised_label_pattern`, `reconcile_sample` and `empty_field_notes`. Its `tiers` section sets the Stage 1 tier rules (`labels`, `core_datapoints`, `tier1`, `tier2`, `tier3`, `analysis_max_tier`). It has no `reporting_date` or `expected_fund_count`: the CLI sets them to the quarter end of the period and to the number of funds in the GP reference.

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
  completeness.py  fund_completeness, gp_completeness, datapoint_completeness
  stages.py     add_tiers (Stage 1 tiers), usability (Stage 2 ratings)
  facts.py      datapoint constants and helpers shared with insights
  reference.py  read_reference, gp_map, add_gp (GP reference workbook)
  insights.py   build_report: verdict, findings and datapoint register (ReportData)
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
