"""Tests fuer das Auswertungsskript inklusive Dateiauswahl."""

from __future__ import annotations

import json

import numpy as np
import pytest

from amplifier_characterization import analysis_cli as acli
from amplifier_characterization.measurement import write_csv
from tests.test_analysis import make_amplifier


@pytest.fixture
def measurement(tmp_path):
    folder = tmp_path / "results" / "2026-09-15_amp1"
    folder.mkdir(parents=True)
    points = make_amplifier(np.arange(-30.0, 0.1, 1.0), [80e6, 100e6, 120e6])
    return write_csv(points, folder / "s12_measurement.csv")


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "frequency": {"start_hz": 8e7, "stop_hz": 1.2e8, "points": 3},
                "amplitude": {"start_dbm": -30.0, "stop_dbm": 0.0, "points": 31},
                "output": {"directory": str(tmp_path / "results"), "name": "amp1"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_parser_defaults():
    args = acli.build_parser().parse_args([])
    assert args.csv is None
    assert args.compression_db == 1.0


def test_start_directory_uses_config(config_file, measurement, tmp_path):
    assert acli.start_directory(str(config_file)) == str(tmp_path / "results")


def test_start_directory_falls_back_without_config(tmp_path):
    assert acli.start_directory(str(tmp_path / "fehlt.json")) == "."


def test_start_directory_falls_back_without_result_folder(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "frequency": {"start_hz": 1e6, "stop_hz": 2e6, "points": 2},
                "amplitude": {"start_dbm": -1.0, "stop_dbm": 0.0, "points": 2},
                "output": {"directory": str(tmp_path / "gibtsnicht")},
            }
        ),
        encoding="utf-8",
    )
    assert acli.start_directory(str(path)) == "."


def test_main_with_explicit_path_writes_all_outputs(measurement):
    lines = []
    assert acli.main([str(measurement)], printer=lines.append) == 0
    folder = measurement.parent
    for name in (
        "analysis_summary.md",
        "analysis_per_frequency.csv",
        "analysis_transfer.png",
        "analysis_compression.png",
        "analysis_frequency.png",
        "analysis_gain_map.png",
        "analysis_power_map.png",
    ):
        assert (folder / name).is_file()
    text = "\n".join(lines)
    assert "Kleinsignalverstaerkung" in text
    assert "Kompression" in text


def test_main_uses_the_file_chooser(measurement, config_file):
    asked = []

    def chooser(initial_dir, title):
        asked.append((initial_dir, title))
        return str(measurement)

    lines = []
    assert acli.main(
        ["-c", str(config_file), "--no-plot"], printer=lines.append, chooser=chooser
    ) == 0
    assert asked and asked[0][0].endswith("results")


def test_main_reports_cancelled_selection(config_file):
    lines = []
    assert acli.main(
        ["-c", str(config_file)], printer=lines.append, chooser=lambda _d, _t: ""
    ) == 1
    assert any("abgebrochen" in line for line in lines)


def test_main_reports_missing_file(tmp_path):
    lines = []
    assert acli.main([str(tmp_path / "fehlt.csv")], printer=lines.append) == 1
    assert any("nicht gefunden" in line for line in lines)


def test_main_reports_unreadable_file(tmp_path):
    path = tmp_path / "kaputt.csv"
    path.write_text("nicht,wirklich\ncsv,daten\n", encoding="utf-8")
    lines = []
    assert acli.main([str(path)], printer=lines.append) == 1
    assert any("nicht lesbar" in line for line in lines)


def test_main_reports_analysis_error(measurement):
    lines = []
    assert acli.main(
        [str(measurement), "--linear-range", "0", "-30"], printer=lines.append
    ) == 1
    assert any("nicht moeglich" in line for line in lines)


def test_main_warns_without_cable_correction(tmp_path):
    points = make_amplifier(np.arange(-30.0, 0.1, 2.0), [80e6], corrected=False)
    path = write_csv(points, tmp_path / "roh.csv")
    lines = []
    assert acli.main([str(path), "--no-plot"], printer=lines.append) == 0
    assert any("keine THRU-Referenz" in line for line in lines)


def test_main_honours_output_directory(measurement, tmp_path):
    target = tmp_path / "auswertung"
    assert acli.main(
        [str(measurement), "--no-plot", "-o", str(target)], printer=lambda _l: None
    ) == 0
    assert (target / "analysis_summary.md").is_file()


def test_entry_script_exposes_main():
    import analyze

    assert analyze.main is acli.main
