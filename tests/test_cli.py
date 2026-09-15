"""Tests fuer die Kommandozeile und den Gesamtablauf."""

from __future__ import annotations

import json
from datetime import date

import pytest

from amplifier_characterization import cli
from amplifier_characterization.instrument import FPC1500, InstrumentError, SimulatedFPC1500
from amplifier_characterization.measurement import run_measurement

CONFIG = {
    "instrument": {"resource": "FAKE::INSTR", "settle_s": 0.0},
    "frequency": {"start_hz": 1e6, "stop_hz": 5e6, "points": 3},
    "amplitude": {"start_dbm": -20.0, "stop_dbm": -10.0, "points": 2},
    "output": {"directory": "results", "name": "amp"},
    # amplifier_gain_db ist Pflicht, sobald auto_level greift.
    "measurement": {"amplifier_gain_db": 0.0},
}


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.json"
    payload = dict(CONFIG)
    payload["output"] = {"directory": str(tmp_path / "results"), "name": "amp"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_parser_defaults():
    args = cli.build_parser().parse_args([])
    assert args.config == cli.DEFAULT_CONFIG
    assert not args.dry_run


def test_apply_overrides_changes_output_and_resource(tmp_path):
    from amplifier_characterization.config import Config

    config = Config.from_dict(CONFIG)
    args = cli.build_parser().parse_args(
        ["-o", str(tmp_path), "--resource", "TCPIP0::1.2.3.4::inst0::INSTR"]
    )
    updated = cli.apply_overrides(config, args)
    assert updated.output.directory == str(tmp_path)
    assert updated.instrument.resource == "TCPIP0::1.2.3.4::inst0::INSTR"


def test_apply_overrides_without_flags_keeps_config():
    from amplifier_characterization.config import Config

    config = Config.from_dict(CONFIG)
    assert cli.apply_overrides(config, cli.build_parser().parse_args([])) is config


def test_make_analyzer_selects_implementation():
    from amplifier_characterization.config import Config

    config = Config.from_dict(CONFIG)
    assert isinstance(cli.make_analyzer(config, True), SimulatedFPC1500)
    assert isinstance(cli.make_analyzer(config, False), FPC1500)


def test_save_run_writes_csv_config_and_plots(tmp_path, config):
    from tests.test_measurement import StubAnalyzer

    points = run_measurement(StubAnalyzer(), config)
    lines = []
    written = cli.save_run(config, points, tmp_path, True, lines.append)
    assert len(written) == 6
    assert (tmp_path / "s12_measurement.csv").exists()
    saved = json.loads((tmp_path / "config_used.json").read_text(encoding="utf-8"))
    assert saved["frequency"]["points"] == 3
    assert len(lines) == 6


def test_save_run_can_skip_plots(tmp_path, config):
    from tests.test_measurement import StubAnalyzer

    points = run_measurement(StubAnalyzer(), config)
    written = cli.save_run(config, points, tmp_path, False, lambda _line: None)
    assert len(written) == 2


def test_main_dry_run_creates_dated_output(config_file, tmp_path):
    lines = []
    code = cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lines.append,
        today=date(2026, 9, 14),
    )
    assert code == 0
    run_directory = tmp_path / "results" / "2026-09-14_amp"
    assert (run_directory / "s12_measurement.csv").exists()
    assert any("Fertig: 6 Messpunkte" in line for line in lines)


def test_main_shows_thru_prompt(config_file, monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "prompt_thru_calibration",
        lambda message, use_gui, title, printer: calls.append((message, use_gui)),
    )
    code = cli.main(
        ["-c", str(config_file), "--dry-run", "--no-gui", "--no-plot"],
        printer=lambda _line: None,
    )
    assert code == 0
    assert calls and calls[0][1] is False
    assert "Verstaerker" in calls[0][0]


def test_main_reports_config_error(tmp_path):
    lines = []
    code = cli.main(["-c", str(tmp_path / "fehlt.json")], printer=lines.append)
    assert code == 1
    assert any("Konfigurationsfehler" in line for line in lines)


