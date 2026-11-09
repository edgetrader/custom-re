"""Tests for rendering the management report to HTML."""

import pandas as pd

from custom_re.checks import run_all, summarise
from custom_re.html_report import render_html, write_html
from custom_re.insights import build_report
from custom_re.loader import tidy


def _report(raw, rules, source="x.xlsx"):
    df = tidy(raw)
    issues = run_all(raw, df, rules)
    return build_report(raw, df, issues, summarise(issues), rules, "Q2 2026", source)


def test_render_contains_sections_and_findings(raw, rules):
    rep = _report(raw, rules)
    page = render_html(rep, pd.Timestamp("2026-07-15 09:30"))
    assert page.startswith("<!doctype html>")
    assert "<title>Custom RE Q2 2026 Data Quality</title>" in page
    for anchor in ['id="usability"', 'id="findings"', 'id="register"', 'id="checks"', 'id="next"']:
        assert anchor in page
    for f in rep.findings:
        assert f.action.replace("'", "&#x27;") in page
    assert "15 Jul 2026 09:30" in page
    # Self-contained: no external fonts, scripts or styles.
    assert not any(x in page for x in ('src="http', 'href="http', "url(http", "@import"))


def test_render_escapes_text(raw, rules):
    page = render_html(_report(raw, rules, source="<b>x</b>.xlsx"), pd.Timestamp("2026-07-15"))
    assert "&lt;b&gt;x&lt;/b&gt;.xlsx" in page
    assert "<b>x</b>.xlsx" not in page


def test_write_html_creates_folder(raw, rules, tmp_path):
    path = tmp_path / "2026Q2" / "r.html"
    write_html(path, _report(raw, rules))
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_render_escapes_extract_text(raw, rules):
    # Labels from the extract reach the page through issue details; they must never become markup.
    raw.loc[raw["Datapoint.Name"] == "Region Level 2", "Value String"] = "<img src=x onerror=alert(1)>"
    page = render_html(_report(raw, rules), pd.Timestamp("2026-07-15"))
    assert "<img src=x" not in page


def test_unassigned_errors_note(raw, rules):
    rules["expected_fund_count"] = 3
    page = render_html(_report(raw, rules), pd.Timestamp("2026-07-15"))
    assert "apply to no single datapoint" in page
