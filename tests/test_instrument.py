"""Tests fuer den SCPI-Wrapper und das simulierte Geraet."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from amplifier_characterization.config import InstrumentConfig
from amplifier_characterization.instrument import (
    FPC1500,
    InstrumentError,
    SimulatedFPC1500,
    open_resource_manager,
)
from tests.conftest import FakeResourceManager, FakeSession, default_answers


def make_device(**kwargs):
    session = FakeSession(answers=default_answers(**kwargs))
    manager = FakeResourceManager(session)
    device = FPC1500("FAKE::INSTR", resource_manager=manager, sleep=lambda _s: None)
    return device, session


# -- ResourceManager ---------------------------------------------------
def test_open_resource_manager_uses_pyvisa(monkeypatch):
    created = {}

    class FakeRM:
        def __init__(self, library):
            created["library"] = library

    module = types.ModuleType("pyvisa")
    module.ResourceManager = FakeRM
    monkeypatch.setitem(sys.modules, "pyvisa", module)
    assert isinstance(open_resource_manager("@py"), FakeRM)
    assert created["library"] == "@py"


def test_open_resource_manager_wraps_backend_error(monkeypatch):
    module = types.ModuleType("pyvisa")

    def boom(_library):
        raise OSError("keine VISA-Bibliothek")

    module.ResourceManager = boom
    monkeypatch.setitem(sys.modules, "pyvisa", module)
    with pytest.raises(InstrumentError, match="VISA-Backend"):
        open_resource_manager()


# -- Verbindung ---------------------------------------------------------
def test_open_sets_timeout_and_is_idempotent():
    device, session = make_device()
    assert device.open() is device
    device.open()
    assert session.timeout == device.timeout_ms


def test_open_creates_resource_manager_when_missing(monkeypatch):
    manager = FakeResourceManager()
    monkeypatch.setattr(
        "amplifier_characterization.instrument.open_resource_manager",
        lambda: manager,
    )
    device = FPC1500("FAKE::INSTR")
    device.open()
    assert manager.opened == ["FAKE::INSTR"]


def test_open_reports_connection_failure():
    device = FPC1500("FAKE::INSTR", resource_manager=FakeResourceManager(fail=True))
    with pytest.raises(InstrumentError, match="Verbindung"):
        device.open()


def test_close_without_open_is_harmless():
    device, _ = make_device()
    device.close()


def test_context_manager_closes_session():
    device, session = make_device()
    with device as opened:
        assert opened is device
    assert session.closed


def test_session_requires_open_connection():
    device, _ = make_device()
    with pytest.raises(InstrumentError, match="Keine offene"):
        _ = device.session


# -- SCPI ---------------------------------------------------------------
def test_write_and_query():
    device, session = make_device()
    device.open()
    device.write("FOO")
    assert session.writes == ["FOO"]
    assert device.identify().startswith("Rohde")
    device.wait()
    assert "*OPC?" in session.queries


def test_write_error_is_wrapped():
    session = FakeSession(fail_write=True)
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    with pytest.raises(InstrumentError, match="Schreiben fehlgeschlagen"):
        device.write("FOO")


def test_query_error_is_wrapped():
    session = FakeSession(fail_query=True)
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    with pytest.raises(InstrumentError, match="Abfrage fehlgeschlagen"):
        device.query("FOO?")


# -- Konfiguration ------------------------------------------------------
def test_configure_with_explicit_filters_and_attenuation():
    device, session = make_device(points=11, trace=[-10.0] * 11)
    device.open()
    device.configure(
        InstrumentConfig(rbw_hz=1e5, vbw_hz=3e5, attenuation_db=20.0, settle_s=0.0),
        1e6,
        2e6,
    )
    joined = " ".join(session.writes)
    assert "BAND 100000.000 Hz" in joined
    assert "BAND:VID 300000.000 Hz" in joined
    assert "INP:ATT 20 dB" in joined
    axis = device.frequency_axis()
    assert axis.size == 11
    assert axis[0] == pytest.approx(1e6)
    assert axis[-1] == pytest.approx(2e6)


def test_configure_with_auto_settings():
    device, session = make_device(points=7, trace=[-1.0] * 7)
    device.open()
    device.configure(InstrumentConfig(settle_s=0.0), 1e6, 5e6)
    joined = " ".join(session.writes)
    assert "BAND:AUTO ON" in joined
    assert "BAND:VID:AUTO ON" in joined
    assert "INP:ATT:AUTO ON" in joined


def test_configure_rejects_implausible_sweep_points():
    device, _ = make_device(trace=[-10.0])
    device.open()
    with pytest.raises(InstrumentError, match="Sweep-Punkte"):
        device.configure(InstrumentConfig(settle_s=0.0), 1e6, 5e6)


def test_configure_takes_sweep_points_from_config():
    session = FakeSession(answers=default_answers())
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.configure(InstrumentConfig(sweep_points=601), 1e6, 5e6)
    assert device.frequency_axis().size == 601
    assert "INIT:IMM" not in session.writes  # kein Testsweep noetig


def test_frequency_axis_requires_configure():
    device, _ = make_device()
    device.open()
    with pytest.raises(InstrumentError, match="configure"):
        device.frequency_axis()


# -- Generator und Messung ----------------------------------------------
def test_generator_and_level_commands():
    from amplifier_characterization.instrument import (
        GENERATOR_LEVEL_CANDIDATES,
        GENERATOR_STATE_CANDIDATES,
    )

    device, session = make_device()
    device.open()
    device.generator(True)
    device.generator(False)
    device.set_generator_level(-12.5)
    assert session.writes == [
        GENERATOR_STATE_CANDIDATES[0].format(state="ON"),
        GENERATOR_STATE_CANDIDATES[0].format(state="OFF"),
        GENERATOR_LEVEL_CANDIDATES[0].format(level=-12.5),
    ]


def test_sweep_waits_for_settling():
    slept = []
    session = FakeSession(answers=default_answers())
    device = FPC1500("X", resource_manager=FakeResourceManager(session), sleep=slept.append)
    device.open()
    device.sweep(0.5)
    device.sweep(0.0)
    assert slept == [0.5]
    assert session.writes == ["INIT:IMM", "INIT:IMM"]


def test_read_trace_parses_values_and_skips_blanks():
    device, _ = make_device(points=3, trace=[-10.0, -11.5, -12.0])
    device.open()
    assert device.read_trace() == pytest.approx([-10.0, -11.5, -12.0])


def test_read_trace_ignores_trailing_separator():
    session = FakeSession(answers={"TRAC:DATA? TRACE1": "-1.0,-2.0, ,"})
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    assert device.read_trace() == pytest.approx([-1.0, -2.0])


def test_read_trace_rejects_garbage():
    session = FakeSession(answers={"TRAC:DATA? TRACE1": "-1.0,abc"})
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    with pytest.raises(InstrumentError, match="Unlesbarer"):
        device.read_trace()


def test_read_trace_rejects_empty_answer():
    session = FakeSession(answers={"TRAC:DATA? TRACE1": ""})
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    with pytest.raises(InstrumentError, match="leeren Trace"):
        device.read_trace()


# -- Simulation ---------------------------------------------------------
def test_simulation_returns_noise_floor_when_generator_off():
    device = SimulatedFPC1500(points=21)
    device.configure(InstrumentConfig(), 1e6, 1e9)
    assert np.all(device.read_trace() == device.noise_floor_dbm)


def test_simulation_shows_gain_and_compression():
    with SimulatedFPC1500(gain_db=20.0, p_sat_dbm=10.0, points=21) as device:
        assert device.identify().startswith("Simulation")
        device.configure(InstrumentConfig(), 1e6, 1e9)
        device.generator(True)
        device.sweep(0.1)
        device.set_generator_level(-40.0)
        small_signal = device.read_trace()
        device.set_generator_level(10.0)
        saturated = device.read_trace()
    assert small_signal[0] == pytest.approx(-20.0, abs=0.1)
    assert saturated.max() <= 10.0 + 1e-6
    assert device.closed
    assert device.frequency_axis().size == 21


# -- Diagnose -----------------------------------------------------------
def test_list_resources_uses_manager():
    class Manager:
        def list_resources(self):
            return ("TCPIP0::192.168.1.10::inst0::INSTR", "ASRL1::INSTR")

    from amplifier_characterization.instrument import list_resources

    assert list_resources(Manager())[0].startswith("TCPIP0")


def test_list_resources_without_manager_uses_backend(monkeypatch):
    from amplifier_characterization import instrument as mod

    class Manager:
        def list_resources(self):
            return ["X::INSTR"]

    monkeypatch.setattr(mod, "open_resource_manager", lambda: Manager())
    assert mod.list_resources() == ["X::INSTR"]


def test_list_resources_wraps_backend_error():
    class Manager:
        def list_resources(self):
            raise OSError("kaputt")

    from amplifier_characterization.instrument import list_resources

    with pytest.raises(InstrumentError, match="Ressourcenliste"):
        list_resources(Manager())


def test_probe_tcp_detects_open_and_closed_port(monkeypatch):
    from amplifier_characterization import instrument as mod

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(mod.socket, "create_connection", lambda *_a, **_k: Connection())
    assert mod.probe_tcp("192.168.1.187", 5025) is True

    def refuse(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(mod.socket, "create_connection", refuse)
    assert mod.probe_tcp("192.168.1.187", 5025) is False


def test_socket_resource_sets_line_termination():
    session = FakeSession(answers=default_answers())
    device = FPC1500(
        "TCPIP0::192.168.1.187::5025::SOCKET", resource_manager=FakeResourceManager(session)
    )
    device.open()
    assert session.read_termination == "\n"
    assert session.write_termination == "\n"


# -- SCPI-Fehlerwarteschlange -------------------------------------------
class ErrorQueueSession(FakeSession):
    """Liefert eine Folge von SYST:ERR?-Antworten."""

    def __init__(self, queue, **kwargs):
        super().__init__(**kwargs)
        self.queue = list(queue)

    def query(self, command):
        if command == "SYST:ERR?" and self.queue:
            return self.queue.pop(0)
        return super().query(command)


def test_check_errors_stops_at_no_error():
    session = ErrorQueueSession(['0,"No error"'], answers=default_answers())
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    assert device.check_errors() == []


def test_check_errors_collects_entries():
    session = ErrorQueueSession(
        ['-113,"Undefined header;SOUR:POW"', '-222,"Data out of range"', '+0,"No error"'],
        answers=default_answers(),
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    errors = device.check_errors()
    assert len(errors) == 2
    assert "Undefined header" in errors[0]


def test_check_errors_respects_limit():
    session = ErrorQueueSession(
        ['-113,"a"'] * 5, answers={**default_answers(), "SYST:ERR?": '-113,"a"'}
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    assert len(device.check_errors(limit=3)) == 3


def test_configure_raises_only_for_essential_commands():
    session = FakeSession(
        answers=default_answers(),
        errors={"FREQ:STAR 1000000.000000 Hz": '-222,"Data out of range"'},
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    with pytest.raises(InstrumentError, match="Kommando abgelehnt"):
        device.configure(InstrumentConfig(sweep_points=7), 1e6, 5e6)


def test_configure_survives_rejected_optional_command():
    session = FakeSession(
        answers=default_answers(),
        errors={"DET RMS": '-113,"Undefined header;DET"'},
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    seen = []
    device.configure(InstrumentConfig(sweep_points=7), 1e6, 5e6, progress=seen.append)
    assert device.rejected == ['DET RMS -> -113,"Undefined header;DET"']
    assert device.frequency_axis().size == 7


def test_attenuation_is_set_before_reference_level():
    session = FakeSession(answers=default_answers())
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.configure(InstrumentConfig(attenuation_db=10.0, sweep_points=5), 1e6, 5e6)
    assert session.writes.index("INP:ATT 10 dB") < session.writes.index(
        "DISP:TRAC:Y:RLEV 10.00 dBm"
    )


def test_trace_mode_falls_back_to_next_candidate():
    from amplifier_characterization.instrument import TRACE_MODE_CANDIDATES

    session = FakeSession(
        answers=default_answers(),
        errors={TRACE_MODE_CANDIDATES[0]: '-113,"Undefined header"'},
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.configure(InstrumentConfig(sweep_points=5), 1e6, 5e6)
    assert TRACE_MODE_CANDIDATES[1] in session.writes
    assert device.rejected == []


def test_trace_mode_gives_up_after_all_candidates():
    from amplifier_characterization.instrument import TRACE_MODE_CANDIDATES

    session = FakeSession(
        answers=default_answers(),
        errors={command: '-113,"Undefined header"' for command in TRACE_MODE_CANDIDATES},
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.configure(InstrumentConfig(sweep_points=5), 1e6, 5e6)
    assert any("Trace-Modus" in entry for entry in device.rejected)


def test_error_check_switches_off_when_device_stays_silent():
    session = FakeSession(answers=default_answers())
    calls = {"n": 0}
    original = session.query

    def query(command):
        if command == "SYST:ERR?":
            calls["n"] += 1
            raise RuntimeError("VI_ERROR_TMO")
        return original(command)

    session.query = query
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    seen = []
    device.configure(InstrumentConfig(sweep_points=5), 1e6, 5e6, progress=seen.append)
    assert calls["n"] == 1  # nach dem ersten Timeout wird nicht mehr gefragt
    assert any("Fehlerpruefung aus" in line for line in seen)


def test_simulation_has_empty_error_queue():
    assert SimulatedFPC1500().check_errors() == []


def test_configure_reports_every_command():
    device, _ = make_device(points=9)
    device.open()
    seen = []
    device.configure(InstrumentConfig(settle_s=0.0), 1e6, 5e6, progress=seen.append)
    assert seen[0] == "*CLS"
    assert "INST:SEL SAN" in seen
    assert "*OPC?" in seen
    assert "SWE:POIN?" not in seen
    assert any("Testsweep" in line for line in seen)
    assert device.rejected == []


def test_simulation_configure_reports_placeholder():
    device = SimulatedFPC1500()
    seen = []
    device.configure(InstrumentConfig(), 1e6, 5e6, progress=seen.append)
    assert seen == ["Simulation: keine SCPI-Kommandos"]


# -- Signalquelle (Tracking-Generator) ----------------------------------
def test_generator_uses_configured_template():
    session = FakeSession(answers=default_answers())
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.configure(
        InstrumentConfig(
            sweep_points=5,
            generator_state_command="SOUR:GEN:STAT {state}",
            generator_level_command="SOUR:GEN:POW {level:.1f} dBm",
        ),
        1e6,
        5e6,
    )
    device.generator(True)
    device.set_generator_level(-12.0)
    assert "SOUR:GEN:STAT ON" in session.writes
    assert "SOUR:GEN:POW -12.0 dBm" in session.writes


def test_generator_resolves_first_accepted_candidate():
    from amplifier_characterization.instrument import GENERATOR_STATE_CANDIDATES

    first = GENERATOR_STATE_CANDIDATES[0].format(state="ON")
    session = FakeSession(
        answers=default_answers(), errors={first: '-113,"Undefined header"'}
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.configure(InstrumentConfig(sweep_points=5), 1e6, 5e6)
    device.generator(True)
    assert device.generator_state_template == GENERATOR_STATE_CANDIDATES[1]
    device.generator(False)
    assert GENERATOR_STATE_CANDIDATES[1].format(state="OFF") in session.writes


def test_generator_raises_when_no_candidate_works():
    from amplifier_characterization.instrument import GENERATOR_STATE_CANDIDATES

    session = FakeSession(
        answers=default_answers(),
        errors={
            template.format(state="ON"): '-113,"Undefined header"'
            for template in GENERATOR_STATE_CANDIDATES
        },
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.configure(InstrumentConfig(sweep_points=5), 1e6, 5e6)
    with pytest.raises(InstrumentError, match="Undefined header"):
        device.generator(True)


def test_generator_level_resolves_and_reuses_template():
    from amplifier_characterization.instrument import GENERATOR_LEVEL_CANDIDATES

    session = FakeSession(answers=default_answers())
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.configure(InstrumentConfig(sweep_points=5), 1e6, 5e6)
    seen = []
    device.set_generator_level(-40.0, progress=seen.append)
    assert device.generator_level_template == GENERATOR_LEVEL_CANDIDATES[0]
    assert seen == [GENERATOR_LEVEL_CANDIDATES[0].format(level=-40.0)]
    device.set_generator_level(-10.0)
    assert GENERATOR_LEVEL_CANDIDATES[0].format(level=-10.0) in session.writes


def test_generator_level_raises_when_no_candidate_works():
    from amplifier_characterization.instrument import GENERATOR_LEVEL_CANDIDATES

    session = FakeSession(
        answers=default_answers(),
        errors={
            template.format(level=-40.0): '-113,"Undefined header"'
            for template in GENERATOR_LEVEL_CANDIDATES
        },
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.configure(InstrumentConfig(sweep_points=5), 1e6, 5e6)
    with pytest.raises(InstrumentError, match="Generatorpegel gefunden"):
        device.set_generator_level(-40.0)


def test_simulation_generator_accepts_progress_argument():
    device = SimulatedFPC1500()
    device.generator(True, progress=lambda _c: None)
    device.set_generator_level(-3.0, progress=lambda _c: None)
    assert device._level_dbm == -3.0


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("#3012ABC", "ABC"),
        ("FREQ:STAR", "FREQ:STAR"),
        ("#0ABC", "ABC"),
        ("#xABC", "ABC"),
    ],
)
def test_strip_block_header(answer, expected):
    from amplifier_characterization.instrument import strip_block_header

    assert strip_block_header(answer) == expected


# -- Pegelgrenzen der Signalquelle --------------------------------------
def test_generator_limits_queries_min_and_max():
    session = FakeSession(
        answers={
            **default_answers(),
            "SOUR:TG:POW? MIN": "-30.0",
            "SOUR:TG:POW? MAX": "0.0",
        }
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    assert device.generator_limits() == (-30.0, 0.0)


def test_generator_limits_uses_resolved_template():
    session = FakeSession(
        answers={
            **default_answers(),
            "SOUR:GEN:POW? MIN": "-20.0",
            "SOUR:GEN:POW? MAX": "5.0",
        }
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    device.generator_level_template = "SOUR:GEN:POW {level:.2f} dBm"
    assert device.generator_limits() == (-20.0, 5.0)


def test_generator_limits_sorts_swapped_answers():
    session = FakeSession(
        answers={
            **default_answers(),
            "SOUR:TG:POW? MIN": "0.0",
            "SOUR:TG:POW? MAX": "-30.0",
        }
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    assert device.generator_limits() == (-30.0, 0.0)


def test_generator_limits_returns_none_when_unsupported():
    session = FakeSession(answers=default_answers())

    def query(command):
        if command.endswith("? MIN"):
            raise RuntimeError("VI_ERROR_TMO")
        return "0"

    session.query = query
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    assert device.generator_limits() is None


def test_generator_limits_returns_none_on_unparsable_answer():
    session = FakeSession(
        answers={**default_answers(), "SOUR:TG:POW? MIN": "keine Ahnung"}
    )
    device = FPC1500("X", resource_manager=FakeResourceManager(session))
    device.open()
    assert device.generator_limits() is None


def test_simulation_reports_no_generator_limits():
    assert SimulatedFPC1500().generator_limits() is None


def test_configure_keeps_generator_in_tracking_mode():
    device, session = make_device()
    device.open()
    device.configure(InstrumentConfig(sweep_points=5), 1e6, 5e6)
    assert "SOUR:TG:FREQ:AUTO ON" in session.writes
