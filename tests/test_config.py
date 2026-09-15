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
    # Pflichtangabe: ohne Verstaerkung laesst Config.validate() nicht durch.
    "measurement": {"amplifier_gain_db": 0.0},
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


def test_make_output_directory_does_not_overwrite_previous_run(tmp_path):
    config = cfg.Config.from_dict(
        {**VALID, "output": {"directory": str(tmp_path), "name": "amp"}}
    )
    first = cfg.make_output_directory(config, date(2026, 1, 2))
    (first / cfg.RESULT_FILE).write_text("daten", encoding="utf-8")
    second = cfg.make_output_directory(config, date(2026, 1, 2))
    assert second.name == "2026-01-02_amp_2"
    (second / cfg.RESULT_FILE).write_text("daten", encoding="utf-8")
    assert cfg.make_output_directory(config, date(2026, 1, 2)).name == "2026-01-02_amp_3"
    assert (first / cfg.RESULT_FILE).read_text(encoding="utf-8") == "daten"


def test_run_directory_appends_thru_suffix():
    output = cfg.OutputConfig(directory="results", name="amp1")
    assert output.run_directory(date(2026, 9, 15), cfg.THRU_SUFFIX).name == (
        "2026-09-15_amp1_thru"
    )


def test_find_latest_reference_picks_newest_thru_run(tmp_path):
    import os
    import time

    config = cfg.Config.from_dict(
        {**VALID, "output": {"directory": str(tmp_path), "name": "amp1"}}
    )
    for index, name in enumerate(("2026-09-01_amp1_thru", "2026-09-15_amp1_thru")):
        folder = tmp_path / name
        folder.mkdir()
        target = folder / cfg.RESULT_FILE
        target.write_text("x", encoding="utf-8")
        os.utime(target, (time.time() + index, time.time() + index))
    (tmp_path / "2026-09-10_amp1").mkdir()
    (tmp_path / "2026-09-10_amp1" / cfg.RESULT_FILE).write_text("x", encoding="utf-8")
    assert cfg.find_latest_reference(config).parent.name == "2026-09-15_amp1_thru"


def test_find_latest_reference_without_results(tmp_path):
    config = cfg.Config.from_dict(
        {**VALID, "output": {"directory": str(tmp_path), "name": "amp1"}}
    )
    assert cfg.find_latest_reference(config) is None
    (tmp_path / "2026-09-15_amp1_thru").mkdir()
    assert cfg.find_latest_reference(config) is None


def test_find_latest_reference_without_directory(tmp_path):
    config = cfg.Config.from_dict(
        {**VALID, "output": {"directory": str(tmp_path / "gibtsnicht"), "name": "a"}}
    )
    assert cfg.find_latest_reference(config) is None


def test_external_pad_must_not_be_negative():
    with pytest.raises(cfg.ConfigError, match="external_pad_db"):
        cfg.Config.from_dict({**VALID, "measurement": {**VALID["measurement"], "external_pad_db": -5.0}})


def test_noise_margin_must_not_be_negative():
    with pytest.raises(cfg.ConfigError, match="noise_margin_db"):
        cfg.Config.from_dict({**VALID, "measurement": {**VALID["measurement"], "noise_margin_db": -1.0}})


def test_measurement_defaults():
    config = cfg.Config.from_dict(
        {**VALID, "measurement": {**VALID["measurement"], "external_pad_db": 10.0, "amplifier_gain_db": 40.0}}
    )
    assert config.measurement.external_pad_db == 10.0
    assert config.measurement.amplifier_gain_db == 40.0
    assert config.measurement.max_input_dbm == 30.0
    assert config.measurement.noise_margin_db == 15.0
    assert config.measurement.auto_limit_levels


def test_repeated_thru_runs_keep_the_thru_suffix_last(tmp_path):
    config = cfg.Config.from_dict(
        {**VALID, "output": {"directory": str(tmp_path), "name": "amp1"}}
    )
    first = cfg.make_output_directory(config, date(2026, 9, 15), cfg.THRU_SUFFIX)
    assert first.name == "2026-09-15_amp1_thru"
    (first / cfg.RESULT_FILE).write_text("x", encoding="utf-8")
    second = cfg.make_output_directory(config, date(2026, 9, 15), cfg.THRU_SUFFIX)
    assert second.name == "2026-09-15_amp1_2_thru"
    (second / cfg.RESULT_FILE).write_text("x", encoding="utf-8")
    third = cfg.make_output_directory(config, date(2026, 9, 15), cfg.THRU_SUFFIX)
    assert third.name == "2026-09-15_amp1_3_thru"
    # und alle drei werden von der Referenzsuche gefunden
    assert cfg.find_latest_reference(config) is not None


def test_repeated_thru_runs_are_found_as_reference(tmp_path):
    config = cfg.Config.from_dict(
        {**VALID, "output": {"directory": str(tmp_path), "name": "amp1"}}
    )
    for _ in range(2):
        folder = cfg.make_output_directory(config, date(2026, 9, 15), cfg.THRU_SUFFIX)
        (folder / cfg.RESULT_FILE).write_text("x", encoding="utf-8")
    assert cfg.find_latest_reference(config).parent.name == "2026-09-15_amp1_2_thru"


