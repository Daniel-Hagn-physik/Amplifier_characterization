"""Tests fuer den Messablauf, CSV-Ausgabe und die Rastermatrix."""

from __future__ import annotations

import numpy as np
import pytest

from amplifier_characterization import measurement as meas
from amplifier_characterization.instrument import InstrumentError


class StubAnalyzer:
    """Geraet mit fester Frequenzachse und pegelabhaengigem Trace."""

    def __init__(self, axis=None, gain_db=10.0, trace_length=None, fail_off=False):
        # fail_off greift erst beim Abschalten nach der Messung, nicht bei der
        # Rauschmessung davor.
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

    def generator(self, on, progress=None):
        del progress
        if not on and self.fail_off and self.levels:
            raise InstrumentError("Generator klemmt")
        self.generator_states.append(on)

    def set_generator_level(self, level_dbm, progress=None):
        del progress
        self.levels.append(level_dbm)

    def sweep(self, settle_s=0.0):
        self.sweeps.append(settle_s)

    def read_trace(self):
        size = self.trace_length or self.axis.size
        if not self.levels:  # Rauschmessung vor der ersten Pegelvorgabe
            return np.full(size, -90.0)
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
    assert analyzer.generator_states == [False, True, False]
    assert any("[1/2]" in message for message in messages)
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


# -- THRU-Referenz ------------------------------------------------------
def make_points(levels, frequencies, offset):
    return [
        meas.Point(
            timestamp="t",
            frequency_hz=frequency,
            p_in_dbm=level,
            p_out_dbm=level + offset,
            gain_db=offset,
        )
        for level in levels
        for frequency in frequencies
    ]


def test_apply_reference_removes_cable_loss():
    thru = make_points([-30.0, -20.0], [1e6, 2e6], -0.6)
    dut = make_points([-30.0, -20.0], [1e6, 2e6], 19.4)
    corrected = meas.apply_reference(dut, thru)
    assert all(point.gain_corr_db == pytest.approx(20.0) for point in corrected)
    assert corrected[0].p_ref_dbm == pytest.approx(-30.6)
    assert meas.has_reference(corrected)


def test_apply_reference_rejects_empty_reference():
    with pytest.raises(meas.MeasurementError, match="keine Punkte"):
        meas.apply_reference(make_points([-30.0], [1e6], 0.0), [])


def test_apply_reference_rejects_mismatched_grid():
    thru = make_points([-30.0], [1e6], -0.6)
    dut = make_points([-30.0], [1e6, 2e6], 19.4)
    with pytest.raises(meas.MeasurementError, match="fehlen in der Referenz"):
        meas.apply_reference(dut, thru)


def test_has_reference_is_false_without_correction():
    assert not meas.has_reference(make_points([-30.0], [1e6], 0.0))
    assert not meas.has_reference([])


def test_cable_loss_averages_insertion_loss():
    thru = make_points([-30.0, -20.0], [1e6, 2e6], -0.6)
    assert meas.cable_loss(thru) == pytest.approx(-0.6)


def test_cable_loss_rejects_empty_reference():
    with pytest.raises(meas.MeasurementError, match="keine Punkte"):
        meas.cable_loss([])


def test_csv_roundtrip_keeps_reference_columns(tmp_path):
    thru = make_points([-30.0], [1e6, 2e6], -0.6)
    dut = make_points([-30.0], [1e6, 2e6], 19.4)
    corrected = meas.apply_reference(dut, thru)
    path = meas.write_csv(corrected, tmp_path / "dut.csv")
    assert meas.read_csv(path) == corrected


def test_read_csv_tolerates_missing_reference_columns(tmp_path):
    path = tmp_path / "alt.csv"
    path.write_text(
        "timestamp,frequency_hz,p_in_dbm,p_out_dbm,gain_db\n"
        "t,1000000.0,-30.0,-30.6,-0.6\n",
        encoding="utf-8",
    )
    restored = meas.read_csv(path)
    assert restored[0].p_ref_dbm is None
    assert restored[0].gain_corr_db is None