def test_main_reports_instrument_error(config_file, monkeypatch):
    class BrokenAnalyzer:
        def open(self):
            raise InstrumentError("keine Verbindung")

        def close(self):
            self.closed = True

    monkeypatch.setattr(cli, "make_analyzer", lambda _config, _dry: BrokenAnalyzer())
    lines = []
    code = cli.main(
        ["-c", str(config_file), "--no-prompt"], printer=lines.append
    )
    assert code == 1
    assert any("Messfehler" in line for line in lines)


def test_main_with_plots(config_file, tmp_path):
    code = cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt"],
        printer=lambda _line: None,
        today=date(2026, 1, 1),
    )
    assert code == 0
    run_directory = tmp_path / "results" / "2026-01-01_amp"
    assert len(list(run_directory.glob("*.png"))) == 4


def test_entry_script_exposes_main():
    import run_s12

    assert run_s12.main is cli.main


def test_host_of_extracts_ip():
    assert cli.host_of("TCPIP0::192.168.1.187::inst0::INSTR") == "192.168.1.187"
    assert cli.host_of("FAKE") == "FAKE"


def test_make_analyzer_passes_simulation_parameters():
    from amplifier_characterization.config import Config

    config = Config.from_dict({**CONFIG, "simulation": {"gain_db": 7.5, "points": 33}})
    analyzer = cli.make_analyzer(config, True)
    assert analyzer.gain_db == 7.5
    assert analyzer.points == 33


def test_check_connection_succeeds_in_dry_run(config):
    lines = []
    assert cli.check_connection(config, True, lines.append) == 0
    assert any("Simulation" in line for line in lines)


def test_check_connection_reports_failure(config, monkeypatch):
    class Unreachable:
        def open(self):
            raise InstrumentError("Verbindung fehlgeschlagen")

        def close(self):
            pass

    monkeypatch.setattr(cli, "make_analyzer", lambda _config, _dry: Unreachable())
    monkeypatch.setattr(cli, "probe_tcp", lambda _host, _port: False)
    lines = []
    assert cli.check_connection(config, False, lines.append) == 1
    assert any("FEHLER" in line for line in lines)


def test_main_check_flag_short_circuits(config_file):
    lines = []
    assert cli.main(["-c", str(config_file), "--check", "--dry-run"], printer=lines.append) == 0
    assert not any("Fertig" in line for line in lines)


def test_diagnose_network_suggests_alternatives_when_ports_answer():
    lines = []
    cli.diagnose_network(
        "TCPIP0::192.168.1.187::inst0::INSTR",
        lines.append,
        probe=lambda _host, port: port in (5025, 4880),
    )
    text = "\n".join(lines)
    assert "erreichbar" in text
    assert "TCPIP0::192.168.1.187::5025::SOCKET" in text
    assert "hislip0" in text


def test_diagnose_network_points_at_dhcp_when_silent():
    lines = []
    cli.diagnose_network(
        "TCPIP0::192.168.1.187::inst0::INSTR",
        lines.append,
        probe=lambda _host, _port: False,
    )
    text = "\n".join(lines)
    assert "Keine Antwort" in text
    assert "DHCP" in text


def test_check_connection_failure_runs_network_diagnosis(config, monkeypatch):
    class Unreachable:
        def open(self):
            raise InstrumentError("VI_ERROR_RSRC_NFOUND")

        def close(self):
            pass

    monkeypatch.setattr(cli, "make_analyzer", lambda _config, _dry: Unreachable())
    monkeypatch.setattr(cli, "probe_tcp", lambda _host, _port: False)
    lines = []
    assert cli.check_connection(config, False, lines.append) == 1
    assert any("Netzwerktest" in line for line in lines)


def test_show_resources_lists_entries():
    lines = []
    assert cli.show_resources(lines.append, lister=lambda: ["A::INSTR"]) == 0
    assert any("A::INSTR" in line for line in lines)


def test_show_resources_reports_empty_backend():
    lines = []
    assert cli.show_resources(lines.append, lister=list) == 1
    assert any("Keine VISA-Ressourcen" in line for line in lines)


