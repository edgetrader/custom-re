"""Render the one-quarter data quality report (summary, steps, actions) as a self-contained HTML page."""

from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd

from custom_re.insights import ReportData, plural
from custom_re.steps import CAUTION, GOOD, MISSING_FROM_EXTRACT, NOT_IN_REFERENCE, UNUSABLE

# Rating -> CSS class for its colour.
R_CLASS = {GOOD: "ok", CAUTION: "care", UNUSABLE: "bad"}

# Inline so the file opens anywhere, offline, with no external requests; light and dark themes.
CSS = """
:root{--bg:#f6f5f2;--surface:#fff;--ink:#1d1f23;--muted:#5d636d;--line:#e3e1dc;--soft:#efede8;
--ok:#1f7a4d;--ok-bg:#e5f3ea;--care:#9a5b00;--care-bg:#fbf0dc;--bad:#b3261e;--bad-bg:#fbe7e5;
--accent:#1d3c6e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141518;--surface:#1c1e22;
--ink:#e9e9ea;--muted:#a0a5ad;--line:#2d3036;--soft:#24272c;--ok:#5fc48f;--ok-bg:#173226;--care:#e3a649;
--care-bg:#352a17;--bad:#f08078;--bad-bg:#3a1d1b;--accent:#9db8e6}}
:root[data-theme="dark"]{--bg:#141518;--surface:#1c1e22;--ink:#e9e9ea;--muted:#a0a5ad;--line:#2d3036;
--soft:#24272c;--ok:#5fc48f;--ok-bg:#173226;--care:#e3a649;--care-bg:#352a17;--bad:#f08078;--bad-bg:#3a1d1b;
--accent:#9db8e6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:1600px;margin:0 auto;padding:36px clamp(16px,3vw,48px) 64px}
h1{font-size:26px;line-height:1.2;margin:4px 0 14px}
h2{font-size:19px;margin:0 0 4px}h2 .n{color:var(--accent);margin-right:8px}
h3{font-size:14px;margin:18px 0 8px}
.eyebrow{font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
.meta{color:var(--muted);font-size:13px;margin:0}
.summary{margin:18px 0 0;padding:14px 18px;background:var(--surface);border:1px solid var(--line);
border-left:5px solid var(--accent);border-radius:10px;font-size:16px;font-weight:600}
section{margin-top:40px}
.sub{color:var(--muted);margin:0 0 14px;max-width:110ch}
.ok-msg{margin:0;padding:10px 14px;border-radius:8px;background:var(--ok-bg);color:var(--ok);font-weight:600}
.bands{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:0 0 6px}
.band{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 16px}
.band b{display:block;font-size:24px;font-variant-numeric:tabular-nums}
.band span{color:var(--muted);font-size:13px}
.table-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;font-weight:600;color:var(--muted);background:var(--soft)}
tr:last-child td{border-bottom:0}.n{font-variant-numeric:tabular-nums;white-space:nowrap}
tr.cat td{background:var(--soft);font-weight:700;font-size:13px}
.pill{display:inline-block;white-space:nowrap;font-size:12px;font-weight:700;padding:2px 10px;
border-radius:999px;color:var(--c);background:var(--c-bg)}
.ok{--c:var(--ok);--c-bg:var(--ok-bg)}.care{--c:var(--care);--c-bg:var(--care-bg)}
.bad{--c:var(--bad);--c-bg:var(--bad-bg)}
.muted{color:var(--muted)}.bad-text{color:var(--bad)}.care-text{color:var(--care)}
details summary{cursor:pointer;white-space:nowrap}details[open] summary{margin-bottom:4px}
.detail>div+div{margin-top:6px}ul.why{margin:2px 0 0;padding-left:18px}ul.why li{margin:2px 0}
td.usable{width:34%}td.detail{min-width:340px}
.funds{display:flex;flex-wrap:wrap;gap:2px 14px;font-size:13px;color:var(--ink)}
ol.actions{margin:8px 0 0;padding-left:22px;max-width:120ch}ol.actions li{margin:6px 0}
footer{margin-top:44px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
@media print{body{background:#fff}section{break-inside:avoid}}
"""


