"""Tests for the three steps: reference check, completeness bands and datapoint usability."""

import pandas as pd
import pytest

from custom_re.checks import run_all
from custom_re.loader import tidy
from custom_re.steps import (
    CAVEATS,
    FULLY,
    IN_REFERENCE,
    MISSING_FROM_EXTRACT,
    NOT_IN_REFERENCE,
    PARTIAL_USE,
    UNUSABLE,
    band_of,
    classify,
    completeness,
    reference_check,
    usability,
)
from tests.conftest import ref_for


def _ref(rows):
    return pd.DataFrame({"excel_row": range(2, len(rows) + 2), "gp": [g for g, _ in rows],
                         "fund": [f for _, f in rows]})


def _drop(raw, fund, dps):
    return raw[~((raw["Company"] == fund) & raw["Datapoint.Name"].isin(dps))].reset_index(drop=True)


def _run(raw, rules, ref=None):
    df = tidy(raw)
    ref = ref_for(raw) if ref is None else ref
    issues = run_all(raw, df, rules, ref)
    comp = completeness(df, rules, ref)
    return comp, *usability(df, rules, issues, comp)


# ---------------------------------------------------------------- step 1


def test_reference_check_lists_both_directions(raw):
    ref = reference_check(tidy(raw), _ref([("GP One", "Fund A"), ("GP Two", "Fund C")]))
    got = {(s, f) for s, f in zip(ref["status"], ref["fund"], strict=True)}
    assert got == {(IN_REFERENCE, "Fund A"), (NOT_IN_REFERENCE, "Fund B"), (MISSING_FROM_EXTRACT, "Fund C")}
    assert list(ref["status"])[:2] == [NOT_IN_REFERENCE, MISSING_FROM_EXTRACT]  # problems first


# ---------------------------------------------------------------- step 2


@pytest.mark.parametrize(("share", "band"), [(1.0, ">90%"), (0.91, ">90%"), (0.9, ">75%"), (0.76, ">75%"),
                                             (0.75, ">50%"), (0.5, "≤50%"), (0.0, "≤50%")])
def test_band_edges(rules, share, band):
    assert band_of(share, rules["completeness_bands"]) == band


def test_completeness_counts_zero_and_none_as_values(raw, rules):
    comp, _, _ = _run(_drop(raw, "Fund B", ["Bad debts"]), rules)
    assert (comp["with_values"] == 36).all() and (comp["band"] == ">90%").all()


