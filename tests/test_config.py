"""Tests fuer Laden, Validieren und Ableiten der Konfiguration."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from amplifier_characterization import config as cfg

VALID = {
    "frequency": {"start_hz": 1e6, "stop_hz": 5e6, "points": 3},
    "amplitude": {"start_dbm": -20, "stop_dbm": -10, "points": 2},
}


def test_build_rejects_non_mapping():
    with pytest.raises(cfg.ConfigError, match="JSON-Objekt"):
        cfg._build(cfg.OutputConfig, ["nope"], "output")


def test_build_rejects_unknown_keys():
    with pytest.raises(cfg.ConfigError, match="Unbekannte Schluessel"):
        cfg._build(cfg.OutputConfig, {"directory": "x", "farbe": "rot"}, "output")


def test_build_reports_missing_required_field():
    with pytest.raises(cfg.ConfigError, match="unvollstaendig"):
        cfg._build(cfg.FrequencyConfig, {"start_hz": 1.0}, "frequency")


def test_linspace_single_point_uses_start():
    assert cfg._linspace(3.0, 9.0, 1, "test") == pytest.approx([3.0])


def test_linspace_regular_grid():
    assert cfg._linspace(0.0, 10.0, 3, "test") == pytest.approx([0.0, 5.0, 10.0])


def test_linspace_rejects_zero_points():
    with pytest.raises(cfg.ConfigError, match="mindestens 1"):
        cfg._linspace(0.0, 1.0, 0, "test")


def test_linspace_rejects_reversed_range():
    with pytest.raises(cfg.ConfigError, match="liegt unter"):
        cfg._linspace(5.0, 1.0, 3, "test")


def test_instrument_validate_accepts_defaults():
    cfg.InstrumentConfig().validate()


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"resource": ""}, "resource"),
        ({"timeout_ms": 0}, "timeout_ms"),
        ({"settle_s": -1.0}, "settle_s"),
        ({"sweep_points": 1}, "sweep_points"),
    ],
)
def test_instrument_validate_rejects_bad_values(kwargs, message):
    with pytest.raises(cfg.ConfigError, match=message):
        cfg.InstrumentConfig(**kwargs).validate()


def test_frequency_grid_requires_positive_start():
    with pytest.raises(cfg.ConfigError, match="start_hz"):
        cfg.FrequencyConfig(start_hz=0.0, stop_hz=1e6, points=3).grid()


def test_amplitude_grid():
    grid = cfg.AmplitudeConfig(start_dbm=-10, stop_dbm=0, points=3).grid()
    assert grid == pytest.approx([-10.0, -5.0, 0.0])


def test_run_directory_contains_date():
    output = cfg.OutputConfig(directory="results", name="amp1")
    assert output.run_directory(date(2026, 9, 14)) == Path("results/2026-09-14_amp1")


def test_run_directory_defaults_to_today():
    stamp = date.today().strftime("%Y-%m-%d")
    assert stamp in str(cfg.OutputConfig().run_directory())


def test_from_dict_builds_full_config():
    config = cfg.Config.from_dict(VALID)
    assert config.instrument.resource == cfg.DEFAULT_RESOURCE
    assert len(config.frequency_grid) == 3
    assert len(config.amplitude_grid) == 2
    assert isinstance(config.frequency_grid, np.ndarray)


def test_from_dict_rejects_non_mapping():
    with pytest.raises(cfg.ConfigError, match="JSON-Objekt"):
        cfg.Config.from_dict([1, 2, 3])


def test_from_dict_rejects_unknown_section():
    with pytest.raises(cfg.ConfigError, match="Unbekannte Abschnitte"):
        cfg.Config.from_dict({**VALID, "sonstiges": {}})


def test_from_dict_requires_frequency_and_amplitude():
    with pytest.raises(cfg.ConfigError, match="'amplitude' fehlt"):
        cfg.Config.from_dict({"frequency": VALID["frequency"]})


def test_load_config_roundtrip(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(VALID), encoding="utf-8")
    assert cfg.load_config(path).frequency.points == 3


def test_load_config_missing_file(tmp_path):
    with pytest.raises(cfg.ConfigError, match="nicht lesbar"):
        cfg.load_config(tmp_path / "fehlt.json")


def test_load_config_invalid_json(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(cfg.ConfigError, match="Ungueltiges JSON"):
        cfg.load_config(path)


def test_make_output_directory_creates_dated_folder(tmp_path):
    config = cfg.Config.from_dict(
        {**VALID, "output": {"directory": str(tmp_path), "name": "amp"}}
    )
    directory = cfg.make_output_directory(config, date(2026, 1, 2))
    assert directory.is_dir()
    assert directory.name == "2026-01-02_amp"


def test_describe_lists_ranges():
    lines = cfg.describe(cfg.Config.from_dict(VALID))
    assert any("Frequenzbereich" in line for line in lines)
    assert lines[-1].endswith("6")


def test_simulation_defaults_model_a_thru_connection():
    simulation = cfg.SimulationConfig()
    simulation.validate()
    assert simulation.gain_db < 0
    assert simulation.rolloff_db_per_ghz == 0.0


def test_simulation_rejects_too_few_points():
    with pytest.raises(cfg.ConfigError, match="simulation.points"):
        cfg.SimulationConfig(points=1).validate()


def test_from_dict_reads_simulation_section():
    config = cfg.Config.from_dict({**VALID, "simulation": {"gain_db": 12.0}})
    assert config.simulation.gain_db == 12.0


def test_generator_command_templates_need_placeholders():
    with pytest.raises(cfg.ConfigError, match="{state}"):
        cfg.InstrumentConfig(generator_state_command="SOUR:GEN:STAT ON").validate()
    with pytest.raises(cfg.ConfigError, match="{level}"):
        cfg.InstrumentConfig(generator_level_command="SOUR:GEN:POW 0").validate()


def test_generator_command_templates_accept_placeholders():
    cfg.InstrumentConfig(
        generator_state_command="SOUR:GEN:STAT {state}",
        generator_level_command="SOUR:GEN:POW {level:.2f} dBm",
    ).validate()
