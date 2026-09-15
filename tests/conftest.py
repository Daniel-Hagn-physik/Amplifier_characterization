"""Gemeinsame Fixtures und Fake-Geraete fuer die Tests."""

from __future__ import annotations

import matplotlib
import pytest

matplotlib.use("Agg", force=True)

from amplifier_characterization.config import (  # noqa: E402
    AmplitudeConfig,
    Config,
    FrequencyConfig,
    InstrumentConfig,
    MeasurementConfig,
    OutputConfig,
)


class FakeSession:
    """Minimaler VISA-Resource-Ersatz: merkt sich Writes, beantwortet Queries.

    ``errors`` ist eine Abbildung Kommando -> SYST:ERR?-Antwort: Nach dem
    genannten Write liefert die naechste Fehlerabfrage diesen Eintrag.
    """

    def __init__(self, answers=None, fail_write=False, fail_query=False, errors=None):
        self.answers = dict(answers or {})
        self.writes = []
        self.queries = []
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.closed = False
        self.fail_write = fail_write
        self.fail_query = fail_query
        self.errors = dict(errors or {})
        self._pending = []

    def write(self, command):
        if self.fail_write:
            raise RuntimeError("boom-write")
        self.writes.append(command)
        if command in self.errors:
            self._pending.append(self.errors[command])

    def query(self, command):
        if self.fail_query:
            raise RuntimeError("boom-query")
        self.queries.append(command)
        if command == "SYST:ERR?" and self._pending:
            return self._pending.pop(0)
        return self.answers.get(command, "0")

    def close(self):
        self.closed = True


class FakeResourceManager:
    def __init__(self, session=None, fail=False):
        self.session = session or FakeSession()
        self.fail = fail
        self.opened = []

    def open_resource(self, resource):
        if self.fail:
            raise RuntimeError("kein Geraet")
        self.opened.append(resource)
        return self.session


def default_answers(points=5, start=1e6, stop=5e6, trace=None):
    trace = trace if trace is not None else [-10.0] * points
    return {
        "*OPC?": "1",
        "FREQ:STAR?": str(start),
        "FREQ:STOP?": str(stop),
        "SWE:POIN?": str(points),
        "TRAC:DATA? TRACE1": ",".join(str(value) for value in trace),
        "*IDN?": "Rohde&Schwarz,FPC1500,123,1.0",
        "SYST:ERR?": '0,"No error"',
    }


@pytest.fixture
def config(tmp_path):
    return Config(
        frequency=FrequencyConfig(start_hz=1e6, stop_hz=5e6, points=3),
        amplitude=AmplitudeConfig(start_dbm=-20.0, stop_dbm=-10.0, points=2),
        instrument=InstrumentConfig(resource="FAKE::INSTR", settle_s=0.0),
        output=OutputConfig(directory=str(tmp_path / "results"), name="test"),
        # amplifier_gain_db ist seit der Pflichtangabe in jeder gueltigen
        # Konfiguration gesetzt - sonst schlaegt Config.validate() fehl.
        measurement=MeasurementConfig(amplifier_gain_db=0.0),
    )