def test_show_resources_reports_backend_error():
    def broken():
        raise InstrumentError("kein Backend")

    lines = []
    assert cli.show_resources(lines.append, lister=broken) == 1
    assert any("VISA-Fehler" in line for line in lines)


def test_main_list_resources_flag(config_file, monkeypatch):
    monkeypatch.setattr(cli, "list_resources", lambda: ["A::INSTR"])
    lines = []
    assert cli.main(["-c", str(config_file), "--list-resources"], printer=lines.append) == 0
    assert any("A::INSTR" in line for line in lines)


def test_diagnose_network_without_known_protocol_port():
    lines = []
    cli.diagnose_network(
        "TCPIP0::192.168.1.187::inst0::INSTR",
        lines.append,
        probe=lambda _host, port: port == 111,
    )
    text = "\n".join(lines)
    assert "erreichbar" in text
    assert "SOCKET" not in text
    assert "hislip0" not in text


def test_probe_instrument_reports_sweep_and_trace(config_file):
    lines = []
    assert cli.main(["-c", str(config_file), "--probe", "--dry-run"], printer=lines.append) == 0
    text = "\n".join(lines)
    assert "Sweep-Punkte" in text
    assert "Trace" in text
    assert "akzeptiert" in text


def test_probe_instrument_lists_rejected_commands(config, monkeypatch):
    from amplifier_characterization.instrument import SimulatedFPC1500

    class Picky(SimulatedFPC1500):
        def check_errors(self, limit=20):
            return ['-113,"Undefined header;SOUR:POW"']

    monkeypatch.setattr(cli, "make_analyzer", lambda _config, _dry: Picky())
    lines = []
    assert cli.probe_instrument(config, True, lines.append) == 1
    assert any("Undefined header" in line for line in lines)


def test_probe_instrument_reports_connection_error(config, monkeypatch):
    class Unreachable:
        def open(self):
            raise InstrumentError("nicht erreichbar")

        def close(self):
            pass

    monkeypatch.setattr(cli, "make_analyzer", lambda _config, _dry: Unreachable())
    lines = []
    assert cli.probe_instrument(config, False, lines.append) == 1
    assert any("FEHLER" in line for line in lines)


def test_probe_instrument_lists_rejected_configuration(config, monkeypatch):
    from amplifier_characterization.instrument import SimulatedFPC1500

    class Noisy(SimulatedFPC1500):
        def configure(self, instrument_config, start_hz, stop_hz, progress=None):
            super().configure(instrument_config, start_hz, stop_hz, progress)
            self.rejected = ['DET RMS -> -113,"Undefined header"']

    monkeypatch.setattr(cli, "make_analyzer", lambda _config, _dry: Noisy())
    lines = []
    assert cli.probe_instrument(config, True, lines.append) == 0
    assert any("Abgelehnt" in line for line in lines)


class FakeDevice:
    """Geraet fuer die Befehlslisten-Abfrage."""

    instances = []

    def __init__(self, answer=None, fail=False):
        self.answer = answer
        self.fail = fail
        self.closed = False

    def open(self):
        if self.fail:
            raise InstrumentError("nicht erreichbar")
        return self

    def identify(self):
        return "Rohde&Schwarz,FPC1500,1,1.70"

    def query(self, command):
        assert command == "SYST:HELP:HEAD?"
        return self.answer

    def close(self):
        self.closed = True


def test_dump_commands_writes_file_and_highlights_source(config, tmp_path, monkeypatch):
    device = FakeDevice(answer="#3030SOUR:GEN:POW,FREQ:STAR,OUTP:STAT")
    monkeypatch.setattr(cli, "FPC1500", lambda *_a, **_k: device)
    target = tmp_path / "commands.txt"
    lines = []
    assert cli.dump_commands(config, str(target), lines.append) == 0
    written = target.read_text(encoding="utf-8").splitlines()
    assert "FREQ:STAR" in written
    assert any("SOUR:GEN:POW" in line for line in lines)
    assert device.closed


