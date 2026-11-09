"""The three data quality steps: reference check, completeness bands, datapoint usability.

Step 1 matches the extract's funds to the GP reference both ways. Step 2 counts, per fund, the datapoints
that have a value (a zero, or "none" for a none-when-missing datapoint, counts) and places the fund in a
completeness band. Step 3 takes the funds in the top band and rates each datapoint Good, Use with caution
or Unusable from the validity and consistency checks, naming the funds behind each rating. Everything is
per fund, so amounts in different currencies are never combined.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

import pandas as pd

from custom_re.completeness import NO_VALUE, NOT_REPORTED, PARTIAL, ZERO_ONLY, datapoint_states
from custom_re.facts import (
    CALLS,
    COUNTRY,
    DEBT,
    END_IP,
    END_TERM,
    REGION_L1,
    REGION_L2,
    anonymised_countries,
    any_positive,
    date_col,
    exact_debt,
)
from custom_re.loader import fund_info_wide
from custom_re.reference import UNMAPPED, gp_map

Rules = dict[str, Any]

# Step 1 statuses.
IN_REFERENCE, NOT_IN_REFERENCE = "In reference", "Not in reference"
MISSING_FROM_EXTRACT = "Missing from extract"
REFERENCE_COLUMNS = ["status", "gp", "fund"]
NO_GP = "(GP blank in reference)"

# Step 2.
COMPLETENESS_COLUMNS = ["band", "gp", "fund", "currency", "with_values", "total", "share",
                        "missing_datapoints"]
GAPS = (NOT_REPORTED, NO_VALUE)  # states with no value; everything else (incl. zero and none) has one

# Step 3 ratings, best first.
GOOD, CAUTION, UNUSABLE = "Good", "Use with caution", "Unusable"
RATINGS = [GOOD, CAUTION, UNUSABLE]
_WORST = {GOOD: 0, CAUTION: 1, UNUSABLE: 2}
USABILITY_COLUMNS = ["category", "datapoint", "rating", "unusable_funds", "caution_funds", "no_value_funds",
                     "reasons"]
DETAIL_COLUMNS = ["gp", "fund", "category", "datapoint", "rating", "reasons", "notes"]
# Gap checks are covered by step 2 and reference checks by step 1; neither rates a value.
SKIP_CHECKS = {"datapoint_not_reported", "value_missing", "fund_not_in_reference",
               "fund_missing_from_extract", "reference_incomplete_row", "reference_duplicate_fund"}
# Long issue details are shortened in the reasons; the Issues sheet keeps the full text.
MAX_REASON_CHARS = 160


def _dp_categories(rules: Rules) -> dict[str, str]:
    """Datapoint -> category, in catalogue order."""
    return {dp: cat for cat, dps in rules["datapoints"].items() for dp in dps}


def _clip(text: str) -> str:
    """Shorten a reason to MAX_REASON_CHARS, ending with an ellipsis when cut."""
    return text if len(text) <= MAX_REASON_CHARS else text[: MAX_REASON_CHARS - 1].rstrip() + "…"


# ---------------------------------------------------------------- step 1


def reference_check(df: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Every fund in the extract or the reference with its status (REFERENCE_COLUMNS), problems first."""
    gps = gp_map(reference)
    # A fund listed with a blank GP is still listed; check_reference reports the blank GP.
    listed = set(reference["fund"].dropna())
    in_extract = set(df["fund"].dropna())

    def gp(f: str) -> str:
        return gps.get(f, NO_GP if f in listed else UNMAPPED)

    rows = [{"status": IN_REFERENCE if f in listed else NOT_IN_REFERENCE, "gp": gp(f), "fund": f}
            for f in in_extract]
    rows += [{"status": MISSING_FROM_EXTRACT, "gp": gp(f), "fund": f} for f in listed - in_extract]
    order = {NOT_IN_REFERENCE: 0, MISSING_FROM_EXTRACT: 1, IN_REFERENCE: 2}
    out = pd.DataFrame(rows, columns=REFERENCE_COLUMNS)
    return out.assign(_o=out["status"].map(order)).sort_values(["_o", "gp", "fund"]).drop(
        columns="_o").reset_index(drop=True)


# ---------------------------------------------------------------- step 2