def test_to_matrix_uses_corrected_gain_when_available():
    thru = make_points([-30.0, -20.0], [1e6, 2e6], -0.6)
    dut = make_points([-30.0, -20.0], [1e6, 2e6], 19.4)
    _f, _l, _p, gain = meas.to_matrix(meas.apply_reference(dut, thru))
    assert np.allclose(gain, 20.0)


# -- Ueberlastschutz ----------------------------------------------------
class HotAnalyzer(StubAnalyzer):
    """Liefert einen Trace, dessen Pegel mit dem Generatorpegel steigt."""

    def read_trace(self):
        if not self.levels:
            return np.full(self.axis.size, -90.0)
        return np.full(self.axis.size, self.levels[-1] + self.gain_db)


def test_overload_aborts_and_keeps_measured_points(config):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    hot = dataclasses.replace(
        config, measurement=MeasurementConfig(max_input_dbm=0.0)
    )
    analyzer = HotAnalyzer(gain_db=15.0)
    with pytest.raises(meas.Overload) as excinfo:
        meas.run_measurement(analyzer, hot)
    # Stufe 1 (-20 dBm -> -5 dBm) geht durch, Stufe 2 (-10 -> +5) bricht ab
    assert len(excinfo.value.points) == 3
    assert "ABBRUCH" in str(excinfo.value)
    assert analyzer.generator_states == [False, True, False]


def test_overload_on_first_level_keeps_nothing(config):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    hot = dataclasses.replace(
        config, measurement=MeasurementConfig(max_input_dbm=-100.0)
    )
    with pytest.raises(meas.Overload) as excinfo:
        meas.run_measurement(HotAnalyzer(), hot)
    assert excinfo.value.points == []


class SaturatingAnalyzer(HotAnalyzer):
    """Verstaerker, der frueh in die Saettigung geht - Hochrechnung ueberschaetzt."""

    cap = -8.0

    def read_trace(self):
        if not self.levels:
            return np.full(self.axis.size, -90.0)
        return np.full(
            self.axis.size, min(self.levels[-1] + self.gain_db, self.cap)
        )


def test_projection_warns_before_the_limit_is_reached(config):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    tight = dataclasses.replace(
        config, measurement=MeasurementConfig(max_input_dbm=-5.0)
    )
    messages = []
    points = meas.run_measurement(
        SaturatingAnalyzer(gain_db=10.0), tight, progress=messages.append
    )
    assert any("hochgerechnet" in message for message in messages)
    assert len(points) == 6  # trotz Warnung komplett durchgelaufen


def test_no_projection_warning_when_headroom_is_sufficient(config):
    messages = []
    meas.run_measurement(StubAnalyzer(), config, progress=messages.append)
    assert not any("hochgerechnet" in message for message in messages)


def test_empty_measurement_trace_is_reported(config):
    class SilentWhenOn(StubAnalyzer):
        def read_trace(self):
            if not self.levels:
                return np.full(self.axis.size, -200.0)
            return np.array([])

    with pytest.raises(meas.MeasurementError, match="Trace-Laenge"):
        meas.run_measurement(SilentWhenOn(), config)


def test_warns_once_when_level_exceeds_reference_level(config):
    import dataclasses

    from amplifier_characterization.config import InstrumentConfig

    low_reference = dataclasses.replace(
        config,
        instrument=dataclasses.replace(
            config.instrument, ref_level_dbm=-15.0, settle_s=0.0
        ),
    )
    messages = []
    meas.run_measurement(StubAnalyzer(), low_reference, progress=messages.append)
    warnings = [m for m in messages if "ueber dem Referenzpegel" in m]
    assert len(warnings) == 1


def test_no_reference_level_warning_with_headroom(config):
    messages = []
    meas.run_measurement(StubAnalyzer(), config, progress=messages.append)
    assert not any("ueber dem Referenzpegel" in message for message in messages)


# -- Rauschgrenze und Pegelbegrenzung -----------------------------------
class NoisyAnalyzer(StubAnalyzer):
    """Liefert bei ausgeschaltetem Generator einen festen Rauschpegel."""

    def __init__(self, noise_dbm=-55.0, **kwargs):
        super().__init__(**kwargs)
        self.noise_dbm = noise_dbm
        self.on = False

    def generator(self, on, progress=None):
        self.on = bool(on)
        super().generator(on, progress)

    def read_trace(self):
        if not self.on:
            return np.full(self.axis.size, self.noise_dbm)
        return super().read_trace()