def test_dump_commands_without_matching_entries(config, tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "FPC1500", lambda *_a, **_k: FakeDevice(answer="FREQ:STAR\nBAND")
    )
    lines = []
    assert cli.dump_commands(config, str(tmp_path / "c.txt"), lines.append) == 0
    assert any("(keine)" in line for line in lines)


def test_dump_commands_reports_unsupported_device(config, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FPC1500", lambda *_a, **_k: FakeDevice(fail=True))
    lines = []
    assert cli.dump_commands(config, str(tmp_path / "c.txt"), lines.append) == 1
    assert any("unterstuetzt" in line for line in lines)


def test_main_dump_commands_flag(config_file, tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "FPC1500", lambda *_a, **_k: FakeDevice(answer="SOUR:GEN:POW")
    )
    lines = []
    code = cli.main(
        ["-c", str(config_file), "--dump-commands", str(tmp_path / "c.txt")],
        printer=lines.append,
    )
    assert code == 0


def test_probe_reports_generator_limits_and_clamps(config, monkeypatch):
    from amplifier_characterization.instrument import SimulatedFPC1500

    class Limited(SimulatedFPC1500):
        def generator_limits(self):
            return (-15.0, 0.0)

    monkeypatch.setattr(cli, "make_analyzer", lambda _config, _dry: Limited())
    lines = []
    assert cli.probe_instrument(config, True, lines.append) == 0
    text = "\n".join(lines)
    assert "Pegelbereich: -15.00 ... +0.00 dBm" in text
    assert "WARNUNG" in text


def test_probe_without_generator_limits(config, monkeypatch):
    from amplifier_characterization.instrument import SimulatedFPC1500

    class Wide(SimulatedFPC1500):
        def generator_limits(self):
            return (-60.0, 10.0)

    monkeypatch.setattr(cli, "make_analyzer", lambda _config, _dry: Wide())
    lines = []
    assert cli.probe_instrument(config, True, lines.append) == 0
    assert "WARNUNG" not in "\n".join(lines)


def _write_reference(path, points):
    from amplifier_characterization.measurement import write_csv

    return write_csv(points, path)


def test_main_applies_reference(config_file, tmp_path):
    from datetime import date as _date

    reference_dir = tmp_path / "thru"
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "-o", str(reference_dir)],
        printer=lambda _l: None,
        today=_date(2026, 1, 1),
    ) == 0
    reference_csv = reference_dir / "2026-01-01_amp" / "s12_measurement.csv"

    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--reference", str(reference_csv)],
        printer=lines.append,
        today=_date(2026, 1, 2),
    ) == 0
    assert any("Kabeldaempfung" in line for line in lines)


def test_main_reports_unreadable_reference(config_file, tmp_path):
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--reference", str(tmp_path / "fehlt.csv")],
        printer=lines.append,
    ) == 1
    assert any("nicht lesbar" in line for line in lines)


def test_main_stops_when_no_level_matches_the_reference(config_file, tmp_path):
    """Passt das Amplitudenraster gar nicht, wird vor der Messung abgebrochen."""
    from amplifier_characterization.measurement import Point

    reference = tmp_path / "thru.csv"
    _write_reference(
        reference,
        [Point(timestamp="t", frequency_hz=1.0, p_in_dbm=-99.0,
               p_out_dbm=-99.6, gain_db=-0.6)],
    )
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--reference", str(reference)],
        printer=lines.append,
    ) == 1
    assert any("kommt in der Referenz" in line for line in lines)


def test_main_reports_mismatched_frequency_grid(config_file, tmp_path):
    """Stimmen die Pegel, fehlt aber eine Frequenz, schlaegt die Korrektur zu."""
    from datetime import date as _date

    from amplifier_characterization.measurement import read_csv, write_csv

    assert cli.main_thru(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lambda _l: None,
        today=_date(2026, 9, 15),
    ) == 0
    original = tmp_path / "results" / "2026-09-15_amp_thru" / "s12_measurement.csv"
    points = read_csv(original)
    frequencies = sorted({p.frequency_hz for p in points})
    trimmed = tmp_path / "trimmed.csv"
    write_csv([p for p in points if p.frequency_hz != frequencies[-1]], trimmed)
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--reference", str(trimmed)],
        printer=lines.append,
    ) == 1
    assert any("Referenzfehler" in line for line in lines)


