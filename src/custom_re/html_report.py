"""Render the one-quarter management data quality report as a self-contained HTML page."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import cast

import pandas as pd

from custom_re.insights import CARE, EMPTY, NOT_USABLE, RATINGS, READY, ReportData, ratings_count, slug

RATING_TEXT = {
    READY: "usable as reported, for single funds and across funds.",
    CARE: "accurate for a single fund, but needs conversion, alignment or a stated caveat before "
          "comparing or totalling.",
    NOT_USABLE: "missing or failing checks; do not use until corrected at source.",
    EMPTY: "the field exists in the extract but holds no values.",
}
TONE_LABEL = {"good": "Ready", "warn": "Caveats", "bad": "Not ready"}

# Inline so the file opens anywhere, offline, with no external requests.
CSS = """
:root{--bg:#f6f5f2;--surface:#fff;--ink:#1d1f23;--muted:#5d636d;--line:#e3e1dc;--soft:#efede8;
--good:#1f7a4d;--good-bg:#e5f3ea;--warn:#9a5b00;--warn-bg:#fbf0dc;--bad:#b3261e;--bad-bg:#fbe7e5;
--info:#2b5c9e;--info-bg:#e6eefa;--empty:#8a8f98;--empty-bg:#eeeff1;--accent:#1d3c6e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141518;--surface:#1c1e22;
--ink:#e9e9ea;--muted:#a0a5ad;--line:#2d3036;--soft:#24272c;--good:#5fc48f;--good-bg:#173226;
--warn:#e3a649;--warn-bg:#352a17;--bad:#f08078;--bad-bg:#3a1d1b;--info:#86aee8;--info-bg:#1c2a40;
--empty:#8d929a;--empty-bg:#26292e;--accent:#9db8e6}}
:root[data-theme="dark"]{--bg:#141518;--surface:#1c1e22;--ink:#e9e9ea;--muted:#a0a5ad;--line:#2d3036;
--soft:#24272c;--good:#5fc48f;--good-bg:#173226;--warn:#e3a649;--warn-bg:#352a17;--bad:#f08078;
--bad-bg:#3a1d1b;--info:#86aee8;--info-bg:#1c2a40;--empty:#8d929a;--empty-bg:#26292e;--accent:#9db8e6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:1080px;margin:0 auto;padding:40px 16px 64px}
h1{font-size:30px;line-height:1.2;margin:4px 0 18px;letter-spacing:-.01em}
h2{font-size:20px;margin:0 0 4px}
.eyebrow{font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
.sub{color:var(--muted);margin:0 0 18px}
section{margin-top:44px}
.meta{display:flex;flex-wrap:wrap;gap:12px 40px;margin:0;
padding:16px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.meta dt{font-size:12px;color:var(--muted)}.meta dd{margin:0;font-weight:600;overflow-wrap:break-word}
.verdict{margin-top:28px;background:var(--surface);border:1px solid var(--line);border-left:6px solid
var(--tone);border-radius:10px;padding:20px 22px}
.verdict .state{display:inline-block;font-size:12px;
font-weight:700;color:var(--tone);background:var(--tone-bg);
padding:2px 10px;border-radius:999px;margin-bottom:8px}
.verdict p.lead{font-size:19px;font-weight:600;margin:0 0 6px;
color:var(--ink)}.verdict p{margin:0;color:var(--muted)}
.t-good{--tone:var(--good);--tone-bg:var(--good-bg)}.t-warn{--tone:var(--warn);--tone-bg:var(--warn-bg)}
.t-bad{--tone:var(--bad);--tone-bg:var(--bad-bg)}.t-info{--tone:var(--info);--tone-bg:var(--info-bg)}
.figures{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}
.fig{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.fig b{display:block;font-size:24px;
font-variant-numeric:tabular-nums}.fig span{color:var(--muted);font-size:13px}
.bar{display:flex;height:16px;border-radius:999px;overflow:hidden;background:var(--soft);margin:14px 0 12px}
.bar i{display:block}.legend{display:flex;flex-wrap:wrap;gap:8px 22px;margin:0;padding:0;list-style:none}
.legend li{font-size:14px;color:var(--muted)}.legend b{color:var(--ink)}
.dot{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:0}
.r-ready{--c:var(--good);--c-bg:var(--good-bg)}.r-use-with-care{--c:var(--warn);--c-bg:var(--warn-bg)}
.r-not-usable{--c:var(--bad);--c-bg:var(--bad-bg)}.r-empty{--c:var(--empty);--c-bg:var(--empty-bg)}
.dot,.bar i{background:var(--c)}
.defs{margin:14px 0 0;padding:0;list-style:none;font-size:14px;color:var(--muted)}.defs b{color:var(--ink)}
.findings{list-style:none;margin:16px 0 0;padding:0;display:grid;gap:12px}
.finding{display:grid;grid-template-columns:34px 1fr;gap:4px 14px;background:var(--surface);
border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.num{grid-row:span 3;width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font-weight:700;
font-size:14px;color:var(--tone);background:var(--tone-bg)}
.finding h3{font-size:16px;margin:2px 0 0;display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px}
.tag{font-size:11px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:var(--tone);
background:var(--tone-bg);padding:2px 8px;border-radius:999px}
.finding p{margin:0}.finding .action{color:var(--muted);font-size:14px}.finding .action b{color:var(--ink)}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}
.filters button{font:inherit;font-size:13px;color:var(--ink);background:var(--surface);border:1px solid
var(--line);border-radius:999px;padding:5px 12px;cursor:pointer}
.filters button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.table-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;font-weight:600;color:var(--muted);background:var(--soft);white-space:nowrap}
tr:last-child td{border-bottom:0}td.n{font-variant-numeric:tabular-nums;white-space:nowrap}
tr.cat td{background:var(--soft);font-weight:700;font-size:13px}
.pill{display:inline-block;white-space:nowrap;font-size:12px;font-weight:600;color:var(--c);background:var(--c-bg);
padding:2px 9px;border-radius:999px}
.st{font-weight:700;font-size:12px}.st-PASS{color:var(--good)}.st-INFO{color:var(--info)}.st-FLAG{color:var(--bad)}
.muted{color:var(--muted)}ol.steps{margin:12px 0 0;padding-left:22px}ol.steps li{margin:6px 0}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px 32px}
.cols ul{margin:6px 0 0;padding-left:20px;color:var(--muted)}h3.small{font-size:14px;margin:0}
.gap-top{margin-top:18px}.gap-sm{margin-top:8px}
.note{margin:10px 0 0;font-size:14px;color:var(--bad)}
.dist{display:grid;gap:6px;margin:14px 0 18px;max-width:640px}
.dist div{display:grid;grid-template-columns:110px 1fr 70px;align-items:center;gap:10px;font-size:14px}
.track{height:12px;background:var(--soft);border-radius:999px;overflow:hidden}
.track i{display:block;height:100%;background:var(--accent);border-radius:999px}
.dist .n{color:var(--muted);font-variant-numeric:tabular-nums}
.meter{display:flex;align-items:center;gap:8px;min-width:150px}.meter .track{flex:1}
details{margin-top:12px}summary{cursor:pointer;font-weight:600;font-size:14px}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
@media print{body{background:#fff}.filters{display:none}section,.finding{break-inside:avoid}
tr[hidden]{display:table-row!important}}
"""

# Filter the register by rating; a category heading hides when none of its rows match.
JS = """
(function(){var bs=document.querySelectorAll('.filters button');
var rows=[].slice.call(document.querySelectorAll('#register tbody tr'));
function apply(f){var cat=null,n=0;function close(){if(cat)cat.hidden=n===0;}
rows.forEach(function(tr){if(tr.classList.contains('cat')){close();cat=tr;n=0;return;}
var show=f==='all'||tr.getAttribute('data-r')===f;tr.hidden=!show;if(show)n++;});close();
bs.forEach(function(b){b.setAttribute('aria-pressed',String(b.getAttribute('data-f')===f));});}
bs.forEach(function(b){b.addEventListener('click',function(){apply(b.getAttribute('data-f'));});});})();
"""


def _e(x: object) -> str:
    """HTML-escape any value."""
    return escape(str(x))


def _verdict(d: ReportData) -> str:
    return (f'<div class="verdict t-{d.verdict_tone}"><span class="state">{TONE_LABEL[d.verdict_tone]}</span>'
            f'<p class="lead">{_e(d.verdict)}</p><p>{_e(d.verdict_detail)}</p></div>')


def _figures(d: ReportData) -> str:
    tiles = "".join(f'<div class="fig"><b>{_e(v)}</b><span>{_e(k)}</span></div>' for v, k in d.figures)
    return f'<div class="figures">{tiles}</div>'


def _usability(d: ReportData) -> str:
    counts = ratings_count(d.datapoints)
    total = sum(counts.values()) or 1
    shown = [r for r in RATINGS if counts[r]]
    bar = "".join(f'<i class="r-{slug(r)}" style="width:{counts[r] / total * 100:.2f}%" title="{_e(r)}"></i>'
                  for r in shown)
    legend = "".join(f'<li><span class="dot r-{slug(r)}"></span><b>{counts[r]}</b> {_e(r)}</li>'
                     for r in shown)
    defs = "".join(f"<li><b>{_e(r)}</b>: {_e(RATING_TEXT[r])}</li>" for r in shown)
    n_dp = total - counts[EMPTY]
    return (f'<section id="usability"><h2>{counts[READY]} of {n_dp} datapoints are ready to use '
            f'as reported</h2>'
            '<p class="sub">Each datapoint has one rating. Empty source fields are counted '
            'separately because '
            f'they hold no data.</p><div class="bar" role="img" aria-label="Usability split">{bar}</div>'
            f'<ul class="legend">{legend}</ul><ul class="defs">{defs}</ul></section>')


def _findings(d: ReportData) -> str:
    if not d.findings:
        return ('<section id="findings"><h2>Findings</h2><p class="sub">No issues or caveats for management '
                'this quarter.</p></section>')
    items = "".join(
        f'<li class="finding t-{f.tone}"><span class="num">{i}</span>'
        f'<h3>{_e(f.title)} <span class="tag">{_e(f.tag)}</span></h3>'
        f'<p>{_e(f.body)}</p><p class="action"><b>Action:</b> {_e(f.action)}</p></li>'
        for i, f in enumerate(d.findings, 1)
    )
    n = len(d.findings)
    return (f'<section id="findings"><h2>What management needs to decide or know</h2>'
            f'<p class="sub">{n} finding{"" if n == 1 else "s"}, most severe first.</p>'
            f'<ol class="findings">{items}</ol></section>')


def _pct(x: float) -> str:
    """Whole-number percentage, e.g. 0.613 -> 61%."""
    return f"{x * 100:.0f}%"


def _fund_rows(rows: pd.DataFrame, why: bool) -> str:
    """Table rows for funds; `why` adds the datapoints reported only as zero."""
    out = []
    for r in rows.to_dict("records"):
        zero_dps = _e(str(r["zero_datapoints"])) if "zero_datapoints" in r else ""
        extra = f"<td>{zero_dps or '<span class=muted>—</span>'}</td>" if why else ""
        out.append(
            f'<tr><td class="n">{int(r["rank"])}</td><td>{_e(r["fund"])}</td><td>{_e(r["currency"])}</td>'
            f'<td class="n">{int(r["datapoints_with_value"])} / {int(r["datapoints_total"])}</td>'
            f'<td class="n">{_pct(float(r["value_share"]))}</td>{extra}</tr>'
        )
    return "".join(out)


def _fund_table(rows: pd.DataFrame, why: bool = True) -> str:
    """Fund completeness table; `why` adds the column of zero-only datapoints."""
    head = ('<th>Rank</th><th>Fund</th><th>Ccy</th><th>Datapoints with value</th><th>Records with value</th>'
            + ("<th>Reported only as zero</th>" if why else ""))
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{_fund_rows(rows, why)}</tbody></table></div>")


def _fund_completeness(d: ReportData) -> str:
    fc = d.fund_completeness
    if fc.empty:
        return ""
    total = int(fc["datapoints_total"].iloc[0])
    n_full = int((fc["datapoints_with_value"] == total).sum())
    levels = fc["datapoints_with_value"].value_counts().sort_index(ascending=False)
    biggest = int(levels.max())
    dist = "".join(
        f'<div><span>{lvl} of {total}</span><span class="track"><i style="width:{n / biggest * 100:.1f}%">'
        f'</i></span><span class="n">{_plural_funds(int(n))}</span></div>'
        for lvl, n in levels.items()
    )
    n_rep = int((fc["datapoints_reported"] == total).sum())
    reporting = (f"Every fund reports all {total} datapoints." if n_rep == len(fc)
                 else f"{n_rep} of {len(fc)} funds report all {total} datapoints; the rest miss some "
                      "(see the checks).")
    n = min(d.top_n, len(fc))
    return (
        f'<section id="fund-completeness"><h2>Fund completeness: {n_full} of {len(fc)} funds have a value in '
        f'every datapoint</h2><p class="sub">{reporting} '
        'A datapoint counts as '
        '"with value" when at least one of its records is non-zero; a zero may be genuine or may mean "not '
        'reported". Funds are ranked by datapoints with value, then by the share of their records with a '
        'non-zero value. Amounts are not compared, since each fund reports in its own currency.</p>'
        '<h3 class="small">Funds by number of datapoints with a value</h3>'
        f'<div class="dist" role="img" aria-label="Funds by datapoints with value">{dist}</div>'
        f'<h3 class="small">Most complete</h3><div class="gap-sm">{_fund_table(fc.head(n), why=False)}</div>'
        f'<h3 class="small gap-top">Least complete</h3>'
        f'<div class="gap-sm">{_fund_table(fc.tail(n).iloc[::-1])}</div>'
        f"<details><summary>All {len(fc)} funds</summary>{_fund_table(fc)}</details></section>"
    )


def _plural_funds(n: int) -> str:
    """'1 fund' or 'N funds'."""
    return f"{n} fund{'' if n == 1 else 's'}"


def _dp_rows(dc: pd.DataFrame) -> str:
    """Table rows for datapoints with a funds-with-value meter."""
    out = []
    for r in dc.to_dict("records"):
        total, valued, recs = int(r["funds_total"]), int(r["funds_with_value"]), int(r["records"])
        width = valued / total * 100 if total else 0.0
        share = _pct(int(r["records_with_value"]) / recs) if recs else "—"
        out.append(
            f'<tr><td>{_e(r["datapoint"])}</td><td class="muted">{_e(r["category"])}</td>'
            f'<td class="n">{int(r["funds_reporting"])} / {total}</td>'
            f'<td><span class="meter"><span class="track"><i style="width:{width:.1f}%"></i></span>'
            f'<span class="n">{valued} / {total}</span></span></td><td class="n">{share}</td></tr>'
        )
    return "".join(out)


def _dp_table(dc: pd.DataFrame) -> str:
    """Datapoint completeness table."""
    return ('<div class="table-wrap"><table><thead><tr><th>Datapoint</th><th>Category</th>'
            '<th>Funds reporting</th>'
            '<th>Funds with value</th><th>Records with value</th></tr></thead>'
            f"<tbody>{_dp_rows(dc)}</tbody></table></div>")


def _datapoint_completeness(d: ReportData) -> str:
    dc = d.datapoint_completeness
    if dc.empty:
        return ""
    # Gaps: a fund with only zeros, or zero periods/positions within a series.
    gaps = dc[(dc["funds_with_value"] < dc["funds_total"]) | (dc["records_with_value"] < dc["records"])]
    n_full = int((dc["funds_with_value"] == dc["funds_total"]).sum())
    complete = dc.loc[~dc.index.isin(gaps.index)]
    lead = (f'<p>The other {len(complete)} datapoints have a non-zero value in every record '
            'for every fund.</p>'
            if len(complete) else "")
    body = (f'<h3 class="small">Datapoints with zero values, most complete first</h3>'
            f'<div class="gap-sm">{_dp_table(gaps)}</div>' if len(gaps) else "")
    return (
        f'<section id="datapoint-completeness"><h2>Datapoint completeness: {n_full} of {len(dc)} '
        'have a value for every fund</h2><p class="sub">"Funds with value" counts funds with at '
        'least one non-zero record. '
        '"Records with value" is the share of records that are non-zero: for time series it shows how many '
        f'periods carry a figure.</p>{body}{lead}'
        f"<details><summary>All {len(dc)} datapoints</summary>{_dp_table(dc)}</details></section>"
    )


def _register(d: ReportData) -> str:
    counts = ratings_count(d.datapoints)
    buttons = [f'<button type="button" data-f="all" aria-pressed="true">All ({len(d.datapoints)})</button>']
    buttons += [f'<button type="button" data-f="{slug(r)}" aria-pressed="false">{_e(r)} '
                f'({counts[r]})</button>'
                for r in RATINGS if counts[r]]
    # Errors without a datapoint (structure, fund-level) cannot lower any single rating, so say so.
    n_un = d.unassigned_errors
    unassigned = (f'<p class="note">{n_un} error{"" if n_un == 1 else "s"} in the checks apply to no single '
                  'datapoint, so the ratings above do not reflect them. See the Issues sheet.</p>'
                  if n_un else "")
    body, cat = [], None
    for r in d.datapoints:
        if r.category != cat:
            cat = r.category
            body.append(f'<tr class="cat"><td colspan="5">{_e(cat)}</td></tr>')
        body.append(
            f'<tr data-r="{slug(r.rating)}"><td>{_e(r.datapoint)}</td><td class="n">'
            f'{r.coverage}/{r.total}</td>'
            f'<td class="n">{_e(r.observed)}</td><td><span class="pill r-{slug(r.rating)}">'
            f'{_e(r.rating)}</span>'
            f'</td><td>{_e(" ".join(r.notes)) or "<span class=muted>—</span>"}</td></tr>'
        )
    return (
        '<section id="register"><h2>Datapoint register</h2><p class="sub">Coverage counts the '
        'funds reporting '
        'the item. Ranges are across funds as reported; amounts are in each fund\'s own currency '
        '(k = thousand, m = million, bn = billion).</p>'
        f'<div class="filters" role="group" aria-label="Filter by usability">{"".join(buttons)}</div>'
        '<div class="table-wrap"><table><thead><tr><th>Datapoint</th><th>Coverage</th><th>Observed range</th>'
        f'<th>Usability</th><th>Notes</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>{unassigned}</section>'
    )


def _checks(d: ReportData) -> str:
    g = d.checks
    rows = "".join(
        f'<tr><td>{_e(str(r.group).capitalize())}</td><td>{_e(r.description)}</td><td class="n">'
        f'{r.checks}</td>'
        f'<td class="n"><span class="st st-PASS">{r.PASS}</span></td>'
        f'<td class="n"><span class="st st-INFO">{r.INFO}</span></td>'
        f'<td class="n"><span class="st st-FLAG">{r.FLAG}</span></td></tr>'
        for r in g.itertuples()
    )
    n, n_ok = int(g["checks"].sum()), int(g["PASS"].sum() + g["INFO"].sum())
    flagged = ""
    if not d.flagged.empty:
        items = "".join(
            f'<tr><td><code>{_e(r.check)}</code></td><td><span class="st st-{_e(r.status)}">'
            f'{_e(r.status)}</span>'
            f'</td><td class="n">{r.issues}</td><td class="n">{cast(int, r.funds_affected) or "—"}</td>'
            f'<td>{_e(r.example)}</td></tr>'
            for r in d.flagged.itertuples()
        )
        flagged = ('<h3 class="small gap-top">Checks with notes or issues</h3>'
                   '<div class="table-wrap gap-sm"><table><thead><tr><th>'
                   'Check</th><th>Status</th>'
                   '<th>Issues</th><th>Funds</th><th>Example</th></tr></thead>'
                   f'<tbody>{items}</tbody></table></div>')
    return (f'<section id="checks"><h2>Checks performed: {n_ok} of {n} passed</h2>'
            '<p class="sub">PASS: no issues. INFO: an observation, not a defect. FLAG: errors '
            'or warnings.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Area</th><th>What was verified</th><th>Checks</th>'
            f'<th>Pass</th><th>Info</th><th>Flag</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'{flagged}</section>')


def _next_steps(d: ReportData) -> str:
    if not d.findings:
        return ""
    steps = "".join(f"<li>{_e(f.action)}</li>" for f in d.findings)
    return f'<section id="next"><h2>Next steps</h2><ol class="steps">{steps}</ol></section>'


def _scope(d: ReportData) -> str:
    n_empty = ratings_count(d.datapoints)[EMPTY]
    return (
        '<section id="scope"><div class="cols"><div><h3 class="small">What this review covers</h3><ul>'
        f'<li>All records in the KPI sheet of the {_e(d.period_label)} extract: '
        f'{len(d.datapoints) - n_empty} '
        f'datapoints and {n_empty} empty source fields.</li>'
        '<li>Completeness, validity, internal consistency and plausibility.</li>'
        '<li>Repeatable: the same checks and this report are generated for each quarter.</li></ul></div>'
        '<div><h3 class="small">What it does not cover</h3><ul>'
        "<li>Agreement with managers' source reports or audited accounts.</li>"
        '<li>Whether the valuations themselves are appropriate.</li>'
        f'<li>Comparison with other quarters: this report covers {_e(d.period_label)} only.</li>'
        '<li>Totals across funds: amounts are in each fund\'s own currency by design, so funds are compared '
        'on completeness and ratios.</li>'
        '</ul></div></div></section>'
    )


def render_html(d: ReportData, generated: pd.Timestamp) -> str:
    """Full HTML document for one quarter's report."""
    title = f"Custom RE {d.period_label} Data Quality"
    meta = [
        ("Source", d.source_name),
        ("Reporting date", f"{d.reporting_date:%d %b %Y}"),
        ("Report generated", f"{generated:%d %b %Y %H:%M}"),
        ("Issues", f"{d.counts['error']} errors · {d.counts['warning']} warnings · {d.counts['info']} info"),
    ]
    meta_html = "".join(f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in meta)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(title)}</title><style>{CSS}</style></head><body><main>"
        f'<div class="eyebrow">Data quality review · {_e(d.period_label)}</div>'
        '<h1>Custom RE fund KPI extract: is it fit for use?</h1>'
        f'<dl class="meta">{meta_html}</dl>'
        f"{_verdict(d)}{_figures(d)}{_usability(d)}{_findings(d)}{_fund_completeness(d)}"
        f"{_datapoint_completeness(d)}{_register(d)}{_checks(d)}"
        f"{_next_steps(d)}{_scope(d)}"
        f'<footer>Generated by <code>app/run_dq.py</code> from {_e(d.source_name)}. Item-level results with '
        'Excel row references are in <code>dq_report.xlsx</code> in the same folder.</footer>'
        f"</main><script>{JS}</script></body></html>"
    )


def write_html(path: Path, d: ReportData, generated: pd.Timestamp | None = None) -> None:
    """Write the report to `path`, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(d, generated or pd.Timestamp.now()), encoding="utf-8")
