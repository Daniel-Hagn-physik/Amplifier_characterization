"""Konfiguration fuer die S12-Messung (Laden, Validieren, Ableiten der Raster)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields, replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type, TypeVar

import numpy as np

DEFAULT_RESOURCE = "TCPIP0::192.168.1.187::hislip0::INSTR"
"""VISA-Adresse des FPC1500 im lokalen Netz (Ethernet, IP 192.168.1.187).

HiSLIP ist bei diesem Geraet der einzige Weg hinein: der SCPI-Raw-Socket auf
Port 5025 ist zu und VXI-11 scheitert (bei der Inbetriebnahme nachgeprueft).
Ein Resource-String mit ::5025::SOCKET oder ::INSTR ohne hislip0 laeuft
deshalb in einen Timeout, auch wenn das Geraet einwandfrei im Netz haengt.
"""

T = TypeVar("T")


class ConfigError(ValueError):
    """Fehlerhafte oder unvollstaendige Konfigurationsdatei."""


def _without_comments(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Entfernt Kommentarschluessel.

    JSON kennt keine Kommentare. Damit die Config trotzdem selbsterklaerend
    bleibt, wird jeder Schluessel ignoriert, der mit einem Unterstrich beginnt -
    ``"_hinweis": "..."`` ist also erlaubt und ohne Wirkung.
    """
    return {key: value for key, value in data.items() if not key.startswith("_")}


def _build(cls: Type[T], data: Mapping[str, Any], section: str) -> T:
    """Baut ein Dataclass-Objekt und meldet unbekannte Schluessel als Fehler."""
    if not isinstance(data, Mapping):
        raise ConfigError(f"Abschnitt '{section}' muss ein JSON-Objekt sein.")
    data = _without_comments(data)
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
    # None heisst jeweils "Automatik am Geraet". Ein fester Wert ist aber
    # Pflicht, sobald mit THRU-Referenz gearbeitet wird: schaltet die Automatik
    # zwischen Referenz- und Verstaerkerlauf um, verschiebt das die Pegel und
    # die Differenz misst nicht mehr nur den Verstaerker.
    rbw_hz: Optional[float] = None
    vbw_hz: Optional[float] = None
    # Der zulaessige Referenzpegel haengt an der Eingangsdaempfung:
    # am Geraet nachgemessen gilt RLEV_max = INP:ATT - 10 dBm. Wer den
    # Referenzpegel anhebt, muss also auch die Daempfung mit anheben.
    ref_level_dbm: float = 10.0
    attenuation_db: Optional[float] = None
    settle_s: float = 0.2
    # Der FPC1500 beantwortet SWE:POIN? nicht (Timeout). Steht hier ein Wert,
    # entfaellt der Testsweep in FPC1500.configure(); das Geraet liefert 1183.
    sweep_points: Optional[int] = None
    # Die Signalquelle des FPC1500 heisst "TG": SOUR:TG:STAT / SOUR:TG:POW.
    # Sind die Kommandos hier eingetragen, entfaellt das Durchprobieren der
    # Kandidatenlisten aus instrument.py - das spart beim Start je einen
    # Schreib- und Fehlerabfragezyklus pro verworfenem Kandidaten.
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
            # Unter 2 Punkten laesst sich keine Frequenzachse aufspannen und
            # np.interp in der Messung haette keine Stuetzstellen.
            raise ConfigError("instrument.sweep_points muss mindestens 2 sein.")
        if self.generator_state_command and "{state}" not in self.generator_state_command:
            raise ConfigError(
                "instrument.generator_state_command braucht den Platzhalter {state}."
            )
        # Geprueft wird nur "{level" ohne schliessende Klammer: der Pegel wird
        # praktisch immer mit Formatangabe geschrieben, z.B. "{level:.2f} dBm".
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
    """Amplitudenbereich (Tracking-Generator-Pegel) und Anzahl der Stufen.

    Der Tracking-Generator des FPC1500 stellt nur -30 bis 0 dBm. Werte
    ausserhalb weist die Messung zurueck, sobald das Geraet seinen
    Pegelbereich ueberhaupt herausrueckt (siehe ``run_measurement``).
    """

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

    def run_directory(
        self, today: Optional[date] = None, suffix: str = ""
    ) -> Path:
        stamp = (today or date.today()).strftime(self.date_format)
        return Path(self.directory).expanduser() / f"{stamp}_{self.name}{suffix}"