def test_apply_overrides_changes_bandwidths():
    from amplifier_characterization.config import Config

    config = Config.from_dict(CONFIG)
    args = cli.build_parser().parse_args(["--rbw", "1000000", "--vbw", "300000"])
    updated = cli.apply_overrides(config, args)
    assert updated.instrument.rbw_hz == 1e6
    assert updated.instrument.vbw_hz == 3e5
    assert updated.instrument.resource == config.instrument.resource


# -- THRU-Lauf und automatische Referenz --------------------------------
def test_main_thru_writes_suffixed_directory(config_file, tmp_path):
    from datetime import date as _date

    lines = []
    assert cli.main_thru(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lines.append,
        today=_date(2026, 9, 15),
    ) == 0
    assert (tmp_path / "results" / "2026-09-15_amp_thru" / "s12_measurement.csv").exists()
    text = "\n".join(lines)
    assert "THRU-Referenzmessung" in text
    assert "run_s12.py" in text


def test_main_finds_thru_reference_automatically(config_file, tmp_path):
    from datetime import date as _date

    assert cli.main_thru(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lambda _l: None,
        today=_date(2026, 9, 15),
    ) == 0
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lines.append,
        today=_date(2026, 9, 16),
    ) == 0
    text = "\n".join(lines)
    assert "Referenz gefunden" in text
    assert "Kabeldaempfung im Mittel" in text


def test_main_warns_when_no_reference_exists(config_file):
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lines.append,
    ) == 0
    assert any("Keine THRU-Referenz gefunden" in line for line in lines)


def test_no_reference_flag_skips_the_search(config_file, tmp_path):
    from datetime import date as _date

    assert cli.main_thru(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lambda _l: None,
        today=_date(2026, 9, 15),
    ) == 0
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--no-reference"],
        printer=lines.append,
    ) == 0
    assert not any("Referenz gefunden" in line for line in lines)


def test_thru_prompt_and_amp_prompt_differ(config_file, monkeypatch):
    captured = []
    monkeypatch.setattr(
        cli,
        "prompt_thru_calibration",
        lambda message, use_gui, title, printer: captured.append((title, message)),
    )
    cli.main_thru(
        ["-c", str(config_file), "--dry-run", "--no-plot"], printer=lambda _l: None
    )
    cli.main(
        ["-c", str(config_file), "--dry-run", "--no-plot"], printer=lambda _l: None
    )
    assert captured[0][0] == "THRU-Referenz"
    assert "OHNE Verstaerker" in captured[0][1]
    assert captured[1][0] == "Verstaerkermessung"
    assert "einschleifen" in captured[1][1]
    assert "Kabelreferenz:" in captured[1][1]


def test_amp_prompt_names_the_reference(config_file, monkeypatch):
    from datetime import date as _date

    cli.main_thru(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lambda _l: None,
        today=_date(2026, 9, 15),
    )
    captured = []
    monkeypatch.setattr(
        cli,
        "prompt_thru_calibration",
        lambda message, use_gui, title, printer: captured.append(message),
    )
    cli.main(
        ["-c", str(config_file), "--dry-run", "--no-plot"], printer=lambda _l: None
    )
    assert "Kabelreferenz:" in captured[0]


def test_entry_script_for_thru_measurement():
    import run_thru

    assert run_thru.main_thru is cli.main_thru


def test_reference_settings_mismatch_is_reported(config_file, tmp_path, monkeypatch):
    from datetime import date as _date

    assert cli.main_thru(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lambda _l: None,
        today=_date(2026, 9, 15),
    ) == 0
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--rbw", "1000000"],
        printer=lines.append,
        today=_date(2026, 9, 16),
    ) == 0
    text = "\n".join(lines)
    assert "andere" in text and "Aufloesebandbreite" in text