def test_measure_noise_floor_uses_generator_off(config):
    analyzer = NoisyAnalyzer(noise_dbm=-52.0)
    assert meas.measure_noise_floor(analyzer, config) == pytest.approx(-52.0)
    assert analyzer.on is False


def test_measure_noise_floor_rejects_empty_trace(config):
    class Silent(StubAnalyzer):
        def read_trace(self):
            return np.array([])

    with pytest.raises(meas.MeasurementError, match="Leerer Trace"):
        meas.measure_noise_floor(Silent(), config)


def _with_measurement(config, **kwargs):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    return dataclasses.replace(config, measurement=MeasurementConfig(**kwargs))


def test_level_limits_without_known_gain_keeps_everything(config):
    levels = np.arange(-30.0, 0.1, 1.0)
    limits = meas.level_limits(
        levels, -55.0, _with_measurement(config, external_pad_db=10.0), use_gain=False
    )
    assert limits.highest_usable_dbm is None
    assert limits.kept.size == levels.size
    assert limits.dropped_high.size == 0


def test_level_limits_upper_bound_from_gain(config):
    levels = np.arange(-30.0, 0.1, 1.0)
    limits = meas.level_limits(
        levels,
        -55.0,
        _with_measurement(
            config, external_pad_db=10.0, amplifier_gain_db=40.0, max_input_dbm=28.0
        ),
        use_gain=True,
    )
    assert limits.highest_usable_dbm == pytest.approx(-2.0)
    assert limits.dropped_high.size == 2


def test_level_limits_ignores_gain_for_thru_run(config):
    levels = np.arange(-30.0, 0.1, 1.0)
    limits = meas.level_limits(
        levels, -55.0, _with_measurement(config, amplifier_gain_db=40.0), use_gain=False
    )
    assert limits.highest_usable_dbm is None
    assert limits.dropped_high.size == 0


def test_limit_report_mentions_the_upper_bound(config):
    levels = np.arange(-30.0, 0.1, 1.0)
    settings = _with_measurement(
        config, external_pad_db=10.0, amplifier_gain_db=40.0, max_input_dbm=28.0
    )
    limits = meas.level_limits(levels, -45.0, settings, use_gain=True)
    text = "\n".join(meas.limit_report(limits, settings, levels.size))
    assert "Rauschgrenze gemessen" in text
    assert "Mindestabstand" in text
    assert "entfallen" in text and "oben" in text


def test_limit_report_without_an_upper_bound(config):
    levels = np.arange(-30.0, 0.1, 1.0)
    limits = meas.level_limits(levels, -80.0, config, use_gain=False)
    text = "\n".join(meas.limit_report(limits, config, levels.size))
    assert "Nutzbar bis" not in text


def test_run_measurement_discards_levels_too_close_to_noise(config):
    """Verworfen wird nach dem gemessenen Abstand, nicht nach einer Schaetzung."""
    settings = _with_measurement(config, noise_margin_db=15.0)
    # Rauschen -25 dBm: Stufe -20 dBm liefert -10 dBm (15 dB Abstand, knapp ok
    # ist sie nicht: -10 - (-25) = 15 -> genau an der Grenze, also verworfen)
    analyzer = NoisyAnalyzer(noise_dbm=-24.0)
    messages = []
    points = meas.run_measurement(analyzer, settings, progress=messages.append)
    assert {p.p_in_dbm for p in points} == {-10.0}
    assert any("ueber dem Rauschen" in message for message in messages)


def test_run_measurement_fails_when_every_level_drowns_in_noise(config):
    settings = _with_measurement(config, noise_margin_db=15.0)
    with pytest.raises(meas.MeasurementError, match="ausreichend ueber dem"):
        meas.run_measurement(NoisyAnalyzer(noise_dbm=0.0), settings)


def test_run_measurement_fails_when_gain_excludes_everything(config):
    settings = _with_measurement(
        config, amplifier_gain_db=60.0, max_input_dbm=0.0, noise_margin_db=0.0
    )
    with pytest.raises(meas.MeasurementError, match="Keine Amplitudenstufe"):
        meas.run_measurement(NoisyAnalyzer(noise_dbm=-90.0), settings, is_thru=False)