@dataclass(frozen=True)
class MeasurementConfig:
    """Ablauf-Optionen der Messung."""

    skip_thru_prompt: bool = False
    # Die Zerstoerschwelle des FPC1500-Eingangs liegt bei +33 dBm CW. Die 30 dBm
    # hier sind die Abbruchschwelle der Messung, also bewusst mit Sicherheits-
    # abstand darunter. Hochsetzen nur, wer den Abstand freiwillig aufgibt.
    max_input_dbm: float = 30.0
    # Nennwert eines externen Abschwaechers - nur fuer Hinweistexte und die
    # Rueckfallabschaetzung. In die Messwerte geht er nicht ein: die
    # THRU-Referenz enthaelt ihn bereits gemessen.
    external_pad_db: Optional[float] = None
    # Erwartete Verstaerkung laut Datenblatt. Pflichtangabe, solange
    # auto_level gesetzt ist: daraus folgen Referenzpegel und Eichleitung des
    # Analyzers, und die muessen VOR dem ersten Sweep feststehen, weil die
    # THRU-Referenz nur fuer genau diese Einstellung gilt. Der Vorab-Test misst
    # die Verstaerkung spaeter nach, kann sie aber nicht rueckwirkend in die
    # Geraeteeinstellung einfliessen lassen.
    amplifier_gain_db: Optional[float] = None
    # Geforderter Abstand zum gemessenen Eigenrauschen. Was darunter liegt,
    # traegt vor allem Rauschen und taugt weder als Messwert noch als Referenz.
    noise_margin_db: float = 15.0
    auto_limit_levels: bool = True
    auto_level: bool = True
    gain_probe: bool = True
    gain_probe_step_db: float = 3.0
    gain_probe_points: int = 3
    thru_message: str = (
        "THRU-REFERENZMESSUNG\n\n"
        "Bitte jetzt die beiden Messkabel OHNE Verstaerker direkt\n"
        "miteinander verbinden:\n\n"
        "    Signalquelle -> Kabel 1 -> Kabel 2 -> Analyzer-Eingang\n\n"
        "Dieser Lauf nimmt die Kabelreferenz auf. Am Geraet selbst ist\n"
        "nichts einzustellen - keine Normalisierung noetig.\n\n"
        "Danach dieses Fenster schliessen; die Messung startet."
    )
    amplifier_message: str = (
        "VERSTAERKERMESSUNG\n\n"
        "Bitte jetzt den Verstaerker zwischen die beiden Messkabel\n"
        "einschleifen:\n\n"
        "    Signalquelle -> Kabel 1 -> Verstaerker -> Kabel 2 -> Analyzer\n\n"
        "Danach dieses Fenster schliessen; die Messung startet."
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
    # Bewusst nicht die 1183 des echten Geraets: so faellt im Trockenlauf auf,
    # wenn irgendwo die Punktzahl des Geraets angenommen statt abgefragt wird.
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
        pad = self.measurement.external_pad_db
        if pad is not None and pad < 0:
            raise ConfigError("measurement.external_pad_db darf nicht negativ sein.")
        if self.measurement.noise_margin_db < 0:
            raise ConfigError("measurement.noise_margin_db darf nicht negativ sein.")
        if self.measurement.gain_probe_step_db <= 0:
            raise ConfigError(
                "measurement.gain_probe_step_db muss groesser als 0 sein."
            )
        if self.measurement.gain_probe_points < 1:
            raise ConfigError(
                "measurement.gain_probe_points muss mindestens 1 sein."
            )
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
        data = _without_comments(data)
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


RESULT_FILE = "s12_measurement.csv"
THRU_SUFFIX = "_thru"


def make_output_directory(
    config: Config, today: Optional[date] = None, suffix: str = ""
) -> Path:
    """Legt das datierte Ausgabeverzeichnis an und gibt es zurueck.

    Enthaelt das Verzeichnis schon eine Messung, wird _2, _3, ... angehaengt -
    ein zweiter Lauf am selben Tag darf den ersten nicht ueberschreiben.
    """
    base = config.output.run_directory(today, suffix)
    stem = config.output.run_directory(today).name
    directory = base
    index = 2
    while (directory / RESULT_FILE).exists():
        # Der Suffix muss hinten stehen bleiben, sonst findet die
        # Referenzsuche den Ordner nicht mehr: ..._amp1_2_thru
        directory = base.parent / f"{stem}_{index}{suffix}"
        index += 1
    directory.mkdir(parents=True, exist_ok=True)
    return directory


MAX_REF_LEVEL_DBM = 30.0
"""Hoechster Referenzpegel des FPC1500 (bei voller Eichleitung)."""

MAX_ATTENUATION_DB = 40.0
"""Hoechste Eichleitungsdaempfung des FPC1500."""

MIXER_HEADROOM_DB = 10.0
"""Kopplung des Geraets: ref_level_dbm <= attenuation_db - 10 dBm."""

LEVEL_HEADROOM_DB = 3.0
"""Sicherheitsabstand zwischen erwartetem Pegel und Referenzpegel."""


def apply_auto_levels(config: Config) -> Tuple[Config, List[str]]:
    """Berechnet Referenzpegel und Eingangsdaempfung aus der Verstaerkung.

    Damit muessen nur noch die Verstaerkung laut Datenblatt und die
    Nenndaempfung des externen Abschwaechers eingetragen werden - die beiden
    gekoppelten Analyzer-Einstellungen folgen daraus.

    Wichtig: gerechnet wird mit der *konfigurierten* Verstaerkung, nicht mit
    der spaeter gemessenen. Nur so kommen THRU- und Verstaerkerlauf auf
    dieselben Geraeteeinstellungen, ohne die die Referenz nicht gilt.

    Fehlt die Verstaerkung, bricht die Funktion ab. Ein Rueckfall auf eine
    "sichere" Einstellung waere verlockend, ist aber falsch: die Einstellung
    muesste nach dem Vorab-Test korrigiert werden, und damit waere die bereits
    aufgenommene THRU-Referenz ungueltig.
    """
    if not config.measurement.auto_level:
        return config, []

    pad = config.measurement.external_pad_db or 0.0
    gain = config.measurement.amplifier_gain_db
    if gain is None:
        # Pflichtangabe - und zwar hier, nicht in Config.validate(): die
        # Auswertung laedt dieselbe Config, fasst aber das Geraet nicht an.
        raise ConfigError(
            "measurement.amplifier_gain_db fehlt. Trage die erwartete "
            "Verstaerkung laut Datenblatt in dB ein (grob reicht, +-3 dB "
            "spielen keine Rolle) - daraus werden Referenzpegel und "
            "Eichleitung des Analyzers berechnet. Beides muss vor dem ersten "
            "Sweep feststehen und fuer THRU-Referenz und Messung dasselbe "
            "sein, sonst gilt die Referenz nicht. Alternativ "
            "measurement.auto_level auf false setzen und beide Werte selbst "
            "eintragen."
        )
    expected = config.amplitude.stop_dbm + gain - pad
    ref = math.ceil((expected + LEVEL_HEADROOM_DB) / 5.0) * 5.0
    if ref > MAX_REF_LEVEL_DBM:
        # Nicht stillschweigend begrenzen: der erwartete Pegel liegt dann
        # oberhalb dessen, was das Geraet ueberhaupt messen kann - und
        # moeglicherweise ueber der Zerstoerschwelle.
        raise ConfigError(
            f"Erwartet werden {expected:+.1f} dBm am Analyzer "
            f"(Verstaerkung {gain:+.0f} dB, Abschwaecher {pad:.0f} dB). "
            f"Das Geraet kann hoechstens {MAX_REF_LEVEL_DBM:+.0f} dBm "
            "messen und geht ab +33 dBm kaputt. Externen Abschwaecher "
            "vergroessern oder amplitude.stop_dbm senken."
        )
    ref = max(ref, -30.0)
    note = (
        f"  Pegel automatisch   : erwartet {expected:+.1f} dBm am Analyzer "
        f"(Verstaerkung {gain:+.0f} dB, Abschwaecher {pad:.0f} dB)"
    )
    attenuation = min(ref + MIXER_HEADROOM_DB, MAX_ATTENUATION_DB)
    updated = replace(
        config,
        instrument=replace(
            config.instrument, ref_level_dbm=ref, attenuation_db=attenuation
        ),
    )
    lines = [
        note,
        f"                        -> Referenzpegel {ref:+.0f} dBm, "
        f"Eichleitung {attenuation:.0f} dB",
    ]
    return updated, lines


def find_latest_reference(config: Config) -> Optional[Path]:
    """Sucht die neueste THRU-Messung unterhalb von output.directory.

    So muss die Kabelreferenz nur einmal aufgenommen und danach nicht bei
    jeder Verstaerkermessung erneut angegeben werden.
    """
    root = Path(config.output.directory).expanduser()
    if not root.is_dir():
        return None
    candidates = [
        entry / RESULT_FILE
        for entry in root.iterdir()
        if entry.is_dir()
        and entry.name.endswith(THRU_SUFFIX)
        and (entry / RESULT_FILE).is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


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