def test_reference_settings_match_is_silent(config_file):
    from datetime import date as _date

    cli.main_thru(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lambda _l: None,
        today=_date(2026, 9, 15),
    )
    lines = []
    cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lines.append,
        today=_date(2026, 9, 16),
    )
    assert not any("ACHTUNG" in line for line in lines)


def test_reference_settings_without_saved_config(config, tmp_path):
    lonely = tmp_path / "s12_measurement.csv"
    lonely.write_text("x", encoding="utf-8")
    assert cli.compare_reference_settings(config, lonely, lambda _l: None)


def test_reference_settings_with_broken_saved_config(config, tmp_path):
    (tmp_path / "config_used.json").write_text("{kaputt", encoding="utf-8")
    assert cli.compare_reference_settings(
        config, tmp_path / "s12_measurement.csv", lambda _l: None
    )


def test_reference_settings_detects_frequency_grid_change(config, tmp_path):
    import dataclasses

    from amplifier_characterization.config import FrequencyConfig

    (tmp_path / "config_used.json").write_text(
        json.dumps(
            {
                "instrument": {
                    key: getattr(config.instrument, key)
                    for key, _label in cli.COMPARED_SETTINGS
                },
                "frequency": vars(config.frequency),
            }
        ),
        encoding="utf-8",
    )
    changed = dataclasses.replace(
        config, frequency=FrequencyConfig(start_hz=1e6, stop_hz=9e6, points=3)
    )
    lines = []
    assert not cli.compare_reference_settings(
        changed, tmp_path / "s12_measurement.csv", lines.append
    )
    assert any("Frequenzraster" in line for line in lines)


def test_explicit_reference_is_also_checked(config_file, tmp_path, monkeypatch):
    from datetime import date as _date

    cli.main_thru(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot"],
        printer=lambda _l: None,
        today=_date(2026, 9, 15),
    )
    reference = tmp_path / "results" / "2026-09-15_amp_thru" / "s12_measurement.csv"
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--reference", str(reference), "--vbw", "999000"],
        printer=lines.append,
        today=_date(2026, 9, 16),
    ) == 0
    assert any("Videobandbreite" in line for line in lines)


def test_pad_note_mentions_attenuator_in_both_prompts(config):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    padded = dataclasses.replace(
        config, measurement=MeasurementConfig(external_pad_db=10.0)
    )
    assert "Referenzmessung" in cli.pad_note(padded, True)
    assert "10 dB" in cli.pad_note(padded, True)
    assert "zerstoert" in cli.pad_note(padded, False)
    assert cli.pad_note(config, True) == ""


def test_main_saves_partial_data_on_overload(config_file, tmp_path, monkeypatch):
    from amplifier_characterization.measurement import Overload, Point

    kept = [Point(timestamp="t", frequency_hz=1e6, p_in_dbm=-30.0,
                  p_out_dbm=20.0, gain_db=50.0)]
    monkeypatch.setattr(
        cli,
        "run_measurement",
        lambda *_a, **_k: (_ for _ in ()).throw(Overload("ABBRUCH bei ...", kept)),
    )
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--no-reference"],
        printer=lines.append,
    ) == 1
    text = "\n".join(lines)
    assert "ABBRUCH" in text
    assert "trotzdem gespeichert" in text
    assert "Abgebrochen nach 1 Messpunkten" in text


def test_main_overload_without_any_point(config_file, monkeypatch):
    from amplifier_characterization.measurement import Overload

    monkeypatch.setattr(
        cli,
        "run_measurement",
        lambda *_a, **_k: (_ for _ in ()).throw(Overload("ABBRUCH", [])),
    )
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--no-reference"],
        printer=lines.append,
    ) == 1
    assert any("Keine Messpunkte" in line for line in lines)


def test_run_query_prints_answers(config, monkeypatch):
    class Answering(FakeDevice):
        def query(self, command):
            return {"A?": "1", "B?": "2"}[command]

    device = Answering()
    monkeypatch.setattr(cli, "FPC1500", lambda *_a, **_k: device)
    lines = []
    assert cli.run_query(config, " A? ; B? ; ", lines.append) == 0
    text = "\n".join(lines)
    assert "A?  ->  1" in text
    assert "B?  ->  2" in text
    assert device.closed


