"""Load the Custom RE KPI extract into a tidy long-format DataFrame."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

KPI_SHEET = "KPI"
CATEGORY_PREFIX = "CustomRE\\"

# Source column -> tidy column name. These are the required columns of the extract.
RENAME = {
    "Company": "fund",
    "Datapoint.Category.Category": "category",
    "Datapoint.Name": "datapoint",
    "Date": "date",
    "Reporting Date": "reporting_date",
    "Currency": "currency",
    "Analysis1": "a1",
    "Analysis2": "a2",
    "Analysis3": "a3",
    "Analysis4": "a4",
    "Value Num": "value_num",
    "Value Date": "value_date",
    "Value String": "value_str",
}

# Text in Value Num that means "no value" (compared case-insensitively), like a blank cell.
NULL_TEXT = {"na", "n/a", "null", "nan", "none"}

# Columns that identify one record.
KEY = ["fund", "datapoint", "date", "a1", "a2", "a3"]


def load_rules(path: str | Path) -> dict[str, Any]:
    """Read the YAML data quality rules."""
    with open(path, encoding="utf-8") as fh:
        rules: dict[str, Any] = yaml.safe_load(fh)
    return rules


def read_raw(path: str | Path) -> pd.DataFrame:
    """Read the KPI sheet exactly as exported, with source column names.

    Default NA strings are disabled so that real labels such as "NA" survive;
    only truly empty cells become missing.
    """
    return pd.read_excel(path, sheet_name=KPI_SHEET, keep_default_na=False, na_values=[""])


def tidy(raw: pd.DataFrame) -> pd.DataFrame:
    """Convert the raw KPI sheet to typed, tidy columns.

    Columns outside RENAME are dropped; `check_unused_columns` verifies they are empty, and that Analysis4
    (loaded as a4) is empty outside label rows.
    Null text (NULL_TEXT) in Value Num counts as no value. Other non-numeric text is kept in
    `value_num_text` so it can be reported.
    """
    missing = [c for c in RENAME if c not in raw.columns]
    if missing:
        raise ValueError(f"KPI sheet is missing required columns: {missing}")

    df = raw[list(RENAME)].rename(columns=RENAME).copy()

    # Export pads blanks with a single space; treat whitespace-only as missing.
    for col in ["fund", "category", "datapoint", "currency", "a1", "a2", "a3", "a4", "value_str"]:
        s = df[col].astype("string").str.strip()
        df[col] = s.mask(s == "")

    df["category"] = df["category"].str.removeprefix(CATEGORY_PREFIX)
    for col in ["date", "reporting_date", "value_date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")

    # Coerce numbers, but remember any text that failed so the DQ report can show it.
    original = raw["Value Num"].astype("string").str.strip()
    original = original.mask(original.str.casefold().isin(NULL_TEXT).fillna(False))
    df["value_num"] = pd.to_numeric(raw["Value Num"], errors="coerce")
    df["value_num_text"] = original.where(df["value_num"].isna() & original.notna() & (original != ""))

    # Excel row number for traceability: +1 for the header, +1 because Excel is 1-based.
    df.insert(0, "excel_row", raw.index + 2)
    return df


def load_kpi(path: str | Path) -> pd.DataFrame:
    """Read and tidy the KPI sheet in one step."""
    return tidy(read_raw(path))


def fund_info_wide(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot fund-level datapoints (Fund Info and GAV Composition) to one row per fund.

    Every fund in `df` gets a row, even if it has no fund-level values.
    `aggfunc="first"` hides duplicates by design; `check_fund_level_count` reports them.
    """
    funds = pd.Index(sorted(df["fund"].dropna().unique()), name="fund")
    sub = df[df["category"].isin(["Fund Info", "GAV Composition"])]
    num = sub.dropna(subset=["value_num"]).pivot_table(
        index="fund", columns="datapoint", values="value_num", aggfunc="first"
    )
    dates = sub.dropna(subset=["value_date"]).pivot_table(
        index="fund", columns="datapoint", values="value_date", aggfunc="first"
    )
    wide = num.join(dates, how="outer").reindex(funds)
    wide.insert(0, "currency", df.groupby("fund")["currency"].first().reindex(funds))
    wide.columns.name = None
    return wide
