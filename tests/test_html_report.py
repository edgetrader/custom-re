"""Tests for rendering the management report to HTML."""

import pandas as pd

from custom_re.checks import run_all, summarise
from custom_re.html_report import render_html, write_html
from custom_re.insights import build_report
from custom_re.loader import tidy
from tests.conftest import ref_for


def _report(raw, rules, source="x.xlsx"):
    df = tidy(raw)
    issues = run_all(raw, df, rules, ref_for(raw))
    return build_report(raw, df, issues, summarise(issues), rules, ref_for(raw), "Q2 2026", source)


def test_render_contains_sections_and_findings(raw, rules):
    rep = _report(raw, rules)
    page = render_html(rep, pd.Timestamp("2026-07-15 09:30"))
    assert page.startswith("<!doctype html>")
    assert "<title>Custom RE Q2 2026 Data Quality</title>" in page
    for anchor in ['id="findings"', 'id="stage1"', 'id="datapoint-completeness"', 'id="stage2"',
                   'id="checks"', 'id="register"', 'id="next"']:
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


def test_stage_sections_follow_the_data(raw, rules):
    page = render_html(_report(raw, rules), pd.Timestamp("2026-07-15"))
    assert "2 of 2 funds are complete enough to analyse" in page
    assert "Every reported fund reaches tier 2 or better" in page
    assert 'class="heat"' in page and "Fund A" in page[page.index('id="stage2"'):]
    # Drop most of Fund B: it falls to tier 4, is named with its reason and left out of the grid.
    keep = raw["Datapoint.Name"].isin(["Fund GAV", "Fund NAV", "Country", "Sector - level 1"])
    raw = raw[(raw["Company"] == "Fund A") | keep].reset_index(drop=True)
    page = render_html(_report(raw, rules), pd.Timestamp("2026-07-15"))
    stage1 = page[page.index('id="stage1"'):page.index('id="datapoint-completeness"')]
    assert "Funds below tier 2: left out of Stage 2" in stage1 and "datapoints unanswered" in stage1
    heat = page[page.index('class="heat"'):]
    assert "Fund A" in heat and "Fund B" not in heat


def test_datapoint_section_names_funds_by_gap(raw, rules):
    raw["Value Num"] = raw["Value Num"].astype(object)
    raw.loc[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund NAV"), "Value Num"] = "NA"
    none_dps = ["Fund Target Net IRR", "Bad debts"]
    fund_b_none = (raw["Company"] == "Fund B") & raw["Datapoint.Name"].isin(none_dps)
    raw = raw[~fund_b_none]
    page = render_html(_report(raw.reset_index(drop=True), rules), pd.Timestamp("2026-07-15"))
    section = page[page.index('id="datapoint-completeness"'):page.index('id="register"')]
    assert "Funds missing each datapoint or its value" in section
    nav = section[section.index("<td>Fund NAV"):]
    assert "Fund A" in nav[:nav.index("</tr>")]
    assert ("no bad debt for 1 fund (not reported, blank or zero), "
            "treated as none rather than a gap: Fund B") in section
    # Fund tables show the gaps per fund.
    assert "<b>Missing:</b> Fund Target Net IRR" in page