def test_run_query_reports_failure(config, monkeypatch):
    monkeypatch.setattr(cli, "FPC1500", lambda *_a, **_k: FakeDevice(fail=True))
    lines = []
    assert cli.run_query(config, "A?", lines.append) == 1
    assert any("FEHLER" in line for line in lines)


def test_main_query_flag(config_file, monkeypatch):
    class Answering(FakeDevice):
        def query(self, command):
            return "+30.0"

    monkeypatch.setattr(cli, "FPC1500", lambda *_a, **_k: Answering())
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--query", "DISP:TRAC:Y:RLEV? MAX"],
        printer=lines.append,
    ) == 0
    assert any("+30.0" in line for line in lines)


def test_run_query_sets_commands_without_question_mark(config, monkeypatch):
    class Recorder(FakeDevice):
        def __init__(self):
            super().__init__()
            self.written = []

        def write(self, command):
            self.written.append(command)

        def query(self, command):
            return "20"

    device = Recorder()
    monkeypatch.setattr(cli, "FPC1500", lambda *_a, **_k: device)
    lines = []
    assert cli.run_query(
        config, "INP:ATT 40 dB; DISP:TRAC:Y:RLEV? MAX", lines.append
    ) == 0
    assert device.written == ["INP:ATT 40 dB"]
    text = "\n".join(lines)
    assert "(gesetzt)" in text
    assert "->  20" in text


def test_apply_overrides_changes_level_settings():
    from amplifier_characterization.config import Config

    config = Config.from_dict(CONFIG)
    args = cli.build_parser().parse_args(
        ["--attenuation", "40", "--ref-level", "30"]
    )
    updated = cli.apply_overrides(config, args)
    assert updated.instrument.attenuation_db == 40.0
    assert updated.instrument.ref_level_dbm == 30.0


def test_apply_overrides_replaces_the_frequency_grid():
    from amplifier_characterization.config import Config

    config = Config.from_dict(CONFIG)
    args = cli.build_parser().parse_args(["--freq", "60e6", "140e6", "401"])
    updated = cli.apply_overrides(config, args)
    assert updated.frequency.start_hz == 6e7
    assert updated.frequency.points == 401


def test_apply_overrides_replaces_the_level_grid():
    from amplifier_characterization.config import Config

    config = Config.from_dict(CONFIG)
    args = cli.build_parser().parse_args(["--levels", "-10", "-10", "1"])
    updated = cli.apply_overrides(config, args)
    assert updated.amplitude.points == 1
    assert updated.amplitude_grid == pytest.approx([-10.0])


def test_apply_overrides_validates_the_result():
    from amplifier_characterization.config import Config, ConfigError

    config = Config.from_dict(CONFIG)
    args = cli.build_parser().parse_args(["--levels", "0", "-30", "31"])
    with pytest.raises(ConfigError):
        cli.apply_overrides(config, args)


def test_main_reports_the_automatic_levels(config_file, monkeypatch):
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--no-reference"],
        printer=lines.append,
    ) == 0
    assert any("Pegel automatisch" in line for line in lines)


def test_explicit_level_flags_disable_the_automatic(config_file):
    lines = []
    assert cli.main(
        ["-c", str(config_file), "--dry-run", "--no-prompt", "--no-plot",
         "--no-reference", "--ref-level", "0", "--attenuation", "10"],
        printer=lines.append,
    ) == 0
    assert not any("Pegel automatisch" in line for line in lines)


def test_impossible_level_combination_is_a_config_error(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "frequency": {"start_hz": 1e6, "stop_hz": 5e6, "points": 3},
                "amplitude": {"start_dbm": -10.0, "stop_dbm": 0.0, "points": 2},
                "measurement": {"amplifier_gain_db": 60.0},
            }
        ),
        encoding="utf-8",
    )
    lines = []
    assert cli.main(["-c", str(path), "--dry-run", "--no-prompt"], printer=lines.append) == 1
    assert any("Konfigurationsfehler" in line for line in lines)
