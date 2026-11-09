import pandas as pd
import pytest

from custom_re.loader import fund_info_wide, tidy


def test_tidy_renames_strips_and_types(raw):
    raw.loc[0, "Analysis1"] = " "
    df = tidy(raw)
    assert {"fund", "category", "datapoint", "value_num", "excel_row"} <= set(df.columns)
    assert df["category"].str.startswith("CustomRE").sum() == 0
    assert pd.isna(df.loc[0, "a1"])
    assert df.loc[0, "excel_row"] == 2  # header is Excel row 1


def test_tidy_rejects_missing_columns(raw):
    with pytest.raises(ValueError, match="Value Num"):
        tidy(raw.drop(columns=["Value Num"]))


def test_fund_info_wide_one_row_per_fund(raw):
    wide = fund_info_wide(tidy(raw))
    assert list(wide.index) == ["Fund A", "Fund B"]
    assert wide.loc["Fund A", "Fund GAV"] == 100_000_000
    assert wide.loc["Fund A", "End of fund term"] == pd.Timestamp("2028-03-01")
    assert wide.loc["Fund A", "currency"] == "GBP"


def test_read_raw_keeps_na_labels(raw, tmp_path):
    from custom_re.loader import read_raw

    raw.loc[raw["Datapoint.Name"] == "Country", "Value String"] = "NA"
    path = tmp_path / "x.xlsx"
    raw.to_excel(path, sheet_name="KPI", index=False)
    back = tidy(read_raw(path))
    assert (back.loc[back["datapoint"] == "Country", "value_str"] == "NA").all()


def test_tidy_coerces_bad_dates_to_nat_and_keeps_number_text(raw):
    raw["Value Num"] = raw["Value Num"].astype(object)
    raw["Date"] = raw["Date"].astype(object)
    raw.loc[0, "Date"] = "not a date"
    raw.loc[0, "Value Num"] = "tbc"
    df = tidy(raw)
    assert pd.isna(df.loc[0, "date"])
    assert df.loc[0, "value_num_text"] == "tbc"
    assert df["value_num_text"].notna().sum() == 1


@pytest.mark.parametrize("text", ["NA", "n/a", " null ", "Null"])
def test_null_text_in_value_num_is_no_value(raw, text):
    raw["Value Num"] = raw["Value Num"].astype(object)
    raw.loc[0, "Value Num"] = text
    df = tidy(raw)
    assert pd.isna(df.loc[0, "value_num"]) and pd.isna(df.loc[0, "value_num_text"])


def test_load_rules_reads_yaml():
    from custom_re.loader import load_rules
    from tests.conftest import ROOT

    rules = load_rules(ROOT / "config" / "dq_rules.yaml")
    assert "Fund Info" in rules["datapoints"]
    assert "reporting_date" not in rules  # derived from the period by the CLI
