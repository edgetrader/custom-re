"""Two-stage data quality view: completeness tiers per fund (stage 1), then usability per fund and datapoint.

Stage 1 sorts every fund into four tiers by how many datapoints it answers, whether whole categories are
missing, and how many datapoints carry errors (thresholds in the rules' `tiers` section). Stage 2 rates
each datapoint of the best-tier funds as usable, use with care or not usable, with the reasons. Both work
per fund, so amounts in different currencies are never combined.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from custom_re.completeness import (
    MISSING,
    NO_VALUE,
    NONE,
    NOT_REPORTED,
    PARTIAL,
    VALUED,
    ZERO_ONLY,
    datapoint_states,
)
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

Rules = dict[str, Any]

# States that answer a datapoint: a value (even with some records blank), a zero, or "none" for a
# none-when-missing datapoint. Partly blank and zero-only values are caveats for stage 2, not gaps.
ANSWERED = [VALUED, NONE, ZERO_ONLY, PARTIAL]
TIER_COLUMNS = ["tier", "tier_label", "analysable", "datapoints_answered", "answered_share",
                "categories_missing", "error_datapoints", "tier_reason"]

USABLE, CARE, NOT_USABLE, NOT_ASSESSED = "Usable", "Use with care", "Not usable", "Not assessed"
USABILITY = [USABLE, CARE, NOT_USABLE]
_WORST = {USABLE: 0, CARE: 1, NOT_USABLE: 2}
# `reason_keys` holds the short cause of each reason (check name or caveat), "|"-separated, so causes can
# be counted per fund without parsing the reason text.
USABILITY_COLUMNS = ["gp", "fund", "tier", "category", "datapoint", "rating", "reasons", "reason_keys"]

# Info-level checks tied to one fund and datapoint that limit how far the value can be relied on. The
# gap checks are left out: the datapoint states already cover them.
CAVEAT_CHECKS = {"iqr_outlier", "occupancy_weighted"}
# Long issue details are shortened in the reasons; the Issues sheet keeps the full text.
MAX_REASON_CHARS = 140


def _catalogue(rules: Rules) -> dict[str, str]:
    """Datapoint -> category, in catalogue order."""
    return {dp: cat for cat, dps in rules["datapoints"].items() for dp in dps}


def _clip(text: str) -> str:
    """Shorten a reason to MAX_REASON_CHARS, ending with an ellipsis when cut."""
    return text if len(text) <= MAX_REASON_CHARS else text[: MAX_REASON_CHARS - 1].rstrip() + "…"


def _names(items: list[str], limit: int = 3) -> str:
    """Up to `limit` names, then a count of the rest."""
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


# ---------------------------------------------------------------- stage 1


def add_tiers(fc: pd.DataFrame, df: pd.DataFrame, rules: Rules, issues: pd.DataFrame) -> pd.DataFrame:
    """Copy of the fund table (fund_completeness) with each fund's completeness tier (TIER_COLUMNS).

    Funds are re-ranked by tier first, then by their completeness rank within the tier.

    A fund takes the best tier whose conditions it meets: share of datapoints answered, error datapoints,
    every category answered and core datapoints answered (tiers 1 and 2). `tier_reason` says what keeps
    the fund out of the tier above.
    """
    cfg = rules["tiers"]
    dp_cat = _catalogue(rules)
    n_total = len(dp_cat)
    out = fc.copy()
    if out.empty:
        return out.assign(**{c: pd.Series(dtype=object) for c in TIER_COLUMNS})

    states = datapoint_states(df, rules, list(out["fund"]))["state"]
    answered = states.isin(ANSWERED)
    # Errors per fund: distinct catalogued datapoints with an error. A fund-wide error (e.g. two
    # currencies in one fund) undermines every value, so it keeps the fund out of tiers 1 and 2.
    errs = issues[(issues["severity"] == "error") & issues["fund"].notna()]
    err_dps = errs[errs["datapoint"].isin(list(dp_cat))].groupby("fund")["datapoint"].nunique()
    fund_wide = (errs[errs["datapoint"].isna()].groupby("fund")["check"]
                 .agg(lambda c: ", ".join(sorted(set(c)))))
    core = list(cfg["core_datapoints"] or [])

    rows = []
    for fund, status in zip(out["fund"], out["status"], strict=True):
        ans = answered.loc[fund]
        unanswered = [dp for dp in dp_cat if not ans.get(dp, False)]
        n_ans = n_total - len(unanswered)
        share = n_ans / n_total if n_total else 0.0
        cats_missing = [c for c in dict.fromkeys(dp_cat.values())
                        if not any(ans.get(dp, False) for dp, cc in dp_cat.items() if cc == c)]
        n_err = int(err_dps.get(fund, 0))
        wide = str(fund_wide.get(fund, ""))
        core_missing = [dp for dp in core if dp in unanswered]

        # Shortfalls against each tier, computed once; the fund lands in the first tier with none.
        shortfalls: dict[int, list[str]] = {}
        for t in (1, 2, 3):
            rule = cfg[f"tier{t}"]
            found: list[str] = []
            if share < rule["min_answered"]:
                found.append(f"{len(unanswered)} of {n_total} datapoints unanswered ({_names(unanswered)})")
            if "max_error_datapoints" in rule and n_err > rule["max_error_datapoints"]:
                found.append(f"errors on {n_err} datapoint{'s' if n_err != 1 else ''}")
            if t <= 2 and wide:
                found.append(f"fund-wide error ({wide})")
            if rule.get("all_categories") and cats_missing:
                found.append(f"no data for {', '.join(cats_missing)}")
            if t <= 2 and core_missing:
                found.append(f"core datapoints missing: {_names(core_missing)}")
            shortfalls[t] = found

        if status == MISSING:
            tier, reason = 4, "Missing from the extract"
        else:
            tier = next((t for t in (1, 2, 3) if not shortfalls[t]), 4)
            reason = ("All datapoints answered, no errors" if tier == 1
                      else "; ".join(shortfalls[tier - 1]))
            reason = reason[:1].upper() + reason[1:]
        rows.append({
            "tier": tier, "tier_label": cfg["labels"][tier],
            "analysable": tier <= int(cfg["analysis_max_tier"]), "datapoints_answered": n_ans,
            "answered_share": share, "categories_missing": ", ".join(cats_missing),
            "error_datapoints": n_err + (1 if wide else 0), "tier_reason": reason,
        })
    tiers = pd.DataFrame(rows, index=out.index)
    out = pd.concat([out, tiers], axis=1)
    # Tier leads the ranking; within a tier the completeness rank still orders funds, ties shared.
    out["rank"] = (out[["tier", "rank"]].apply(tuple, axis=1).rank(method="min").astype(int))
    return out.sort_values(["rank", "fund"], kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- stage 2


def usability(df: pd.DataFrame, rules: Rules, issues: pd.DataFrame, fc: pd.DataFrame) -> pd.DataFrame:
    """Rate every datapoint of every reported fund (USABILITY_COLUMNS); `fc` must carry tiers (add_tiers).

    Funds above the rules' `analysis_max_tier` are "Not assessed". For the rest, a datapoint is not usable
    when it is missing or has an error, use with care when a warning or caveat applies (zero to confirm,
    partly blank, outlier, fund past term, derived or anonymised figures), and usable otherwise.
    """
    report = pd.Timestamp(rules["reporting_date"])
    cfg = rules["report"]
    dp_cat = _catalogue(rules)
    funds_df = fc[fc["status"] != MISSING]
    if funds_df.empty:
        return pd.DataFrame(columns=USABILITY_COLUMNS)
    funds = list(funds_df["fund"])
    states = datapoint_states(df, rules, funds)["state"]
    reasons: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)

    def add(fund: str, dp: str, rating: str, key: str, text: str = "") -> None:
        reasons[(fund, dp)].append((rating, key, text or key))

    # The datapoint's own state.
    none_text = rules["none_when_missing"]
    # Annotated dict gives plain (fund, dp) tuple keys; MultiIndex.items() keys are typed Hashable.
    state_map: dict[tuple[str, str], str] = states.to_dict()  # type: ignore[assignment]
    for (fund, dp), state in state_map.items():
        if state in (NOT_REPORTED, NO_VALUE):
            add(fund, dp, NOT_USABLE,
                "Not reported" if state == NOT_REPORTED else "No value (blank, null or NA)")
        elif state == PARTIAL:
            add(fund, dp, CARE, "No value in some records")
        elif state == ZERO_ONLY:
            add(fund, dp, CARE, "Only zero", "Only zero: confirm it means none")
        elif state == NONE:
            add(fund, dp, USABLE, none_text[dp], f"{none_text[dp]} (treated as none)")

    # Check results on this fund and datapoint. A fund-wide error (e.g. two currencies) undermines
    # every value of the fund.
    for r in issues[issues["fund"].isin(funds)].to_dict("records"):
        sev, check, dp = str(r["severity"]), str(r["check"]), r["datapoint"]
        text = _clip(f"{check}: {r['detail']}")
        if pd.isna(dp):
            if sev == "error":
                for d in dp_cat:
                    add(r["fund"], d, NOT_USABLE, f"Fund-wide {check}", f"Fund-wide error: {text}")
        elif dp in dp_cat:
            if sev == "error":
                add(r["fund"], dp, NOT_USABLE, check, text)
            elif sev == "warning" or check in CAVEAT_CHECKS:
                add(r["fund"], dp, CARE, check, text)

    # Fund-specific caveats from the fund-life dates.
    w = fund_info_wide(df)
    past = date_col(w, END_TERM) < report
    calls = any_positive(df, CALLS).reindex(w.index, fill_value=False)
    late_calls = (date_col(w, END_IP) < report) & calls
    for fund in w.index[past]:
        add(fund, END_TERM, CARE, "Past term end", "Fund is past this date: confirm any term extension")
    for fund in w.index[late_calls]:
        add(fund, CALLS, CARE, "Calls after investment period",
            "Calls projected after the investment period: confirm their purpose")

    # Caveats that apply to every fund's value of a datapoint.
    shared: list[tuple[str, str, str]] = []
    if anonymised_countries(df, cfg["anonymised_label_pattern"]):
        shared.append((COUNTRY, "Anonymised labels",
                       "Anonymised labels: use regions unless a mapping is supplied"))
    if (issues["check"] == "naming_note").any():
        shared += [(REGION_L1, "Level naming", "Holds the sub-region despite the \"Level 1\" name"),
                   (REGION_L2, "Level naming", "Holds the continent despite the \"Level 2\" name")]
    if exact_debt(df, w, cfg):
        shared.append((DEBT, "Likely derived", "Equals LTV × GAV exactly for every fund: verify at source"))
    for fund in funds:
        for dp, key, text in shared:
            # Only where the fund has a value; a missing value is already not usable.
            if states.get((fund, dp)) not in (NOT_REPORTED, NO_VALUE):
                add(fund, dp, CARE, key, text)

    tier_of = dict(zip(funds_df["fund"], funds_df["tier"], strict=True))
    analysable = dict(zip(funds_df["fund"], funds_df["analysable"], strict=True))
    gp_of = dict(zip(funds_df["fund"], funds_df["gp"], strict=True))
    rows = []
    for fund in funds:
        assessed = bool(analysable[fund])
        for dp, category in dp_cat.items():
            found = reasons.get((fund, dp), [])
            rating = max((r for r, _, _ in found), key=_WORST.__getitem__, default=USABLE)
            rows.append({
                "gp": gp_of[fund], "fund": fund, "tier": int(tier_of[fund]), "category": category,
                "datapoint": dp, "rating": rating if assessed else NOT_ASSESSED,
                # Keep the reasons for unassessed funds too: they explain the gaps behind the tier.
                "reasons": "; ".join(dict.fromkeys(t for _, _, t in found)),
                "reason_keys": "|".join(dict.fromkeys(k for r, k, _ in found if r != USABLE)),
            })
    return pd.DataFrame(rows, columns=USABILITY_COLUMNS)


def usability_by_datapoint(u: pd.DataFrame) -> pd.DataFrame:
    """Per datapoint (catalogue order), assessed funds by rating and the most common reasons."""
    a = u[u["rating"] != NOT_ASSESSED]
    order = list(dict.fromkeys(u["datapoint"]))
    counts = a.groupby(["datapoint", "rating"]).size().unstack(fill_value=0).reindex(
        index=order, columns=USABILITY, fill_value=0)
    flagged = a[a["rating"] != USABLE]

    def top_reasons(g: pd.DataFrame) -> str:
        # Count funds per cause (a fund with five outlier rows counts once).
        keys = g.assign(key=g["reason_keys"].str.split("|")).explode("key")
        top = keys[keys["key"] != ""].groupby("key")["fund"].nunique().sort_values(
            ascending=False, kind="stable").head(3)
        return "; ".join(f"{k} ({v})" for k, v in top.items())

    reasons = pd.Series({dp: top_reasons(g) for dp, g in flagged.groupby("datapoint")}, dtype=object)
    reasons = reasons.reindex(order).fillna("")
    cats = u.drop_duplicates("datapoint").set_index("datapoint")["category"].reindex(order)
    out = counts.assign(category=cats, reasons=reasons).reset_index()
    out.columns.name = None
    return out[["category", "datapoint", *USABILITY, "reasons"]]


def usability_by_fund(u: pd.DataFrame) -> pd.DataFrame:
    """Per assessed fund: datapoints by rating, ordered best first (most usable, fewest not usable)."""
    a = u[u["rating"] != NOT_ASSESSED]
    if a.empty:
        return pd.DataFrame(columns=["gp", "fund", "tier", *USABILITY])
    counts = (a.groupby(["gp", "fund", "tier", "rating"]).size().unstack(fill_value=0)
              .reindex(columns=USABILITY, fill_value=0))
    counts.columns.name = None
    out = counts.reset_index()
    return out.sort_values([NOT_USABLE, USABLE, "fund"], ascending=[True, False, True],
                           kind="stable").reset_index(drop=True)
