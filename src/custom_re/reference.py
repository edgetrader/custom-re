"""Read the GP reference (which GP manages each expected fund) and attach GP names to fund-level tables."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REFERENCE_SHEET = "Fund Name"
# Source column -> internal name.
REFERENCE_COLUMNS = {"GP Name": "gp", "Fund Name": "fund"}
# GP label for an extract fund the reference does not list.
UNMAPPED = "(not in reference)"


def read_reference(path: str | Path) -> pd.DataFrame:
    """Read the reference sheet as columns gp, fund and excel_row, with surrounding blanks stripped.

    Rows blank in both columns are dropped (trailing table padding); a row with only one blank is kept so
    `check_reference` can report it. Raises ValueError if the sheet or its columns are missing.
    """
    raw = pd.read_excel(path, sheet_name=REFERENCE_SHEET, dtype="string")
    missing = [c for c in REFERENCE_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"{path}: sheet {REFERENCE_SHEET!r} is missing columns {missing}")
    ref = raw[list(REFERENCE_COLUMNS)].rename(columns=REFERENCE_COLUMNS)
    for col in ref.columns:
        s = ref[col].str.strip()
        ref[col] = s.mask(s == "")
    # Excel row for traceability: +1 for the header, +1 because Excel is 1-based.
    ref.insert(0, "excel_row", raw.index + 2)
    return ref[ref[["gp", "fund"]].notna().any(axis=1)].reset_index(drop=True)


def gp_map(reference: pd.DataFrame) -> dict[str, str]:
    """Fund -> GP for complete reference rows; the first listing wins if a fund is listed twice."""
    ok = reference.dropna(subset=["gp", "fund"]).drop_duplicates("fund")
    return dict(zip(ok["fund"], ok["gp"], strict=True))


def add_gp(frame: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Copy of `frame` with a gp column inserted before its fund column.

    Funds missing from the reference get UNMAPPED; rows without a fund (extract-wide issues) stay blank.
    """
    gps = frame["fund"].map(gp_map(reference))
    gps = gps.mask(frame["fund"].notna() & gps.isna(), UNMAPPED)
    out = frame.copy()
    out.insert(list(out.columns).index("fund"), "gp", gps)
    return out