def test_run_measurement_without_auto_limit_and_nothing_to_drop(config):
    settings = _with_measurement(config, auto_limit_levels=False)
    messages = []
    meas.run_measurement(NoisyAnalyzer(noise_dbm=-90.0), settings,
                         progress=messages.append)
    assert not any("auto_limit_levels" in message for message in messages)


def test_run_measurement_without_auto_limit_keeps_levels_above_the_gain_bound(config):
    settings = _with_measurement(
        config, amplifier_gain_db=40.0, max_input_dbm=0.0,
        auto_limit_levels=False, noise_margin_db=0.0,
    )
    messages = []
    meas.run_measurement(NoisyAnalyzer(noise_dbm=-90.0), settings,
                         progress=messages.append)
    assert any("auto_limit_levels ist aus" in message for message in messages)


def test_nominal_pad_value_does_not_touch_the_measured_data(config):
    """Der Nennwert aus der Config veraendert die Messwerte nicht."""
    without = meas.run_measurement(StubAnalyzer(gain_db=10.0), config)
    with_nominal = meas.run_measurement(
        StubAnalyzer(gain_db=10.0), _with_measurement(config, external_pad_db=20.0)
    )
    assert with_nominal[0].p_out_dbm == pytest.approx(without[0].p_out_dbm)


def test_unknown_attenuation_cancels_and_yields_true_output_power(config):
    """Die Streckendaempfung muss nicht bekannt sein - die Referenz misst sie.

    Strecke daempft unbekannte 19.7 dB, Verstaerker verstaerkt 36 dB.
    """
    thru = meas.run_measurement(StubAnalyzer(gain_db=-19.7), config, is_thru=True)
    dut = meas.run_measurement(StubAnalyzer(gain_db=36.0 - 19.7), config)
    corrected = meas.apply_reference(dut, thru)
    assert all(p.gain_corr_db == pytest.approx(36.0) for p in corrected)
    for point in corrected:
        assert point.p_amp_dbm == pytest.approx(point.p_in_dbm + 36.0)
    # Rohwert am Analyzer bleibt erhalten und ist um die Daempfung kleiner
    assert corrected[-1].p_out_dbm == pytest.approx(
        corrected[-1].p_amp_dbm - 19.7
    )


def test_to_matrix_reports_amplifier_output_power_when_calibrated(config):
    thru = meas.run_measurement(StubAnalyzer(gain_db=-19.7), config, is_thru=True)
    dut = meas.run_measurement(StubAnalyzer(gain_db=36.0 - 19.7), config)
    corrected = meas.apply_reference(dut, thru)
    _f, levels, p_out, _g = meas.to_matrix(corrected)
    assert np.allclose(p_out, levels[:, None] + 36.0)


def test_has_reference_needs_the_derived_output_power():
    point = meas.Point(timestamp="t", frequency_hz=1e6, p_in_dbm=-10.0,
                       p_out_dbm=0.0, gain_db=10.0, p_ref_dbm=-1.0,
                       gain_corr_db=1.0)
    assert not meas.has_reference([point])


# -- Vorab-Test der Verstaerkung ----------------------------------------
def _reference(config, path_loss_db=19.7):
    """THRU-Referenz einer Strecke mit unbekannter Daempfung."""
    return meas.run_measurement(
        StubAnalyzer(gain_db=-path_loss_db), config, is_thru=True
    )


def test_reference_peaks_takes_the_highest_value_per_level(config):
    reference = _reference(config)
    peaks = meas.reference_peaks(reference)
    assert set(peaks) == {-20.0, -10.0}
    assert peaks[-20.0] == pytest.approx(-39.7)


def test_probe_gain_measures_the_true_gain(config):
    reference = _reference(config)
    analyzer = StubAnalyzer(gain_db=36.0 - 19.7)
    messages = []
    probe = meas.probe_gain(
        analyzer, config, config.amplitude_grid, reference, messages.append
    )
    assert probe.gain_db == pytest.approx(36.0)
    assert probe.levels_dbm[0] == pytest.approx(-20.0)
    assert any("Verstaerkung" in message for message in messages)