def test_gain_probe_settings_are_validated():
    with pytest.raises(cfg.ConfigError, match="gain_probe_step_db"):
        cfg.Config.from_dict({**VALID, "measurement": {**VALID["measurement"], "gain_probe_step_db": 0.0}})
    with pytest.raises(cfg.ConfigError, match="gain_probe_points"):
        cfg.Config.from_dict({**VALID, "measurement": {**VALID["measurement"], "gain_probe_points": 0}})
    config = cfg.Config.from_dict({**VALID, "measurement": {**VALID["measurement"], }})
    assert config.measurement.gain_probe
    assert config.measurement.max_input_dbm == 30.0


def test_comment_keys_are_ignored():
    """Schluessel mit fuehrendem _ machen die Config selbsterklaerend."""
    config = cfg.Config.from_dict(
        {
            "_hinweis": "erklaerender Text",
            "frequency": {"_was": "Frequenzbereich", **VALID["frequency"]},
            "amplitude": VALID["amplitude"],
            "measurement": {
                "_was": "Ablauf",
                "noise_margin_db": 12.0,
                **VALID["measurement"],
            },
        }
    )
    assert config.measurement.noise_margin_db == 12.0
    assert config.frequency.points == 3


def test_unknown_keys_without_underscore_are_still_rejected():
    with pytest.raises(cfg.ConfigError, match="Unbekannte Schluessel"):
        cfg.Config.from_dict(
            {**VALID, "measurement": {**VALID["measurement"], "rauschabstand": 12.0}}
        )


def test_shipped_default_config_is_valid():
    """Die mitgelieferte Config muss sich laden lassen."""
    config = cfg.load_config(Path(__file__).resolve().parents[1] / "config" / "default.json")
    assert config.frequency.points > 1
    assert config.instrument.ref_level_dbm <= config.instrument.attenuation_db - 10


# -- Automatische Pegeleinstellung --------------------------------------
def _measurement(**kwargs):
    """measurement-Abschnitt mit der Pflichtangabe als Grundlage."""
    return {"measurement": {**VALID["measurement"], **kwargs}}


def test_auto_level_computes_from_gain_and_pad():
    config = cfg.Config.from_dict(
        {**VALID, **_measurement(amplifier_gain_db=36.0, external_pad_db=20.0)}
    )
    updated, lines = cfg.apply_auto_levels(config)
    # -10 dBm Stoppegel + 36 dB - 20 dB = +6 dBm erwartet -> naechste 5er-Stufe
    assert updated.instrument.ref_level_dbm == 10.0
    assert updated.instrument.attenuation_db == 20.0
    assert any("Verstaerkung" in line for line in lines)


def test_missing_gain_is_rejected():
    """Ohne Verstaerkung laesst sich keine Geraeteeinstellung waehlen, die fuer
    THRU-Referenz und Messung gleichermassen gilt - also gar nicht erst laufen
    lassen."""
    config = cfg.Config.from_dict(
        {
            "frequency": VALID["frequency"],
            "amplitude": VALID["amplitude"],
            "measurement": {"external_pad_db": 10.0},
        }
    )
    with pytest.raises(cfg.ConfigError, match="amplifier_gain_db"):
        cfg.apply_auto_levels(config)


def test_missing_gain_is_allowed_without_auto_level():
    """Wer Referenzpegel und Eichleitung selbst setzt, braucht die Angabe nicht."""
    config = cfg.Config.from_dict(
        {
            "frequency": VALID["frequency"],
            "amplitude": VALID["amplitude"],
            "instrument": {"ref_level_dbm": 0.0, "attenuation_db": 10.0},
            "measurement": {"auto_level": False},
        }
    )
    assert config.measurement.amplifier_gain_db is None
    updated, lines = cfg.apply_auto_levels(config)
    assert updated is config
    assert lines == []


def test_auto_level_can_be_switched_off():
    config = cfg.Config.from_dict(
        {
            **VALID,
            "instrument": {"ref_level_dbm": 0.0, "attenuation_db": 10.0},
            **_measurement(auto_level=False, amplifier_gain_db=36.0),
        }
    )
    updated, lines = cfg.apply_auto_levels(config)
    assert updated.instrument.ref_level_dbm == 0.0
    assert lines == []


def test_auto_level_rejects_a_level_the_device_cannot_measure():
    """Statt stillschweigend zu begrenzen: sagen, dass der Pegel zu hoch ist."""
    config = cfg.Config.from_dict(
        {**VALID, **_measurement(amplifier_gain_db=60.0, external_pad_db=0.0)}
    )
    with pytest.raises(cfg.ConfigError, match="kaputt"):
        cfg.apply_auto_levels(config)


def test_auto_level_does_not_fall_below_the_lowest_reference_level():
    config = cfg.Config.from_dict(
        {
            "frequency": VALID["frequency"],
            "amplitude": {"start_dbm": -30.0, "stop_dbm": -30.0, "points": 1},
            **_measurement(amplifier_gain_db=0.0, external_pad_db=40.0),
        }
    )
    updated, _lines = cfg.apply_auto_levels(config)
    assert updated.instrument.ref_level_dbm == -30.0
