"""Tests for rendering the report to HTML."""

import pandas as pd

from custom_re.checks import run_all
from custom_re.html_report import render_html, write_html
from custom_re.insights import build_report
from custom_re.loader import tidy
from tests.conftest import ref_for


def _page(raw, rules, ref=None, source="x.xlsx"):
    df = tidy(raw)
    ref = ref_for(raw) if ref is None else ref
    rep = build_report(df, run_all(raw, df, rules, ref), rules, ref, "Q2 2026", source)
    return render_html(rep, pd.Timestamp("2026-07-15 09:30"))


def test_page_has_summary_three_steps_and_actions(raw, rules):
    page = _page(raw, rules)
    assert page.startswith("<!doctype html>") and "<title>Custom RE Q2 2026 Data Quality</title>" in page
    ids = ['id="summary"', 'id="step1"', 'id="step2"', 'id="step3"', 'id="actions"']
    assert all(i in page for i in ids) and [page.index(i) for i in ids] == sorted(page.index(i) for i in ids)
    assert "All 2 funds in the extract are in the reference list" in page
    assert "Good for all 2 funds" in page and "None: no fund" in page
    assert not any(x in page for x in ('src="http', 'href="http', "url(http", "@import"))


def test_page_names_problem_funds(raw, rules):
    ref = pd.DataFrame({"excel_row": [2, 3], "gp": ["GP One", "GP One"], "fund": ["Fund A", "Fund C"]})
    raw.loc[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund LTV (current)"), "Value Num"] = 2.0
    page = _page(raw, rules, ref)
    step1 = page[page.index('id="step1"'):page.index('id="step2"')]
    assert "In the extract but not in the reference (1)" in step1 and "Fund B" in step1
    assert "In the reference but missing from the extract (1)" in step1 and "Fund C" in step1
    step3 = page[page.index('id="step3"'):page.index('id="actions"')]
    assert "<b>Unusable for</b> Fund A" in step3


def test_escapes_text(raw, rules, tmp_path):
    raw.loc[raw["Company"] == "Fund A", "Company"] = "<b>Fund A</b>"
    page = _page(raw, rules, source="<i>x</i>.xlsx")
    assert "<b>Fund A</b>" not in page and "&lt;i&gt;x&lt;/i&gt;.xlsx" in page


def test_write_html_creates_folder(raw, rules, tmp_path):
    df = tidy(raw)
    rep = build_report(df, run_all(raw, df, rules, ref_for(raw)), rules, ref_for(raw), "Q2 2026", "x.xlsx")
    path = tmp_path / "2026Q2" / "r.html"
    write_html(path, rep)
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")