def test_blank_schedule_counts_as_a_value_but_no_rows_is_a_gap(raw, rules):
    raw["Value Num"] = raw["Value Num"].astype(object)
    dist = (raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Distribution amount")
    raw.loc[dist, "Value Num"] = None
    raw = _drop(raw, "Fund A", ["Capital call amount"])
    comp, usab, _ = _run(raw, rules)
    a = comp.set_index("fund").loc["Fund A"]
    assert a["with_values"] == a["total"] - 1
    assert a["missing_datapoints"] == "Capital call amount"
    dist = usab.set_index("datapoint").loc["Distribution amount"]
    assert dist["funds_with_value"] == 2 and not dist["no_value_funds"]


def test_completeness_lists_missing_datapoints_and_bands(raw, rules):
    raw["Value Num"] = raw["Value Num"].astype(object)
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund NAV"), "Value Num"] = "NA"
    dropped = ["Fund Target Net IRR", "Fund LTV Cap", "Others", "Country"]
    comp, _, _ = _run(_drop(raw, "Fund B", dropped), rules)
    b = comp.set_index("fund").loc["Fund B"]
    assert b["with_values"] == 31 and b["band"] == ">75%"
    assert b["missing_datapoints"] == "Fund LTV Cap, Fund NAV, Fund Target Net IRR, Others, Country"
    assert list(comp["fund"]) == ["Fund A", "Fund B"]  # best band first
    assert comp.set_index("fund").loc["Fund B", "gp"] == "GP 2"


# ---------------------------------------------------------------- step 3


def test_clean_fixture_all_good(raw, rules):
    _, usab, detail = _run(raw, rules)
    assert (usab["rating"] == FULLY).all() and len(usab) == 36 and (usab["valid_share"] == 1).all()
    # Caveats are notes in the detail, not ratings.
    notes = detail.set_index(["fund", "datapoint"])["notes"]
    assert "Only zero" in notes[("Fund A", "Carried interest incurred to-date")]
    assert "Anonymised" in notes[("Fund A", "Country")]


def test_error_on_one_fund_makes_datapoint_partially_usable_and_names_fund(raw, rules):
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund LTV (current)"), "Value Num"] = 2.0
    _, usab, _ = _run(raw, rules)
    row = usab.set_index("datapoint").loc["Fund LTV (current)"]
    # 1 of 2 funds valid (50%): partially usable, not unusable.
    assert row["rating"] == PARTIAL_USE and row["valid_share"] == 0.5 and row["unusable_funds"] == ["Fund B"]
    assert row["unusable"][0] == ("outside 0% to 100%", ["Fund B"])
    assert row["usable_funds"] == ["Fund A"] and "between 0% and 100%" in row["why_usable"]


def test_warning_makes_datapoint_usable_with_caveats(raw, rules):
    raw.loc[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Bad debts"), "Value Num"] = 5e6
    _, usab, _ = _run(raw, rules)
    row = usab.set_index("datapoint").loc["Bad debts"]
    assert row["rating"] == CAVEATS and row["caution_funds"] == ["Fund A"] and row["valid_share"] == 1


def test_missing_value_names_fund_without_changing_rating(raw, rules):
    _, usab, detail = _run(_drop(raw, "Fund B", ["Fund Target Net IRR"]), rules)
    row = usab.set_index("datapoint").loc["Fund Target Net IRR"]
    assert row["rating"] == FULLY and row["no_value_funds"] == ["Fund B"]
    assert (row["funds_with_value"], row["funds_assessed"], row["completeness"]) == (1, 2, 0.5)
    assert row["usable_funds"] == ["Fund A"]


def test_two_currencies_make_only_that_funds_amounts_unusable(raw, rules):
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund GAV"), "Currency"] = "EUR"
    _, usab, detail = _run(raw, rules)
    u = usab.set_index("datapoint")
    for dp in rules["amount_datapoints"]:
        assert u.loc[dp, "rating"] == PARTIAL_USE and u.loc[dp, "unusable_funds"] == ["Fund B"]
    # Ratios and dates do not depend on the currency.
    assert u.loc["Fund LTV (current)", "rating"] == FULLY and u.loc["Fund inception date", "rating"] == FULLY
    b = detail[detail["fund"] == "Fund B"].set_index("datapoint")["rating"]
    assert (b[rules["amount_datapoints"]] == UNUSABLE).all() and b["Country Percentage"] != UNUSABLE


def test_only_top_band_funds_are_assessed(raw, rules):
    keep = ["Fund GAV", "Fund NAV", "Country", "Sector - level 1"]
    raw = raw[(raw["Company"] == "Fund A") | raw["Datapoint.Name"].isin(keep)].reset_index(drop=True)
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund GAV"), "Value Num"] = -1.0
    comp, usab, detail = _run(raw, rules)
    assert comp.set_index("fund").loc["Fund B", "band"] == "≤50%"
    assert set(detail["fund"]) == {"Fund A"}
    assert usab.set_index("datapoint").loc["Fund GAV", "rating"] == FULLY


def test_no_fund_in_top_band(raw, rules):
    raw = raw[raw["Datapoint.Name"].isin(["Fund GAV"])].reset_index(drop=True)
    _, usab, detail = _run(raw, rules)
    assert usab.empty and detail.empty


def test_fund_listed_with_blank_gp_is_in_reference(raw):
    ref = reference_check(tidy(raw), _ref([("GP One", "Fund A"), (None, "Fund B")]))
    b = ref.set_index("fund").loc["Fund B"]
    assert b["status"] == IN_REFERENCE and b["gp"] == "(GP blank in reference)"


def test_fund_names_with_commas_are_counted_once(raw, rules):
    # Real fund names can contain commas (e.g. "Fund II, L.P."); counts must not split them.
    raw["Company"] = raw["Company"].map({"Fund A": "Fund A, L.P.", "Fund B": "Fund B, SCSp"})
    raw.loc[(raw["Company"] == "Fund B, SCSp") & (raw["Datapoint.Name"] == "Bad debts"), "Value Num"] = 5e6
    _, usab, _ = _run(raw, rules)
    for r in usab.to_dict("records"):
        assert r["n_usable"] == len(r["usable_funds"]) <= r["funds_with_value"] <= r["funds_assessed"] == 2
    bad = usab.set_index("datapoint").loc["Bad debts"]
    assert bad["usable_funds"] == ["Fund A, L.P."] and bad["caution"] == [
        ("bad debts unusually high against GAV", ["Fund B, SCSp"])]


def test_every_assessed_fund_is_counted_once_per_datapoint(raw, rules):
    raw.loc[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Fund LTV (current)"), "Value Num"] = 2.0
    _, usab, _ = _run(_drop(raw, "Fund B", ["Fund Target Net IRR"]), rules)
    for r in usab.to_dict("records"):
        parts = r["usable_funds"] + r["caution_funds"] + r["unusable_funds"] + r["no_value_funds"]
        assert sorted(parts) == ["Fund A", "Fund B"], r["datapoint"]


T = {"usable_min_valid": 0.95, "partial_min_valid": 0.50, "fully_usable_max_caution": 0.05}


@pytest.mark.parametrize(
    ("n_value", "n_unusable", "n_caution", "across_error", "across_warning", "expected"),
    [
        (60, 0, 0, False, False, FULLY),
        (60, 0, 3, False, False, FULLY),  # warnings on 5%: still fully usable
        (60, 0, 4, False, False, CAVEATS),  # warnings on 6.7%
        (60, 1, 0, False, False, CAVEATS),  # one invalid fund of 60: 98% valid
        (60, 3, 0, False, False, CAVEATS),  # exactly 95% valid
        (60, 4, 0, False, False, PARTIAL_USE),  # 93% valid
        (60, 30, 0, False, False, PARTIAL_USE),  # exactly 50% valid
        (60, 31, 0, False, False, UNUSABLE),  # 48% valid
        (60, 0, 0, True, False, UNUSABLE),  # a problem across every fund
        (60, 0, 0, False, True, CAVEATS),
        (0, 0, 0, False, False, UNUSABLE),  # no fund has a value
    ],
)
def test_classify_thresholds(n_value, n_unusable, n_caution, across_error, across_warning, expected):
    assert classify(n_value, n_unusable, n_caution, across_error, across_warning, T) == expected


def test_thresholds_come_from_the_rules(raw, rules):
    rules["usability"] = {"usable_min_valid": 0.4, "partial_min_valid": 0.2, "fully_usable_max_caution": 0.0}
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund LTV (current)"), "Value Num"] = 2.0
    _, usab, _ = _run(raw, rules)
    assert usab.set_index("datapoint").loc["Fund LTV (current)", "rating"] == CAVEATS  # 50% >= 40%


def test_fund_without_a_value_is_not_counted_even_with_an_error(raw, rules):
    """Two currencies make Fund B's amounts unusable, but a fund with no value is not rated or counted."""
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund GAV"), "Currency"] = "EUR"
    raw = _drop(raw, "Fund B", ["Fund NAV"])
    _, usab, detail = _run(raw, rules)
    nav = usab.set_index("datapoint").loc["Fund NAV"]
    assert nav["funds_with_value"] == 1 and nav["no_value_funds"] == ["Fund B"]
    assert nav["unusable_funds"] == [] and nav["valid_share"] == 1 and nav["rating"] == FULLY
    b = detail.set_index(["fund", "datapoint"]).loc[("Fund B", "Fund NAV")]
    assert b["rating"] == "No value" and b["reasons"] == "more than one currency in the fund"
