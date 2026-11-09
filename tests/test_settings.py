import pandas as pd
import pytest

from custom_re.settings import Period, load_settings, parse_period


@pytest.mark.parametrize("text", ["Q2 2026", "q2 2026", "2026Q2", "2026 Q2", "Q2-2026", " 2026-q2 "])
def test_parse_period_accepts_common_forms(text):
    assert parse_period(text) == Period(2026, 2)


@pytest.mark.parametrize("text", ["Q5 2026", "Q0 2026", "2026", "June 2026", "Q2 26", "2026Q2extra", ""])
def test_parse_period_rejects_other_text(text):
    with pytest.raises(ValueError, match="Unrecognised period"):
        parse_period(text)


@pytest.mark.parametrize(
    ("q", "end"), [(1, "2026-03-31"), (2, "2026-06-30"), (3, "2026-09-30"), (4, "2026-12-31")]
)
def test_period_names_and_quarter_end(q, end):
    p = Period(2026, q)
    assert p.code == f"2026Q{q}"
    assert p.label == f"Q{q} 2026"
    assert p.end == pd.Timestamp(end)


def test_load_settings_resolves_relative_and_absolute_paths(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(f"data_dir: data\noutput_dir: {tmp_path / 'abs_out'}\n"
                   "rules_file: config/r.yaml\ninput_pattern: 'X_{period}.xlsx'\n"
                   f"reference_file: {tmp_path / 'ref' / 'GP.xlsx'}\n")
    s = load_settings(cfg, root=tmp_path / "proj")
    p = Period(2026, 2)
    assert s.input_path(p) == tmp_path / "proj" / "data" / "X_2026Q2.xlsx"
    assert s.period_output_dir(p) == tmp_path / "abs_out" / "2026Q2"
    assert s.rules_file == tmp_path / "proj" / "config" / "r.yaml"
    assert s.reference_file == tmp_path / "ref" / "GP.xlsx"


@pytest.mark.parametrize(("body", "msg"), [
    ("data_dir: d\n", "missing settings"),
    ("data_dir: d\noutput_dir: o\nrules_file: r\nreference_file: g\n"
     "input_pattern: fixed.xlsx\n", "must contain"),
    ("data_dir: d\noutput_dir: o\nrules_file: r\nreference_file: g\n"
     "input_pattern: '{year}_{period}.xlsx'\n", "only use"),
    ("- a list\n", "must be a mapping"),
    ("data_dir: [unclosed\n", "not valid YAML"),
])
def test_load_settings_rejects_incomplete_config(tmp_path, body, msg):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(body)
    with pytest.raises(ValueError, match=msg):
        load_settings(cfg, root=tmp_path)


def test_project_settings_file_finds_q2_extract():
    from tests.conftest import ROOT

    s = load_settings(ROOT / "config" / "settings.yaml", ROOT)
    assert s.input_path(Period(2026, 2)).name == "Custom_RE_Extract_2026Q2.xlsx"
