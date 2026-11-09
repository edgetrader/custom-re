"""Render the one-quarter management data quality report as a self-contained HTML page."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import cast

import pandas as pd

from custom_re.completeness import NO_VALUE, NONE, NOT_REPORTED, PARTIAL, REPORTED, ZERO_ONLY
from custom_re.insights import CARE, EMPTY, NOT_USABLE, RATINGS, READY, ReportData, ratings_count, slug
from custom_re.stages import NOT_ASSESSED, USABILITY, USABLE

RATING_TEXT = {
    READY: "usable as reported, for single funds and across funds.",
    CARE: "accurate for a single fund, but needs conversion, alignment or a stated caveat before "
          "comparing or totalling.",
    NOT_USABLE: "missing or failing checks; do not use until corrected at source.",
    EMPTY: "the field exists in the extract but holds no values.",
}
TONE_LABEL = {"good": "Ready", "warn": "Caveats", "bad": "Not ready"}
# Stage 2 rating -> CSS class (colour) and a glyph, so the heatmap does not rely on colour alone.
U_CLASS = {USABLE: "u-ok", CARE: "u-care", NOT_USABLE: "u-bad", NOT_ASSESSED: "u-na"}
U_GLYPH = {USABLE: "", CARE: "!", NOT_USABLE: "✕", NOT_ASSESSED: ""}
U_TEXT = {
    USABLE: "no check or caveat limits the value.",
    CARE: "usable for this fund with the stated caveat (a zero to confirm, a warning, a derived or "
          "anonymised figure).",
    NOT_USABLE: "missing or failing a check; correct at source before use.",
}

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
main{max-width:1760px;margin:0 auto;padding:40px clamp(16px,3vw,48px) 64px}
.sub,.verdict p,.finding p,.defs,.cols ul{max-width:110ch}
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
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;font-weight:600;color:var(--muted);background:var(--soft)}th.n{white-space:nowrap}
tr:last-child td{border-bottom:0}td.n{font-variant-numeric:tabular-nums;white-space:nowrap}
tr.cat td{background:var(--soft);font-weight:700;font-size:13px}
.pill{display:inline-block;white-space:nowrap;font-size:12px;font-weight:600;color:var(--c);background:var(--c-bg);
padding:2px 9px;border-radius:999px}
.st{font-weight:700;font-size:12px}.st-PASS{color:var(--good)}.st-INFO{color:var(--info)}.st-FLAG{color:var(--bad)}
.muted{color:var(--muted)}ol.steps{margin:12px 0 0;padding-left:22px}ol.steps li{margin:6px 0}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px 32px}
.cols ul{margin:6px 0 0;padding-left:20px;color:var(--muted)}h3.small{font-size:14px;margin:0}
.gap-top,h3.gap-top{margin-top:24px}.gap-sm{margin-top:8px}
.note{margin:10px 0 0;font-size:14px;color:var(--bad)}.small-text{font-size:12px}
details.inline{margin:0}details.inline summary{font-weight:400}.bad-text{color:var(--bad);font-weight:600}
.dist{display:grid;gap:6px;margin:14px 0 18px;max-width:640px}
.dist div{display:grid;grid-template-columns:110px 1fr 70px;align-items:center;gap:10px;font-size:14px}
.track{height:12px;background:var(--soft);border-radius:999px;overflow:hidden}
.track i{display:block;height:100%;background:var(--accent);border-radius:999px}
.dist .n{color:var(--muted);font-variant-numeric:tabular-nums}
.meter{display:flex;align-items:center;gap:8px;min-width:150px}.meter .track{flex:1}
details{margin-top:12px}summary{cursor:pointer;font-weight:600;font-size:14px}
summary h2.inline{display:inline;font-size:20px}
.t1{--c:var(--good);--c-bg:var(--good-bg)}.t2{--c:var(--info);--c-bg:var(--info-bg)}
.t3{--c:var(--warn);--c-bg:var(--warn-bg)}.t4{--c:var(--bad);--c-bg:var(--bad-bg)}
.u-ok{--c:var(--good);--c-bg:var(--good-bg)}.u-care{--c:var(--warn);--c-bg:var(--warn-bg)}
.u-bad{--c:var(--bad);--c-bg:var(--bad-bg)}.u-na{--c:var(--empty);--c-bg:var(--empty-bg)}
.bar i.t1,.bar i.t2,.bar i.t3,.bar i.t4,.bar i.u-ok,.bar i.u-care,.bar i.u-bad,.stack i{background:var(--c)}
.tier{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:6px;font-size:12px;
font-weight:700;color:var(--c);background:var(--c-bg)}
.track.stack{display:flex}.track.stack i{display:block;height:100%;border-radius:0}
table.heat{table-layout:fixed;border-collapse:separate;border-spacing:2px;font-size:12px}
table.heat th,table.heat td{border:0;padding:0}
table.heat th.fn{padding:4px 10px 4px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
font-weight:500;color:var(--ink);background:none;font-size:12px;position:sticky;left:0;background:var(--surface);z-index:1}
table.heat th.hc{padding:6px 2px;text-align:center;border-bottom:1px solid var(--line);font-size:11px;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
table.heat th.hv{vertical-align:bottom;padding:4px 0;height:170px;background:none}
table.heat th.hv span{display:block;writing-mode:vertical-rl;transform:rotate(180deg);max-height:165px;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:0 auto;font-weight:500}
table.heat td.c{height:20px;border-radius:4px;background:var(--c);color:#1d1f23;text-align:center;
font-size:11px;font-weight:700;line-height:20px}
table.heat td.u-ok{opacity:.55}table.heat td.n{padding:0 6px;text-align:right;color:var(--muted)}
table.heat tr.cat td{padding:8px 0 2px;background:none;font-size:12px}
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


def _fund_row(r: dict[str, object]) -> str:
    """One fund row: rank, tier, fund, currency, answered vs non-zero datapoints, records, gaps."""
    tier = int(cast(int, r["tier"]))
    total = int(cast(int, r["datapoints_total"]))
    # A fund missing from the extract has no currency (NaN).
    ccy = _e(r["currency"]) if isinstance(r["currency"], str) else "—"
    # Status first when the fund is not matched to the reference; then missing and zero-only datapoints.
    parts = [f'<span class="bad-text">{_e(r["status"])}</span>'] if r["status"] != REPORTED else []
    if r["missing_datapoints"]:
        parts.append(f"<b>Missing:</b> {_e(r['missing_datapoints'])}")
    if r["zero_datapoints"]:
        parts.append(f"<b>Only zero:</b> {_e(r['zero_datapoints'])}")
    return (
        f'<tr><td class="n">{int(cast(int, r["rank"]))}</td>'
        f'<td><span class="tier t{tier}" title="{_e(r["tier_label"])}">{tier}</span></td>'
        f"<td>{_e(r['fund'])}</td><td>{ccy}</td>"
        f'<td class="n">{int(cast(int, r["datapoints_answered"]))} / {total}</td>'
        f'<td class="n">{int(cast(int, r["datapoints_with_value"]))} / {total}</td>'
        f'<td class="n">{_pct(float(cast(float, r["value_share"])))}</td>'
        f"<td>{'. '.join(parts) or '<span class=muted>—</span>'}</td></tr>"
    )


FUND_HEAD = ("<th>Rank</th><th>Tier</th><th>Fund</th><th>Ccy</th>"
             '<th class="n" title="A value, a zero, or none where allowed">Answered</th>'
             '<th class="n">Non-zero</th><th class="n">Records non-zero</th><th>Gaps</th>')


def _funds_by_gp(d: ReportData) -> str:
    """Every fund, grouped under its GP in GP rank order, funds by rank within each GP."""
    fc = d.fund_completeness
    body = []
    for gp in d.gp_completeness["gp"]:
        funds = fc[fc["gp"] == gp]
        body.append(f'<tr class="cat"><td colspan="8">{_e(gp)} · {_plural_funds(len(funds))}</td></tr>')
        body += [_fund_row(r) for r in cast("list[dict[str, object]]", funds.to_dict("records"))]
    return (f'<div class="table-wrap"><table><thead><tr>{FUND_HEAD}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table></div>")


def _bar(parts: list[tuple[str, int, str]], label: str) -> str:
    """Stacked share bar with a legend; parts are (name, count, css class), zero counts left out."""
    total = sum(n for _, n, _ in parts) or 1
    shown = [(k, n, c) for k, n, c in parts if n]
    bar = "".join(f'<i class="{c}" style="width:{n / total * 100:.2f}%" title="{_e(k)}: {n}"></i>'
                  for k, n, c in shown)
    legend = "".join(f'<li><span class="dot {c}"></span><b>{n}</b> {_e(k)}</li>' for k, n, c in shown)
    return (f'<div class="bar" role="img" aria-label="{_e(label)}">{bar}</div>'
            f'<ul class="legend">{legend}</ul>')


def _tier_rule(d: ReportData, tier: int) -> str:
    """Plain-language conditions of one tier, from the rules."""
    if tier == 4:
        return "below tier 3, or missing from the extract"
    t = d.tiers[f"tier{tier}"]
    share = float(t["min_answered"])
    parts = ["every datapoint answered" if share >= 1 else f"at least {share:.0%} of datapoints answered"]
    if "max_error_datapoints" in t:
        n = int(t["max_error_datapoints"])
        parts.append("no errors" if n == 0 else f"errors on at most {n} datapoint{'s' if n != 1 else ''}")
    if t.get("all_categories"):
        parts.append("every category reported")
    if tier <= 2 and d.tiers["core_datapoints"]:
        parts.append("all core datapoints answered")
    return ", ".join(parts)


def _stage1(d: ReportData) -> str:
    fc, gc = d.fund_completeness, d.gp_completeness
    if fc.empty:
        return ""
    labels = {int(k): v for k, v in d.tiers["labels"].items()}
    top = int(d.tiers["analysis_max_tier"])
    counts = {t: int((fc["tier"] == t).sum()) for t in (1, 2, 3, 4)}
    n_ok = sum(counts[t] for t in counts if t <= top)
    bar = _bar([(f"Tier {t} · {labels[t]}", counts[t], f"t{t}") for t in (1, 2, 3, 4)], "Funds by tier")
    defs = "".join(f'<li><span class="tier t{t}">{t}</span> <b>{_e(labels[t])}</b>: {_e(_tier_rule(d, t))}'
                   f'</li>' for t in (1, 2, 3, 4))
    total = int(fc["datapoints_total"].iloc[0])

    # GP table: tier profile first, then the completeness detail behind it.
    rows = []
    for r in gc.to_dict("records"):
        expected, reported = int(r["funds_expected"]), int(r["funds_reported"])
        n_funds = sum(int(r[f"funds_tier_{t}"]) for t in (1, 2, 3, 4)) or 1
        mini = "".join(f'<i class="t{t}" style="width:{int(r[f"funds_tier_{t}"]) / n_funds * 100:.1f}%"></i>'
                       for t in (1, 2, 3, 4) if r[f"funds_tier_{t}"])
        split = " · ".join(str(int(r[f"funds_tier_{t}"])) for t in (1, 2, 3, 4))
        funds_txt = f"{reported} / {expected}" if expected else f"{reported} unlisted"
        short = ' class="n bad-text"' if reported < expected or not expected else ' class="n"'
        mean = float(r["mean_datapoints_with_value"])
        avg = f"{mean:.1f} / {total}" if reported else "—"
        issues = (f'{int(r["errors"])} · {int(r["warnings"])}' if r["errors"] or r["warnings"]
                  else '<span class="muted">—</span>')
        rows.append(
            f'<tr><td class="n">{int(r["rank"])}</td><td>{_e(r["gp"])}</td><td{short}>{funds_txt}</td>'
            f'<td><span class="meter"><span class="track stack">{mini}</span>'
            f'<span class="n">{split}</span></span></td>'
            f'<td class="n">{int(r["funds_analysable"])} ({_pct(float(r["analysable_share"]))})</td>'
            f'<td class="n">{avg}</td><td class="n">{issues}</td><td>{_e(r["currencies"]) or "—"}</td></tr>'
        )
    gp_table = (
        '<div class="table-wrap gap-sm"><table><thead><tr><th>Rank</th><th>GP</th>'
        '<th class="n">Funds reported</th>'
        '<th>Funds by tier (1 · 2 · 3 · 4)</th><th class="n">Analysable</th>'
        '<th class="n">Avg datapoints with value</th><th class="n">Errors · warnings</th><th>Currencies</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )

    low = fc[fc["tier"] > top]
    low_rows = "".join(
        f'<tr><td><span class="tier t{int(r["tier"])}">{int(r["tier"])}</span></td><td>{_e(r["gp"])}</td>'
        f'<td>{_e(r["fund"])}</td><td class="n">{int(r["datapoints_answered"])} / {total}</td>'
        f'<td>{_e(r["tier_reason"])}</td></tr>'
        for r in low.to_dict("records"))
    low_html = (
        f'<h3 class="small gap-top">Funds below tier {top}: left out of Stage 2</h3>'
        '<div class="table-wrap gap-sm"><table><thead><tr><th>Tier</th><th>GP</th><th>Fund</th>'
        f'<th class="n">Answered</th><th>What keeps it from a higher tier</th></tr></thead><tbody>{low_rows}'
        '</tbody></table></div>'
        if len(low) else
        f'<p class="gap-top">Every reported fund reaches tier {top} or better, so all go on to Stage 2.</p>')
    return (
        f'<section id="stage1"><div class="eyebrow">Stage 1</div><h2>Completeness by GP and fund: {n_ok} of '
        f'{len(fc)} funds are complete enough to analyse</h2>'
        '<p class="sub">Each fund is placed in one of four tiers by how many datapoints it answers (a value, '
        'a zero, or "none" where that is allowed), whether whole categories are missing, and how many '
        f'datapoints carry errors. Funds in tiers 1–{top} go on to the datapoint analysis in Stage 2. '
        'GPs are ranked by the share of their funds that are analysable. '
        'Only counts are combined, never amounts.</p>'
        f'{bar}<ul class="defs">{defs}</ul>'
        f'<h3 class="small gap-top">GPs</h3>{gp_table}{low_html}'
        f"<details><summary>All {len(fc)} funds by GP, with their gaps</summary>{_funds_by_gp(d)}</details>"
        '</section>'
    )


def _stage2(d: ReportData) -> str:
    u = d.usability
    a = u[u["rating"] != NOT_ASSESSED]
    top = int(d.tiers["analysis_max_tier"])
    head = '<section id="stage2"><div class="eyebrow">Stage 2</div>'
    if a.empty:
        return (head + '<h2>Usability by fund and datapoint</h2><p class="sub">No fund reaches tier '
                f'{top}, so no datapoint analysis is possible this quarter.</p></section>')
    n_funds = a["fund"].nunique()
    counts = {r: int((a["rating"] == r).sum()) for r in USABILITY}
    bar = _bar([(r, counts[r], U_CLASS[r]) for r in USABILITY], "Fund datapoints by usability")
    defs = "".join(f'<li><span class="dot {U_CLASS[r]}"></span><b>{_e(r)}</b>: {_e(U_TEXT[r])}</li>'
                   for r in USABILITY)

    # Per datapoint: how many analysable funds can use it, and why not.
    dp_rows, cat = [], None
    for r in d.usability_by_datapoint.to_dict("records"):
        if r["category"] != cat:
            cat = r["category"]
            dp_rows.append(f'<tr class="cat"><td colspan="3">{_e(cat)}</td></tr>')
        n = sum(int(r[k]) for k in USABILITY) or 1
        mini = "".join(f'<i class="{U_CLASS[k]}" style="width:{int(r[k]) / n * 100:.1f}%"></i>'
                       for k in USABILITY if r[k])
        split = " · ".join(str(int(r[k])) for k in USABILITY)
        dp_rows.append(
            f'<tr><td>{_e(r["datapoint"])}</td><td><span class="meter">'
            f'<span class="track stack">{mini}</span>'
            f'<span class="n">{split}</span></span></td>'
            f'<td>{_e(r["reasons"]) or "<span class=muted>—</span>"}</td></tr>')
    dp_table = (
        '<div class="table-wrap gap-sm"><table><thead><tr><th>Datapoint</th>'
        '<th>Funds: usable · with care · not usable</th><th>Main reasons (funds)</th></tr></thead>'
        f'<tbody>{"".join(dp_rows)}</tbody></table></div>'
    )
    return (head + f'<h2>Usability by fund and datapoint: {_pct(counts[USABLE] / len(a))} of values '
            f'usable as reported</h2><p class="sub">Every datapoint of the {n_funds} funds in tiers '
            f'1–{top} is rated for that fund alone, so currencies never mix. '
            'The grid below shows each fund; the workbook\'s Usability sheet lists every rating '
            'with its reasons.</p>'
            f'{bar}<ul class="defs">{defs}</ul>'
            f'<h3 class="small gap-top">By datapoint</h3>{dp_table}'
            f'<h3 class="small gap-top">By fund</h3>{_heatmap(d)}</section>')


def _heatmap(d: ReportData) -> str:
    """Fund × datapoint grid of ratings, funds grouped by GP; hover a cell for its reasons."""
    u = d.usability[d.usability["rating"] != NOT_ASSESSED]
    dps = list(dict.fromkeys(d.usability["datapoint"]))
    cats = d.usability.drop_duplicates("datapoint").set_index("datapoint")["category"]
    groups = list(dict.fromkeys(cats[dp] for dp in dps))
    cat_head = "".join(f'<th colspan="{sum(cats[dp] == c for dp in dps)}" class="hc" title="{_e(c)}">'
                       f'{_e(c)}</th>' for c in groups)
    # Fixed column widths keep every cell square; long category names are clipped (full name on hover).
    cols = ('<colgroup><col style="width:210px">' + '<col style="width:22px">' * len(dps)
            + '<col style="width:34px">' * 3 + "</colgroup>")
    width = 210 + 22 * len(dps) + 34 * 3
    dp_head = "".join(f'<th class="hv" title="{_e(dp)}"><span>{_e(dp)}</span></th>' for dp in dps)
    cell = {(f, dp): (r, why) for f, dp, r, why in u[["fund", "datapoint", "rating", "reasons"]].itertuples(
        index=False)}
    order = d.usability_by_fund
    body = []
    for gp in d.gp_completeness["gp"]:
        funds = order[order["gp"] == gp]
        if funds.empty:
            continue
        body.append(f'<tr class="cat"><td colspan="{len(dps) + 4}">{_e(gp)}</td></tr>')
        for r in funds.to_dict("records"):
            f = r["fund"]
            cells = []
            for dp in dps:
                rating, why = cell[(f, dp)]
                label = f"{dp}: {rating}" + (f" — {why}" if why else "")
                cells.append(f'<td class="c {U_CLASS[rating]}" title="{_e(label)}" aria-label="{_e(label)}">'
                             f"{U_GLYPH[rating]}</td>")
            cells_html = "".join(cells)
            body.append(f'<tr><th class="fn" scope="row">{_e(f)}</th>{cells_html}'
                        + "".join(f'<td class="n">{int(r[k])}</td>' for k in USABILITY) + "</tr>")
    return (
        f'<div class="table-wrap gap-sm"><table class="heat" style="width:{width}px">{cols}<thead>'
        f'<tr><th rowspan="2" class="fn">Fund</th>{cat_head}<th colspan="3" class="hc">Count</th></tr>'
        f'<tr>{dp_head}<th class="hv"><span>Usable</span></th><th class="hv"><span>With care</span></th>'
        '<th class="hv"><span>Not usable</span></th></tr>'
        f'</thead><tbody>{"".join(body)}</tbody></table></div>'
        '<p class="muted small-text gap-sm">! = use with care, ✕ = not usable, blank = usable. Hover a cell '
        'for the datapoint and the reason for its rating.</p>'
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
            f'<span class="n">{valued} / {total}</span></span></td><td class="n">{share}</td>'
            + "".join(f'<td class="n">{int(r[c]) or "—"}</td>'
                      for c in ("funds_not_reported", "funds_no_value", "funds_partial", "funds_zero_only",
                                "funds_none"))
            + "</tr>"
        )
    return "".join(out)


def _dp_table(dc: pd.DataFrame) -> str:
    """Datapoint completeness table."""
    return ('<div class="table-wrap"><table><thead><tr><th>Datapoint</th><th>Category</th>'
            '<th>Funds reporting</th>'
            '<th>Funds with value</th><th>Records with value</th><th>Not reported</th><th>No value</th>'
            '<th>No value in some records</th><th>Only zero</th><th>Treated as none</th></tr></thead>'
            f"<tbody>{_dp_rows(dc)}</tbody></table></div>")


def _fund_list(funds: list[str], limit: int = 4) -> str:
    """Comma-separated fund names; a longer list folds into a details element with a count."""
    if not funds:
        return '<span class="muted">—</span>'
    names = ", ".join(_e(f) for f in funds)
    if len(funds) <= limit:
        return names
    return f'<details class="inline"><summary>{_plural_funds(len(funds))}</summary>{names}</details>'


def _datapoint_completeness(d: ReportData) -> str:
    dc, gaps = d.datapoint_completeness, d.gaps
    if dc.empty:
        return ""
    funds_by = {k: sorted(g["fund"]) for k, g in gaps.groupby(["datapoint", "gap"], sort=False)}
    # Funds with some records blank (e.g. series periods) share the "No value" column, marked as such.
    for (dp, g), funds in list(funds_by.items()):
        if g == PARTIAL:
            funds_by[(dp, NO_VALUE)] = sorted(funds_by.get((dp, NO_VALUE), [])
                                              + [f"{f} (some records)" for f in funds])
    n_full = int((dc["funds_with_value"] == dc["funds_total"]).sum())
    # Datapoints with a fund lacking a value, or with zero periods/positions inside a series.
    gap_cols = ["funds_not_reported", "funds_no_value", "funds_partial", "funds_zero_only"]
    short = dc[(dc[gap_cols].sum(axis=1) > 0) | (dc["records_with_value"] < dc["records"])]
    rows = []
    for r in short.to_dict("records"):
        dp, total, valued = r["datapoint"], int(r["funds_total"]), int(r["funds_with_value"])
        width = valued / total * 100 if total else 0.0
        recs = int(r["records"])
        share = _pct(int(r["records_with_value"]) / recs) if recs else "—"
        cells = "".join(f"<td>{_fund_list(funds_by.get((dp, g), []))}</td>"
                        for g in (NOT_REPORTED, NO_VALUE, ZERO_ONLY))
        rows.append(
            f'<tr><td>{_e(dp)}<div class="muted small-text">{_e(r["category"])}</div></td>'
            f'<td><span class="meter"><span class="track"><i style="width:{width:.1f}%"></i></span>'
            f'<span class="n">{valued} / {total}</span></span></td><td class="n">{share}</td>{cells}</tr>'
        )
    table = (
        '<h3 class="small">Funds missing each datapoint or its value</h3><div class="table-wrap gap-sm">'
        '<table><thead><tr><th>Datapoint</th><th>Funds with value</th><th>Records with value</th>'
        '<th>Not reported</th><th>No value (blank, null or NA)</th><th>Reported only as zero</th></tr>'
        f'</thead><tbody>{"".join(rows)}</tbody></table></div>' if rows else "")
    # "None when missing" datapoints: an answer, not a gap, but the funds are named so the
    # assumption can be checked.
    none_notes = "".join(
        f'<p class="gap-sm"><b>{_e(dp)}</b>: {_e(g["note"].iloc[0].lower())} for {_plural_funds(len(g))} '
        f'(not reported, blank or zero), treated as none rather than a gap: '
        f'{_fund_list(sorted(g["fund"]))}.</p>'
        for dp, g in gaps[gaps["gap"] == NONE].groupby("datapoint", sort=False)
    )
    # "None when missing" datapoints are described by their own note, not as fully valued.
    n_rest = int((~dc.index.isin(short.index) & (dc["funds_none"] == 0)).sum())
    lead = (f"<p>The other {n_rest} datapoints have a non-zero value in every record for every fund.</p>"
            if n_rest else "")
    return (
        f'<section id="datapoint-completeness"><div class="eyebrow">Stage 1</div><h2>'
        f'Datapoint completeness: {n_full} of {len(dc)} '
        'have a value for every fund</h2><p class="sub">Not every fund is expected to report every '
        'datapoint. For each datapoint below, the funds are named by kind of gap: not reported at all, '
        'reported with no value (blank, null or NA), or reported only as zero, which may be genuine. '
        '"Records with value" is the share of records that are non-zero: for time series it shows how many '
        f'periods carry a figure.</p>{table}{none_notes}{lead}'
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
        '<section id="register"><details><summary><h2 class="inline">Datapoint register across all funds'
        '</h2></summary><p class="sub gap-sm">One rating per datapoint across every fund (Stage 2 rates each '
        'fund separately). Coverage counts the funds reporting the item. Ranges are across funds as '
        'reported; amounts are in each fund\'s own currency (k = thousand, m = million, bn = billion).</p>'
        f'<div class="filters" role="group" aria-label="Filter by usability">{"".join(buttons)}</div>'
        '<div class="table-wrap"><table><thead><tr><th>Datapoint</th><th>Coverage</th><th>Observed range</th>'
        f'<th>Usability</th><th>Notes</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>{unassigned}'
        '</details></section>'
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
        '<li>Fund coverage and GP attribution against the GP reference file.</li>'
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
        f"{_verdict(d)}{_figures(d)}{_findings(d)}{_stage1(d)}{_datapoint_completeness(d)}{_stage2(d)}"
        f"{_checks(d)}{_register(d)}"
        f"{_next_steps(d)}{_scope(d)}"
        f'<footer>Generated by <code>app/run_dq.py</code> from {_e(d.source_name)}. Item-level results with '
        'Excel row references are in <code>dq_report.xlsx</code> in the same folder.</footer>'
        f"</main><script>{JS}</script></body></html>"
    )


def write_html(path: Path, d: ReportData, generated: pd.Timestamp | None = None) -> None:
    """Write the report to `path`, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(d, generated or pd.Timestamp.now()), encoding="utf-8")