def _e(x: object) -> str:
    """HTML-escape any value."""
    return escape(str(x))


def _table(head: list[str], rows: list[str]) -> str:
    """Build a table in the standard frame; `rows` are complete <tr> elements."""
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join(rows)
    return f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _step1(d: ReportData) -> str:
    ref = d.reference
    n_ext = int((ref["status"] != MISSING_FROM_EXTRACT).sum())
    unlisted = ref[ref["status"] == NOT_IN_REFERENCE]
    missing = ref[ref["status"] == MISSING_FROM_EXTRACT]
    if unlisted.empty and missing.empty:
        body = (f'<p class="ok-msg">All {n_ext} funds in the extract are in the reference list, and every '
                'fund in the reference list is in the extract.</p>')
    else:
        body = ""
        if len(unlisted):
            body += (f"<h3>In the extract but not in the reference ({len(unlisted)})</h3>"
                     + _table(["Fund"], [f"<tr><td>{_e(f)}</td></tr>" for f in unlisted["fund"]]))
        else:
            body += f"<p class=\"ok-msg\">All {n_ext} funds in the extract are in the reference list.</p>"
        if len(missing):
            body += (f"<h3>In the reference but missing from the extract ({len(missing)})</h3>"
                     + _table(["GP", "Fund"], [f"<tr><td>{_e(g)}</td><td>{_e(f)}</td></tr>"
                                              for g, f in zip(missing["gp"], missing["fund"], strict=True)]))
    return ('<section id="step1"><h2><span class="n">1</span>Reference check</h2>'
            '<p class="sub">Every fund in the extract should be in the GP reference list, and every fund in '
            f'the list should be in the extract.</p>{body}</section>')


def _step2(d: ReportData) -> str:
    comp = d.completeness
    total = int(comp["total"].iloc[0]) if len(comp) else 0
    tiles = "".join(f'<div class="band"><b>{int((comp["band"] == b).sum())}</b><span>funds {_e(b)} '
                    "complete</span></div>" for b in d.bands)
    tables = ""
    for b in d.bands:
        rows = comp[comp["band"] == b]
        if rows.empty:
            continue
        trs = [f'<tr><td>{_e(r["gp"])}</td><td>{_e(r["fund"])}</td>'
               f'<td class="n">{int(r["with_values"])} / {total}</td>'
               f'<td class="n">{r["share"] * 100:.0f}%</td>'
               f'<td>{_e(r["missing_datapoints"]) or "<span class=muted>—</span>"}</td></tr>'
               for r in rows.to_dict("records")]
        tables += (f"<h3>{_e(b)} complete ({plural(len(rows), 'fund')})</h3>"
                   + _table(["GP", "Fund", "With values", "Share", "Missing datapoints"], trs))
    return ('<section id="step2"><h2><span class="n">2</span>Completeness</h2>'
            f'<p class="sub">Datapoints with a value out of the full list of {total}, per fund. '
            'A zero counts as a value, and so does no bad debt (Bad debts not reported or blank). '
            'For debt maturities, capital calls and distributions, a blank or zero amount on a schedule date '
            'means nothing falls on that date.</p>'
            f'<div class="bands">{tiles}</div>{tables}</section>')


def _fund_list(funds: list[str]) -> str:
    """Fund names, one per line so names that contain commas stay readable."""
    return "".join(f'<span class="fund">{_e(f)}</span>' for f in funds)


def _usable_for(funds: list[str], n_all: int) -> str:
    """'All N funds' or 'K of N funds', with the names folded into a details element."""
    if not funds:
        return '<span class="muted">None</span>'
    head = f"All {n_all} funds" if len(funds) == n_all else f"{len(funds)} of {n_all} funds"
    return f'<details><summary>{head}</summary><div class="funds">{_fund_list(funds)}</div></details>'


def _reasons(label: str, css: str, groups: list[tuple[str, list[str]]]) -> str:
    """Render a labelled block: each reason first, then the funds it applies to."""
    items = "".join(f'<li><b>{_e(why)}</b> <span class="muted">({plural(len(funds), "fund")})</span>'
                    f'<div class="funds">{_fund_list(funds)}</div></li>' for why, funds in groups)
    return f'<div class="{css}"><b>{label}</b><ul class="why">{items}</ul></div>'


