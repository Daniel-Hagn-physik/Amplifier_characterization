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
    "measurement": {},
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
    assert len(written) == 5
    assert (tmp_path / "s12_measurement.csv").exists()
    saved = json.loads((tmp_path / "config_used.json").read_text(encoding="utf-8"))
    assert saved["frequency"]["points"] == 3
    assert len(lines) == 5


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
        lambda message, use_gui, printer: calls.append((message, use_gui)),
    )
    code = cli.main(
        ["-c", str(config_file), "--dry-run", "--no-gui", "--no-plot"],
        printer=lambda _line: None,
    )
    assert code == 0
    assert calls and calls[0][1] is False
    assert "THRU" in calls[0][0]


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
    assert len(list(run_directory.glob("*.png"))) == 3


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
