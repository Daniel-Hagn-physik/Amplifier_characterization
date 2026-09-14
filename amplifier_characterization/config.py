"""Konfiguration fuer die S12-Messung (Laden, Validieren, Ableiten der Raster)."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Type, TypeVar

import numpy as np

DEFAULT_RESOURCE = "TCPIP0::192.168.1.187::hislip0::INSTR"
"""VISA-Adresse des FPC1500 im lokalen Netz (Ethernet, IP 192.168.1.187)."""

T = TypeVar("T")


class ConfigError(ValueError):
    """Fehlerhafte oder unvollstaendige Konfigurationsdatei."""


def _build(cls: Type[T], data: Mapping[str, Any], section: str) -> T:
    """Baut ein Dataclass-Objekt und meldet unbekannte Schluessel als Fehler."""
    if not isinstance(data, Mapping):
        raise ConfigError(f"Abschnitt '{section}' muss ein JSON-Objekt sein.")
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(
            f"Unbekannte Schluessel in '{section}': {', '.join(unknown)}. "
            f"Erlaubt sind: {', '.join(sorted(known))}."
        )
    try:
        return cls(**data)  # type: ignore[call-arg]
    except TypeError as exc:  # fehlende Pflichtfelder
        raise ConfigError(f"Abschnitt '{section}' ist unvollstaendig: {exc}") from exc


def _linspace(start: float, stop: float, points: int, label: str) -> np.ndarray:
    """Lineares Raster mit Validierung; bei einem Punkt wird nur der Start genutzt."""
    if points < 1:
        raise ConfigError(f"{label}: 'points' muss mindestens 1 sein (ist {points}).")
    if stop < start:
        raise ConfigError(f"{label}: 'stop' ({stop}) liegt unter 'start' ({start}).")
    if points == 1:
        return np.array([float(start)])
    return np.linspace(float(start), float(stop), int(points))


@dataclass(frozen=True)
class InstrumentConfig:
    """Verbindungs- und Analyzer-Einstellungen."""

    resource: str = DEFAULT_RESOURCE
    timeout_ms: int = 20000
    rbw_hz: Optional[float] = None
    vbw_hz: Optional[float] = None
    ref_level_dbm: float = 10.0
    attenuation_db: Optional[float] = None
    settle_s: float = 0.2
    sweep_points: Optional[int] = None
    generator_state_command: Optional[str] = None
    generator_level_command: Optional[str] = None

    def validate(self) -> None:
        if not self.resource:
            raise ConfigError("instrument.resource darf nicht leer sein.")
        if self.timeout_ms <= 0:
            raise ConfigError("instrument.timeout_ms muss positiv sein.")
        if self.settle_s < 0:
            raise ConfigError("instrument.settle_s darf nicht negativ sein.")
        if self.sweep_points is not None and self.sweep_points < 2:
            raise ConfigError("instrument.sweep_points muss mindestens 2 sein.")
        if self.generator_state_command and "{state}" not in self.generator_state_command:
            raise ConfigError(
                "instrument.generator_state_command braucht den Platzhalter {state}."
            )
        if self.generator_level_command and "{level" not in self.generator_level_command:
            raise ConfigError(
                "instrument.generator_level_command braucht den Platzhalter {level}."
            )


@dataclass(frozen=True)
class FrequencyConfig:
    """Frequenzbereich und Anzahl der Frequenzschritte des RF-Signals."""

    start_hz: float
    stop_hz: float
    points: int

    def grid(self) -> np.ndarray:
        if self.start_hz <= 0:
            raise ConfigError("frequency.start_hz muss groesser als 0 sein.")
        return _linspace(self.start_hz, self.stop_hz, self.points, "frequency")


@dataclass(frozen=True)
class AmplitudeConfig:
    """Amplitudenbereich (Tracking-Generator-Pegel) und Anzahl der Stufen."""

    start_dbm: float
    stop_dbm: float
    points: int

    def grid(self) -> np.ndarray:
        return _linspace(self.start_dbm, self.stop_dbm, self.points, "amplitude")


@dataclass(frozen=True)
class OutputConfig:
    """Ablage der Ergebnisse; das Verzeichnis enthaelt immer das aktuelle Datum."""

    directory: str = "results"
    name: str = "s12"
    date_format: str = "%Y-%m-%d"

    def run_directory(self, today: Optional[date] = None) -> Path:
        stamp = (today or date.today()).strftime(self.date_format)
        return Path(self.directory).expanduser() / f"{stamp}_{self.name}"


@dataclass(frozen=True)
class MeasurementConfig:
    """Ablauf-Optionen der Messung."""

    skip_thru_prompt: bool = False
    thru_message: str = (
        "Bitte jetzt eine THRU-Kalibrierung mit den Messkabeln durchfuehren\n"
        "(Kabel ohne DUT direkt verbinden, Normalize/THRU am FPC1500 ausloesen).\n\n"
        "Danach dieses Fenster schliessen - die Messung startet anschliessend."
    )


@dataclass(frozen=True)
class SimulationConfig:
    """Modell des simulierten Pruefaufbaus fuer --dry-run.

    Die Voreinstellung bildet den THRU-Fall ab: beide Kalibrierkabel direkt
    verbunden, kein Verstaerker - also flacher, leicht negativer Gain durch
    die Kabeldaempfung und keine Kompression.
    """

    gain_db: float = -0.6
    p_sat_dbm: float = 40.0
    rolloff_db_per_ghz: float = 0.0
    points: int = 711
    noise_floor_dbm: float = -100.0

    def validate(self) -> None:
        if self.points < 2:
            raise ConfigError("simulation.points muss mindestens 2 sein.")


@dataclass(frozen=True)
class Config:
    """Gesamte Messkonfiguration."""

    frequency: FrequencyConfig
    amplitude: AmplitudeConfig
    instrument: InstrumentConfig = InstrumentConfig()
    output: OutputConfig = OutputConfig()
    measurement: MeasurementConfig = MeasurementConfig()
    simulation: SimulationConfig = SimulationConfig()

    def validate(self) -> None:
        self.instrument.validate()
        self.simulation.validate()
        self.frequency.grid()
        self.amplitude.grid()

    @property
    def frequency_grid(self) -> np.ndarray:
        return self.frequency.grid()

    @property
    def amplitude_grid(self) -> np.ndarray:
        return self.amplitude.grid()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Config":
        if not isinstance(data, Mapping):
            raise ConfigError("Die Konfiguration muss ein JSON-Objekt sein.")
        allowed = {
            "frequency",
            "amplitude",
            "instrument",
            "output",
            "measurement",
            "simulation",
        }
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ConfigError(
                f"Unbekannte Abschnitte: {', '.join(unknown)}. "
                f"Erlaubt sind: {', '.join(sorted(allowed))}."
            )
        for required in ("frequency", "amplitude"):
            if required not in data:
                raise ConfigError(f"Abschnitt '{required}' fehlt in der Konfiguration.")
        config = cls(
            frequency=_build(FrequencyConfig, data["frequency"], "frequency"),
            amplitude=_build(AmplitudeConfig, data["amplitude"], "amplitude"),
            instrument=_build(
                InstrumentConfig, data.get("instrument", {}), "instrument"
            ),
            output=_build(OutputConfig, data.get("output", {}), "output"),
            measurement=_build(
                MeasurementConfig, data.get("measurement", {}), "measurement"
            ),
            simulation=_build(
                SimulationConfig, data.get("simulation", {}), "simulation"
            ),
        )
        config.validate()
        return config


def load_config(path: Path | str) -> Config:
    """Liest eine JSON-Konfiguration von der Platte."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Konfigurationsdatei nicht lesbar: {path} ({exc})") from exc
    try:
        data: Dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Ungueltiges JSON in {path}: {exc}") from exc
    return Config.from_dict(data)


def make_output_directory(config: Config, today: Optional[date] = None) -> Path:
    """Legt das datierte Ausgabeverzeichnis an und gibt es zurueck."""
    directory = config.output.run_directory(today)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def describe(config: Config) -> List[str]:
    """Kurze, menschenlesbare Zusammenfassung der Messparameter."""
    freq = config.frequency_grid
    amp = config.amplitude_grid
    return [
        f"VISA-Adresse      : {config.instrument.resource}",
        f"Frequenzbereich   : {freq[0] / 1e6:.3f} - {freq[-1] / 1e6:.3f} MHz "
        f"({len(freq)} Punkte)",
        f"Amplitudenbereich : {amp[0]:.1f} - {amp[-1]:.1f} dBm ({len(amp)} Stufen)",
        f"Messpunkte gesamt : {len(freq) * len(amp)}",
    ]