def test_probe_gain_derives_the_safe_upper_level(config):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    settings = dataclasses.replace(
        config, measurement=MeasurementConfig(max_input_dbm=0.0)
    )
    reference = _reference(settings)
    probe = meas.probe_gain(
        StubAnalyzer(gain_db=36.0 - 19.7),
        settings,
        settings.amplitude_grid,
        reference,
        lambda _m: None,
    )
    # letzter Messpunkt: -20 dBm -> -3.7 dBm, also 3.7 dB Luft bis 0 dBm
    assert probe.highest_safe_dbm == pytest.approx(-16.3)


def test_probe_gain_stops_before_exceeding_the_ceiling(config):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    settings = dataclasses.replace(
        config,
        measurement=MeasurementConfig(max_input_dbm=-2.0, gain_probe_step_db=3.0),
    )
    reference = _reference(settings)
    messages = []
    probe = meas.probe_gain(
        StubAnalyzer(gain_db=36.0 - 19.7),
        settings,
        settings.amplitude_grid,
        reference,
        messages.append,
    )
    assert len(probe.levels_dbm) == 1
    assert any("zu hoch" in message for message in messages)


def test_probe_gain_breaks_when_ceiling_is_already_exceeded(config):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    settings = dataclasses.replace(
        config, measurement=MeasurementConfig(max_input_dbm=-10.0)
    )
    reference = _reference(settings)
    probe = meas.probe_gain(
        StubAnalyzer(gain_db=60.0 - 19.7),
        settings,
        settings.amplitude_grid,
        reference,
        lambda _m: None,
    )
    assert len(probe.levels_dbm) == 1


def test_probe_gain_skips_levels_missing_from_the_reference(config):
    reference = [p for p in _reference(config) if p.p_in_dbm != -20.0]
    probe = meas.probe_gain(
        StubAnalyzer(gain_db=36.0 - 19.7),
        config,
        config.amplitude_grid,
        reference,
        lambda _m: None,
    )
    assert probe.levels_dbm == [pytest.approx(-10.0)]


def test_probe_gain_without_any_usable_level(config):
    with pytest.raises(meas.MeasurementError, match="Vorab-Test nicht moeglich"):
        meas.probe_gain(
            StubAnalyzer(), config, config.amplitude_grid, [], lambda _m: None
        )


def test_probe_gain_rejects_empty_trace(config):
    class Silent(StubAnalyzer):
        def read_trace(self):
            if not self.levels:
                return np.full(self.axis.size, -90.0)
            return np.array([])

    with pytest.raises(meas.MeasurementError, match="Leerer Trace im Vorab-Test"):
        meas.probe_gain(
            Silent(), config, config.amplitude_grid, _reference(config),
            lambda _m: None
        )


def test_run_measurement_limits_levels_from_the_probe(config):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    settings = dataclasses.replace(
        config, measurement=MeasurementConfig(max_input_dbm=0.0)
    )
    reference = _reference(settings)
    messages = []
    points = meas.run_measurement(
        StubAnalyzer(gain_db=36.0 - 19.7),
        settings,
        progress=messages.append,
        reference=reference,
    )
    assert any("entfallen oben" in message for message in messages)
    assert {p.p_in_dbm for p in points} == {-20.0}


def test_run_measurement_warns_instead_of_limiting_when_disabled(config):
    """Ohne auto_limit greift nur noch die Notbremse waehrend der Messung."""
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    settings = dataclasses.replace(
        config,
        measurement=MeasurementConfig(max_input_dbm=0.0, auto_limit_levels=False),
    )
    messages = []
    with pytest.raises(meas.Overload):
        meas.run_measurement(
            StubAnalyzer(gain_db=36.0 - 19.7),
            settings,
            progress=messages.append,
            reference=_reference(settings),
        )
    assert any("sicheren Grenze" in message for message in messages)


