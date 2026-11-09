"""Tests for the management report content derived from one quarter's extract."""

import pandas as pd
import pytest

from custom_re.checks import run_all, summarise
from custom_re.insights import CARE, EMPTY, NOT_USABLE, READY, build_report, fmt_amount, fmt_pct
from custom_re.loader import tidy
from tests.conftest import ref_for


def _report(raw, rules):
    df = tidy(raw)
    issues = run_all(raw, df, rules, ref_for(raw))
    return build_report(raw, df, issues, summarise(issues), rules, ref_for(raw), "Q2 2026", "x.xlsx")


def _titles(rep):
    return [f.title for f in rep.findings]


def _rating(rep, dp):
    return next(r for r in rep.datapoints if r.datapoint == dp)


@pytest.mark.parametrize(("value", "text"), [
    (0, "0"), (950, "950"), (60_500, "60.5k"), (165e6, "165m"), (42.9e9, "42.9bn"), (1.234e12, "1,234bn"),
    # Just below a unit boundary must promote, never print "1e+03k".
    (999_400, "999k"), (999_999, "1m"), (999.6e6, "1bn"),
])
def test_fmt_amount(value, text):
    assert fmt_amount(value) == text


def test_fmt_pct_uses_true_minus():
    assert fmt_pct(-0.019) == "−1.9%"
    assert fmt_pct(0.5) == "50.0%"
    assert fmt_pct(-0.0001) == "0.0%"


def test_clean_fixture_is_green(raw, rules):
    rep = _report(raw, rules)
    # Calendar mix and the other caveats are information only; nothing needs confirming.
    assert rep.counts["error"] == rep.counts["warning"] == 0
    assert rep.verdict_tone == "good"
    titles = " | ".join(_titles(rep))
    # Fixture: one monthly and one quarterly fund, investment period ended in 2021 with calls projected,
    # anonymised countries, empty export columns and debt exactly LTV x GAV.
    assert "different calendars" in titles
    assert "capital calls after the investment period" in titles
    assert "never filled" in titles
    assert "Geography labels" in titles
    assert "unusually exact" in titles
    assert "currencies" not in titles and "past the stated end" not in titles
    assert _rating(rep, "Fund LTV (current)").rating == READY
    assert _rating(rep, "Country").rating == CARE
    assert _rating(rep, "Capital call amount").rating == READY  # mixed calendars: a note only


def test_fund_past_term_turns_verdict_amber(raw, rules):
    mask = (raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "End of fund term")
    raw.loc[mask, "Value Date"] = pd.Timestamp("2025-06-30")
    rep = _report(raw, rules)
    assert rep.verdict_tone == "warn"
    assert "1 point to confirm" in rep.verdict


def test_empty_source_fields_listed_with_configured_notes(raw, rules):
    rep = _report(raw, rules)
    empty = {r.datapoint: r for r in rep.datapoints if r.rating == EMPTY}
    assert {"Scenario", "Version", "Value Memo"} <= set(empty)
    assert "" not in empty  # unnamed padding columns are not fields
    assert empty["Scenario"].notes == [rules["report"]["empty_field_notes"]["Scenario"]]


def test_one_currency_per_fund_is_expected(raw, rules):
    # Funds reporting in different currencies is by design: no finding, no lower rating.
    raw.loc[raw["Company"] == "Fund B", "Currency"] = "EUR"
    rep = _report(raw, rules)
    assert not any("currenc" in f.title for f in rep.findings)
    assert rep.verdict_tone == "good"
    gav = _rating(rep, "Fund GAV")
    assert gav.rating == READY and any("own currency" in n for n in gav.notes)


def test_fund_past_term_is_reported(raw, rules):
    mask = (raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "End of fund term")
    raw.loc[mask, "Value Date"] = pd.Timestamp("2025-06-30")
    rep = _report(raw, rules)
    assert "1 fund is past the stated end of fund term" in _titles(rep)
    assert _rating(rep, "End of fund term").rating == CARE


def test_errors_make_report_not_ready(raw, rules):
    raw = raw[~((raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund GAV"))].reset_index(drop=True)
    rep = _report(raw, rules)
    assert rep.verdict_tone == "bad"
    assert rep.findings[0].tone == "bad" and "error" in rep.findings[0].title
    gav = _rating(rep, "Fund GAV")
    assert gav.coverage == 1 and gav.rating == NOT_USABLE


def test_check_groups_cover_every_check(raw, rules):
    rep = _report(raw, rules)
    assert int(rep.checks["checks"].sum()) == 49
    assert set(rep.flagged["status"]) <= {"INFO", "FLAG"}


def test_missing_fund_life_dates_do_not_crash(raw, rules):
    raw = raw[~raw["Datapoint.Name"].isin(["End of fund term", "End of investment period"])]
    rep = _report(raw.reset_index(drop=True), rules)
    titles = " | ".join(_titles(rep))
    assert "past the stated end" not in titles and "after the investment period" not in titles
    assert _rating(rep, "End of fund term").rating == NOT_USABLE


def test_empty_extract_rates_nothing_ready(raw, rules):
    rep = _report(raw.iloc[0:0].copy(), rules)
    assert rep.verdict_tone == "bad"
    assert all(r.rating in (NOT_USABLE, EMPTY) for r in rep.datapoints)


def test_errors_without_datapoint_are_counted(raw, rules):
    rules["expected_fund_count"] = 3  # fund_count error has no datapoint
    rep = _report(raw, rules)
    assert rep.unassigned_errors >= 1


def test_exactness_claim_needs_every_fund(raw, rules):
    raw = raw[~((raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Debt maturity value"))]
    rep = _report(raw.reset_index(drop=True), rules)
    assert "unusually exact" not in " | ".join(_titles(rep))


def test_zero_only_datapoints_finding(raw, rules):
    # The fixture reports carried interest as 0.0 for both funds.
    rep = _report(raw, rules)
    f = next(f for f in rep.findings if "only as zero" in f.title)
    assert "Carried interest incurred to-date (2 funds)" in f.body


def test_absent_datapoint_is_not_called_a_zero(raw, rules):
    raw = raw[~((raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund NAV"))].reset_index(drop=True)
    rep = _report(raw, rules)
    f = next(f for f in rep.findings if "only as zero" in f.title)
    assert "Fund NAV" not in f.body  # absent, not zero: reported by the checks as an error instead
    assert f.title.startswith("2 funds")  # both still report carried interest as 0.0
