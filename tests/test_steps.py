"""Tests for the three steps: reference check, completeness bands and datapoint usability."""

import pandas as pd
import pytest

from custom_re.checks import run_all
from custom_re.loader import tidy
from custom_re.steps import (
    CAUTION,
    GOOD,
    IN_REFERENCE,
    MISSING_FROM_EXTRACT,
    NOT_IN_REFERENCE,
    UNUSABLE,
    band_of,
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
    assert (usab["rating"] == GOOD).all() and len(usab) == 36
    # Caveats are notes in the detail, not ratings.
    notes = detail.set_index(["fund", "datapoint"])["notes"]
    assert "Only zero" in notes[("Fund A", "Carried interest incurred to-date")]
    assert "Anonymised" in notes[("Fund A", "Country")]


def test_error_makes_datapoint_unusable_and_names_fund(raw, rules):
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund LTV (current)"), "Value Num"] = 2.0
    _, usab, _ = _run(raw, rules)
    row = usab.set_index("datapoint").loc["Fund LTV (current)"]
    assert row["rating"] == UNUSABLE and row["unusable_funds"] == ["Fund B"]
    assert row["unusable"][0] == ("outside 0% to 100%", ["Fund B"])
    assert row["usable_funds"] == ["Fund A"] and "between 0% and 100%" in row["why_usable"]


def test_warning_makes_datapoint_caution(raw, rules):
    raw.loc[(raw["Company"] == "Fund A") & (raw["Datapoint.Name"] == "Bad debts"), "Value Num"] = 5e6
    _, usab, _ = _run(raw, rules)
    row = usab.set_index("datapoint").loc["Bad debts"]
    assert row["rating"] == CAUTION and row["caution_funds"] == ["Fund A"]


def test_missing_value_names_fund_without_changing_rating(raw, rules):
    _, usab, detail = _run(_drop(raw, "Fund B", ["Fund Target Net IRR"]), rules)
    row = usab.set_index("datapoint").loc["Fund Target Net IRR"]
    assert row["rating"] == GOOD and row["no_value_funds"] == ["Fund B"]
    assert (row["funds_with_value"], row["funds_assessed"], row["completeness"]) == (1, 2, 0.5)
    assert row["usable_funds"] == ["Fund A"]


def test_fund_wide_error_makes_every_datapoint_unusable_for_that_fund(raw, rules):
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund GAV"), "Currency"] = "EUR"
    _, usab, _ = _run(raw, rules)
    assert (usab["rating"] == UNUSABLE).all()
    assert usab.set_index("datapoint").loc["Fund NAV", "unusable_funds"] == ["Fund B"]


def test_only_top_band_funds_are_assessed(raw, rules):
    keep = ["Fund GAV", "Fund NAV", "Country", "Sector - level 1"]
    raw = raw[(raw["Company"] == "Fund A") | raw["Datapoint.Name"].isin(keep)].reset_index(drop=True)
    raw.loc[(raw["Company"] == "Fund B") & (raw["Datapoint.Name"] == "Fund GAV"), "Value Num"] = -1.0
    comp, usab, detail = _run(raw, rules)
    assert comp.set_index("fund").loc["Fund B", "band"] == "≤50%"
    assert set(detail["fund"]) == {"Fund A"}
    assert usab.set_index("datapoint").loc["Fund GAV", "rating"] == GOOD


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