def test_run_measurement_fails_when_even_the_lowest_level_is_unsafe(config):
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    settings = dataclasses.replace(
        config, measurement=MeasurementConfig(max_input_dbm=-10.0)
    )
    with pytest.raises(meas.MeasurementError, match="kleinste Eingangspegel"):
        meas.run_measurement(
            StubAnalyzer(gain_db=36.0 - 19.7),
            settings,
            reference=_reference(settings),
        )


def test_thru_run_does_not_probe(config):
    messages = []
    meas.run_measurement(
        StubAnalyzer(gain_db=-19.7), config, progress=messages.append,
        is_thru=True, reference=_reference(config)
    )
    assert not any("Vorab-Test" in message for message in messages)


def test_probe_gain_uses_every_requested_point(config):
    """Reicht die Luft, werden gain_probe_points Stufen vermessen."""
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    levels = np.arange(-30.0, 0.1, 1.0)
    settings = dataclasses.replace(
        config,
        amplitude=type(config.amplitude)(start_dbm=-30.0, stop_dbm=0.0, points=31),
        measurement=MeasurementConfig(
            max_input_dbm=60.0, gain_probe_points=3, gain_probe_step_db=3.0
        ),
    )
    reference = meas.run_measurement(
        StubAnalyzer(gain_db=-19.7), settings, is_thru=True
    )
    probe = meas.probe_gain(
        StubAnalyzer(gain_db=10.0 - 19.7), settings, levels, reference,
        lambda _m: None
    )
    assert probe.levels_dbm == [pytest.approx(-30.0), pytest.approx(-27.0),
                                pytest.approx(-24.0)]
    assert probe.gain_db == pytest.approx(10.0)


def test_probe_gain_stops_when_the_measured_peak_exceeds_the_ceiling(config):
    """Liegt der erste Messpunkt schon darueber, bricht der Test sofort ab."""
    import dataclasses

    from amplifier_characterization.config import MeasurementConfig

    levels = np.arange(-30.0, 0.1, 1.0)
    wide = dataclasses.replace(
        config,
        amplitude=type(config.amplitude)(start_dbm=-30.0, stop_dbm=0.0, points=31),
    )
    reference = meas.run_measurement(
        StubAnalyzer(gain_db=-19.7), wide, is_thru=True
    )
    settings = dataclasses.replace(
        wide,
        measurement=MeasurementConfig(max_input_dbm=-25.0, gain_probe_step_db=3.0),
    )
    messages = []
    probe = meas.probe_gain(
        StubAnalyzer(gain_db=40.0 - 19.7), settings, levels, reference,
        messages.append
    )
    assert len(probe.levels_dbm) == 1
    assert not any("waere zu hoch" in message for message in messages)


def test_read_csv_backfills_the_amplifier_power_column(tmp_path):
    """Messungen von vor der Spalte p_amp_dbm bleiben voll auswertbar."""
    path = tmp_path / "alt.csv"
    path.write_text(
        "timestamp,frequency_hz,p_in_dbm,p_out_dbm,gain_db,p_ref_dbm,gain_corr_db\n"
        "t,1000000.0,-10.0,5.0,15.0,-15.0,20.0\n",
        encoding="utf-8",
    )
    restored = meas.read_csv(path)
    assert restored[0].p_amp_dbm == pytest.approx(10.0)
    assert meas.has_reference(restored)


def test_levels_missing_from_the_reference_are_skipped(config):
    """Schwankt die Rauschgrenze leicht, darf das Raster nicht auseinanderlaufen."""
    reference = [p for p in _reference(config) if p.p_in_dbm != -20.0]
    messages = []
    points = meas.run_measurement(
        StubAnalyzer(gain_db=36.0 - 19.7),
        config,
        progress=messages.append,
        reference=reference,
    )
    assert {p.p_in_dbm for p in points} == {-10.0}
    assert any("fehlen in der Referenz" in message for message in messages)
    # und die Korrektur laeuft danach ohne Luecke durch
    assert meas.apply_reference(points, reference)


def test_measurement_fails_when_no_level_is_in_the_reference(config):
    with pytest.raises(meas.MeasurementError, match="kommt in der Referenz"):
        meas.run_measurement(
            StubAnalyzer(gain_db=36.0 - 19.7),
            config,
            reference=[p for p in _reference(config) if p.p_in_dbm > 0.0],
        )