def _step3(d: ReportData) -> str:
    u, top = d.usability, d.bands[0]
    n = int((d.completeness["band"] == top).sum())
    head = ('<section id="step3"><h2><span class="n">3</span>Datapoint usability</h2>'
            f'<p class="sub">For the {plural(n, "fund")} {top} complete: how many report each datapoint, '
            'whether their values are usable, and for which funds. A datapoint is Unusable if any fund\'s '
            'value fails validation, Use with caution if any fund\'s value needs review, and Good otherwise. '
            'A fund with no value is named but does not change the rating.</p>')
    if u.empty:
        return head + (f'<p class="sub">No fund is {_e(top)} complete, '
                       'so no datapoint was assessed.</p></section>')
    counts = " · ".join(f'<span class="pill {R_CLASS[r]}">{int((u["rating"] == r).sum())} {_e(r)}</span>'
                        for r in (GOOD, CAUTION, UNUSABLE))
    rows, cat = [], None
    for r in u.to_dict("records"):
        if r["category"] != cat:
            cat = r["category"]
            rows.append(f'<tr class="cat"><td colspan="5">{_e(cat)}</td></tr>')
        parts = []
        if r["unusable"]:
            parts.append(_reasons("Unusable", "bad-text", r["unusable"]))
        if r["caution"]:
            parts.append(_reasons("Use with caution", "care-text", r["caution"]))
        if r["no_value_funds"]:
            parts.append(_reasons("Not assessed", "muted", [("No value", r["no_value_funds"])]))
        if r["why_usable"]:
            label = "Usable" if not parts else "Usable for the other funds"
            parts.append(f'<div><b>{label}:</b> {_e(r["why_usable"])}.</div>')
        total, valued = int(r["funds_assessed"]), int(r["funds_with_value"])
        rows.append(
            f'<tr><td>{_e(r["datapoint"])}</td>'
            f'<td class="n">{valued} / {total} '
            f'<span class="muted">({r["completeness"] * 100:.0f}%)</span></td>'
            f'<td><span class="pill {R_CLASS[r["rating"]]}">{_e(r["rating"])}</span></td>'
            f'<td class="usable">{_usable_for(r["usable_funds"], total)}</td>'
            f'<td class="detail">{"".join(parts)}</td></tr>')
    table = _table(["Datapoint", "Funds with value", "Rating", "Usable for", "Why"], rows)
    return head + f"<p>{counts}</p>" + table + "</section>"


def _actions(d: ReportData) -> str:
    body = ("".join(f"<li>{_e(a)}</li>" for a in d.actions) if d.actions
            else "<li>None: no fund, datapoint or value needs following up.</li>")
    return f'<section id="actions"><h2>Actions for managers</h2><ol class="actions">{body}</ol></section>'


def render_html(d: ReportData, generated: pd.Timestamp) -> str:
    """Full HTML document for one quarter's report."""
    title = f"Custom RE {d.period_label} Data Quality"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(title)}</title><style>{CSS}</style></head><body><main>"
        f'<div class="eyebrow">Data quality · {_e(d.period_label)}</div><h1>Custom RE fund KPI extract</h1>'
        f'<p class="meta">Source {_e(d.source_name)} · reporting date {d.reporting_date:%d %b %Y} · '
        f'checks: {plural(d.counts["error"], "error")}, {plural(d.counts["warning"], "warning")} · generated '
        f'{generated:%d %b %Y %H:%M}</p>'
        f'<p class="summary" id="summary">{_e(d.summary)}</p>'
        f"{_step1(d)}{_step2(d)}{_step3(d)}{_actions(d)}"
        "<footer>Full detail (every check result, per fund ratings and notes) is in "
        "<code>dq_report.xlsx</code> in the same folder.</footer></main></body></html>"
    )


def write_html(path: Path, d: ReportData, generated: pd.Timestamp | None = None) -> None:
    """Write the report to `path`, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(d, generated or pd.Timestamp.now()), encoding="utf-8")
