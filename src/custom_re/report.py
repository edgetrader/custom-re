"""Write the data quality results to an Excel workbook."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

MAX_COL_WIDTH = 80


def write_report(
    path: Path,
    summary: pd.DataFrame,
    issues: pd.DataFrame,
    funds: pd.DataFrame,
    completeness: dict[str, pd.DataFrame] | None = None,
) -> None:
    """Write Summary, Issues, Fund Metrics and any completeness sheets, with filters and sized columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sheets = {"Summary": summary, "Issues": issues, "Fund Metrics": funds.reset_index()}
    sheets.update(completeness or {})
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, frame in sheets.items():
            frame.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col_cells in ws.columns:
                for cell in col_cells:
                    # Text from the extract starting with "=" must stay text, never a formula.
                    if cell.data_type == "f":
                        cell.data_type = "s"
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells)
                letter = col_cells[0].column_letter
                ws.column_dimensions[letter].width = min(max(10, width + 2), MAX_COL_WIDTH)