def band_of(share: float, bands: list[dict[str, Any]]) -> str:
    """Label of the first band whose `above` threshold the share exceeds (the last band has none)."""
    for b in bands:
        if b["above"] is None or share > b["above"]:
            return str(b["label"])
    return str(bands[-1]["label"])


def completeness(df: pd.DataFrame, rules: Rules, reference: pd.DataFrame) -> pd.DataFrame:
    """Per fund in the extract: datapoints with values against the full list, and its band.

    A datapoint has a value when the fund reports it with a value in at least one record; a zero counts,
    and so does "none" for none-when-missing datapoints (e.g. no bad debt). Ordered by band, then share
    (lowest first, so gaps lead), then fund. Returns COMPLETENESS_COLUMNS.
    """
    dp_cat = _dp_categories(rules)
    funds = sorted(df["fund"].dropna().unique())
    if not funds:
        return pd.DataFrame(columns=COMPLETENESS_COLUMNS)
    states = datapoint_states(df, rules, funds)["state"]
    ref = reference_check(df, reference)
    gps = dict(zip(ref["fund"], ref["gp"], strict=True))
    ccy = df.groupby("fund")["currency"].first()
    bands = rules["completeness_bands"]
    rows = []
    for fund in funds:
        st = states.loc[fund]
        missing = [dp for dp in dp_cat if st.get(dp) in GAPS]
        n = len(dp_cat) - len(missing)
        share = n / len(dp_cat)
        rows.append({"band": band_of(share, bands), "gp": gps.get(fund, UNMAPPED), "fund": fund,
                     "currency": ccy.get(fund), "with_values": n, "total": len(dp_cat), "share": share,
                     "missing_datapoints": ", ".join(missing)})
    out = pd.DataFrame(rows, columns=COMPLETENESS_COLUMNS)
    order = {str(b["label"]): i for i, b in enumerate(bands)}
    return out.assign(_b=out["band"].map(order)).sort_values(
        ["_b", "share", "fund"], ascending=[True, True, True]).drop(columns="_b").reset_index(drop=True)


# ---------------------------------------------------------------- step 3


