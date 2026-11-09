"""Reporting periods and the configurable file locations that depend on them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

# Accepts "Q2 2026", "2026Q2", "2026 Q2", "Q2-2026", "2026-q2" and similar.
_QUARTER_FIRST = re.compile(r"^Q([1-4])[\s_-]*(\d{4})$", re.IGNORECASE)
_YEAR_FIRST = re.compile(r"^(\d{4})[\s_-]*Q([1-4])$", re.IGNORECASE)


@dataclass(frozen=True)
class Period:
    """A calendar quarter, e.g. Q2 2026."""

    year: int
    quarter: int

    @property
    def code(self) -> str:
        """Compact form used in file and folder names, e.g. 2026Q2."""
        return f"{self.year}Q{self.quarter}"

    @property
    def label(self) -> str:
        """Display form, e.g. Q2 2026."""
        return f"Q{self.quarter} {self.year}"

    @property
    def end(self) -> pd.Timestamp:
        """Last day of the quarter: the expected reporting date of its extract."""
        return pd.Period(year=self.year, quarter=self.quarter, freq="Q").end_time.normalize()


def parse_period(text: str) -> Period:
    """Parse a quarter such as "Q2 2026" or "2026Q2"; raise ValueError otherwise."""
    s = text.strip()
    if m := _QUARTER_FIRST.match(s):
        return Period(year=int(m.group(2)), quarter=int(m.group(1)))
    if m := _YEAR_FIRST.match(s):
        return Period(year=int(m.group(1)), quarter=int(m.group(2)))
    raise ValueError(f"Unrecognised period {text!r}; expected a quarter such as 'Q2 2026' or '2026Q2'")


@dataclass(frozen=True)
class Settings:
    """Resolved folder locations and the extract naming pattern."""

    data_dir: Path
    output_dir: Path
    rules_file: Path
    input_pattern: str

    def input_path(self, period: Period) -> Path:
        """Extract file expected for `period`."""
        return self.data_dir / self.input_pattern.format(period=period.code)

    def period_output_dir(self, period: Period) -> Path:
        """Folder holding every output for `period`, e.g. output/2026Q2."""
        return self.output_dir / period.code


def load_settings(path: str | Path, root: str | Path) -> Settings:
    """Read the settings YAML, resolving relative paths against `root` (the project root)."""
    with open(path, encoding="utf-8") as fh:
        try:
            raw = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping of setting names to values")

    required = ["data_dir", "output_dir", "rules_file", "input_pattern"]
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise ValueError(f"{path} is missing settings: {missing}")
    if "{period}" not in raw["input_pattern"]:
        raise ValueError("input_pattern must contain {period}")
    # Fail now, not mid-run, if the pattern has other placeholders or stray braces.
    try:
        raw["input_pattern"].format(period="2000Q1")
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"input_pattern may only use the {{period}} placeholder: {exc!r}") from exc

    base = Path(root)

    def resolve(p: str) -> Path:
        # Path("/abs") stays absolute when joined, so this handles both cases.
        return base / Path(p).expanduser()

    return Settings(
        data_dir=resolve(raw["data_dir"]),
        output_dir=resolve(raw["output_dir"]),
        rules_file=resolve(raw["rules_file"]),
        input_pattern=raw["input_pattern"],
    )
