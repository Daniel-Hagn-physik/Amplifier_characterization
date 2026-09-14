"""Tests fuer den Messablauf, CSV-Ausgabe und die Rastermatrix."""

from __future__ import annotations

import numpy as np
import pytest

from amplifier_characterization import measurement as meas
from amplifier_characterization.instrument import InstrumentError


class StubAnalyzer:
    """Geraet mit fester Frequenzachse und pegelabhaengigem Trace."""

    def __init__(self, axis=None, gain_db=10.0, trace_length=None, fail_off=False):
        self.axis = np.asarray(axis if axis is not None else [1e6, 3e6, 5e6], dtype=float)
        self.gain_db = gain_db
        self.trace_length = trace_length
        self.fail_off = fail_off
        self.levels = []
        self.generator_states = []
        self.sweeps = []
        self.configured = None

    def configure(self, instrument_config, start_hz, stop_hz):
        self.configured = (instrument_config, start_hz, stop_hz)

    def frequency_axis(self):
        return self.axis

    def generator(self, on):
        if not on and self.fail_off:
            raise InstrumentError("Generator klemmt")
        self.generator_states.append(on)

    def set_generator_level(self, level_dbm):
        self.levels.append(level_dbm)

    def sweep(self, settle_s=0.0):
        self.sweeps.append(settle_s)

    def read_trace(self):
        size = self.trace_length or self.axis.size
        return np.full(size, self.levels[-1] + self.gain_db)


def test_now_returns_isoformat():
    assert "T" in meas._now()


def test_run_measurement_fills_grid(config):
    analyzer = StubAnalyzer()
    messages = []
    points = meas.run_measurement(analyzer, config, progress=messages.append,
                                  clock=lambda: "2026-09-14T10:00:00")
    assert len(points) == 3 * 2
    assert analyzer.configured[1:] == (1e6, 5e6)
    assert analyzer.levels == pytest.approx([-20.0, -10.0])
    assert analyzer.generator_states == [True, False]
    assert len(messages) == 2
    assert all(point.gain_db == pytest.approx(10.0) for point in points)
    assert points[0].timestamp == "2026-09-14T10:00:00"


def test_run_measurement_without_progress_callback(config):
    points = meas.run_measurement(StubAnalyzer(), config)
    assert points


def test_run_measurement_rejects_degenerate_axis(config):
    with pytest.raises(meas.MeasurementError, match="Frequenzachse"):
        meas.run_measurement(StubAnalyzer(axis=[1e6]), config)


def test_run_measurement_rejects_trace_length_mismatch(config):
    with pytest.raises(meas.MeasurementError, match="Trace-Laenge"):
        meas.run_measurement(StubAnalyzer(trace_length=7), config)


def test_run_measurement_survives_generator_shutdown_error(config):
    messages = []
    points = meas.run_measurement(
        StubAnalyzer(fail_off=True), config, progress=messages.append
    )
    assert len(points) == 6
    assert any("Warnung" in message for message in messages)


def test_csv_roundtrip(tmp_path, config):
    points = meas.run_measurement(StubAnalyzer(), config)
    path = meas.write_csv(points, tmp_path / "sub" / "out.csv")
    assert path.exists()
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == meas.CSV_FIELDS
    restored = meas.read_csv(path)
    assert restored == points


def test_to_matrix_sorts_into_grid(config):
    points = meas.run_measurement(StubAnalyzer(), config)
    frequencies, levels, p_out, gain = meas.to_matrix(points)
    assert frequencies.size == 3
    assert levels == pytest.approx([-20.0, -10.0])
    assert p_out.shape == (2, 3)
    assert np.allclose(gain, 10.0)


def test_to_matrix_rejects_empty_input():
    with pytest.raises(meas.MeasurementError, match="Keine Messpunkte"):
        meas.to_matrix([])


def test_run_measurement_reports_rejected_commands(config):
    analyzer = StubAnalyzer()
    analyzer.rejected = ['DET RMS -> -113,"Undefined header"']
    messages = []
    meas.run_measurement(analyzer, config, progress=messages.append)
    assert any("abgelehnt" in message for message in messages)


def test_run_measurement_rejects_levels_outside_generator_range(config):
    class Limited(StubAnalyzer):
        def generator_limits(self):
            return (-15.0, 0.0)

    with pytest.raises(meas.MeasurementError, match="Generatorbereichs"):
        meas.run_measurement(Limited(), config)


def test_run_measurement_accepts_levels_inside_generator_range(config):
    class Limited(StubAnalyzer):
        def generator_limits(self):
            return (-40.0, 0.0)

    assert meas.run_measurement(Limited(), config)