def usability(df: pd.DataFrame, rules: Rules, issues: pd.DataFrame,
              comp: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rate each datapoint for the funds in the top completeness band.

    Per fund and datapoint: Unusable when a check raises an error on it (or a fund-wide error, such as two
    currencies in one fund), Use with caution when a check raises a warning, Good otherwise. Caveats that
    do not change the rating (zeros, partly blank series, fund-life dates, anonymised or derived figures,
    outliers) are kept as notes. A missing value names the fund but does not change the rating; step 2
    already counts it. Each datapoint takes the worst rating of any fund. An error or warning on a datapoint
    but no single fund (e.g. a label mapping that conflicts across funds) rates the datapoint itself and
    is shown as "(across funds)"; it does not appear in the per fund detail. Any error with a fund but no
    datapoint (today only mixed_currency) makes every value of that fund unusable.

    Returns (per datapoint: USABILITY_COLUMNS, per fund and datapoint: DETAIL_COLUMNS).
    """
    dp_cat = _dp_categories(rules)
    top = str(rules["completeness_bands"][0]["label"])
    funds = list(comp.loc[comp["band"] == top, "fund"])
    gp_of = dict(zip(comp["fund"], comp["gp"], strict=True))
    if not funds:
        return pd.DataFrame(columns=USABILITY_COLUMNS), pd.DataFrame(columns=DETAIL_COLUMNS)
    # Plain dict keyed by (fund, datapoint) so the keys are typed tuples.
    states = cast(
        "dict[tuple[str, str], str]", datapoint_states(df, rules, funds)["state"].to_dict()
    )
    rating: dict[tuple[str, str], str] = defaultdict(lambda: GOOD)
    # Errors and warnings on a datapoint but no single fund (e.g. a mapping conflict across funds).
    dp_level: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in issues[issues["fund"].isna() & issues["datapoint"].isin(list(dp_cat))].to_dict("records"):
        sev = str(r["severity"])
        if sev in ("error", "warning") and r["check"] not in SKIP_CHECKS:
            dp_level[str(r["datapoint"])].append((UNUSABLE if sev == "error" else CAUTION, str(r["check"])))
    reasons: dict[tuple[str, str], list[str]] = defaultdict(list)
    notes: dict[tuple[str, str], list[str]] = defaultdict(list)

    def rate(fund: str, dp: str, level: str, text: str) -> None:
        key = (fund, dp)
        if _WORST[level] > _WORST[rating[key]]:
            rating[key] = level
        reasons[key].append(text)

    # Check results: errors make a value unusable, warnings call for caution, info is a note.
    for r in issues[issues["fund"].isin(funds)].to_dict("records"):
        sev, check, dp, fund = str(r["severity"]), str(r["check"]), r["datapoint"], str(r["fund"])
        if check in SKIP_CHECKS:
            continue
        text = _clip(f"{check}: {r['detail']}")
        if pd.isna(dp):
            if sev == "error":
                for each_dp in dp_cat:
                    rate(fund, each_dp, UNUSABLE, f"fund-wide {text}")
        elif dp in dp_cat:
            if sev == "error":
                rate(fund, dp, UNUSABLE, text)
            elif sev == "warning":
                rate(fund, dp, CAUTION, text)
            else:
                notes[(fund, dp)].append(text)

    # Caveats kept as notes only: they do not change the rating.
    for (fund, dp), state in states.items():
        if state == ZERO_ONLY:
            notes[(fund, dp)].append("Only zero: confirm it means none")
        elif state == PARTIAL:
            notes[(fund, dp)].append("No value in some records")
    report = pd.Timestamp(rules["reporting_date"])
    w = fund_info_wide(df).reindex(funds)
    for fund in w.index[date_col(w, END_TERM) < report]:
        notes[(fund, END_TERM)].append("Fund is past this date: confirm any term extension")
    calls = any_positive(df, CALLS).reindex(w.index, fill_value=False)
    for fund in w.index[(date_col(w, END_IP) < report) & calls]:
        notes[(fund, CALLS)].append("Calls projected after the investment period")
    shared = []
    if anonymised_countries(df, rules["report"]["anonymised_label_pattern"]):
        shared.append((COUNTRY, "Anonymised country labels"))
    if (issues["check"] == "naming_note").any():
        shared += [(REGION_L1, "Holds the sub-region despite the \"Level 1\" name"),
                   (REGION_L2, "Holds the continent despite the \"Level 2\" name")]
    if exact_debt(df, fund_info_wide(df), rules["report"]):
        shared.append((DEBT, "Equals LTV × GAV exactly for every fund: likely derived"))
    for fund in funds:
        for dp, text in shared:
            notes[(fund, dp)].append(text)

    # Per fund and datapoint detail.
    detail = []
    for fund in funds:
        for dp, cat in dp_cat.items():
            key = (fund, dp)
            no_value = states.get(key) in GAPS
            detail.append({
                "gp": gp_of[fund], "fund": fund, "category": cat, "datapoint": dp,
                "rating": "No value" if no_value and rating[key] == GOOD else rating[key],
                "reasons": "; ".join(dict.fromkeys(reasons[key])),
                "notes": "; ".join(dict.fromkeys(notes[key])),
            })
    det = pd.DataFrame(detail, columns=DETAIL_COLUMNS)

    # Per datapoint: worst rating, with the funds behind it.
    rows = []
    for dp, cat in dp_cat.items():
        d = det[det["datapoint"] == dp]
        unusable = list(d.loc[d["rating"] == UNUSABLE, "fund"])
        caution = list(d.loc[d["rating"] == CAUTION, "fund"])
        empty = list(d.loc[d["rating"] == "No value", "fund"])
        levels = [lvl for lvl, _ in dp_level[dp]]
        worst = (UNUSABLE if unusable or UNUSABLE in levels
                 else CAUTION if caution or CAUTION in levels else GOOD)
        # Distinct causes, by check name, so the row stays short.
        raw_causes = d.loc[d["rating"].isin([UNUSABLE, CAUTION]), "reasons"].str.split("; ").explode()
        causes = [*raw_causes.dropna().str.split(":").str[0].str.removeprefix("fund-wide "),
                  *(f"{c} (across funds)" for _, c in dp_level[dp])]
        rows.append({"category": cat, "datapoint": dp, "rating": worst, "unusable_funds": ", ".join(unusable),
                     "caution_funds": ", ".join(caution), "no_value_funds": ", ".join(empty),
                     "reasons": ", ".join(dict.fromkeys(causes))})
    return pd.DataFrame(rows, columns=USABILITY_COLUMNS), det
