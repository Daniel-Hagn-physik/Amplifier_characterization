"""PyVISA-Anbindung an den Rohde & Schwarz FPC1500 inklusive Tracking-Generator."""

from __future__ import annotations

import socket
import time
from typing import Any, Callable, List, Optional, Sequence, Tuple

import numpy as np

from .config import InstrumentConfig


class InstrumentError(RuntimeError):
    """Fehler beim Ansprechen des Spectrum Analyzers."""


def open_resource_manager(visa_library: str = "") -> Any:
    """Erzeugt einen PyVISA-ResourceManager (separat gehalten, um testbar zu sein)."""
    try:
        import pyvisa
    except ImportError as exc:  # pragma: no cover - defensive, pyvisa ist Pflicht
        raise InstrumentError("PyVISA ist nicht installiert (pip install pyvisa).") from exc
    try:
        return pyvisa.ResourceManager(visa_library)
    except Exception as exc:
        raise InstrumentError(f"VISA-Backend nicht verfuegbar: {exc}") from exc


def list_resources(resource_manager: Optional[Any] = None) -> List[str]:
    """Listet alle VISA-Ressourcen, die das Backend aktuell sieht."""
    manager = resource_manager or open_resource_manager()
    try:
        return [str(entry) for entry in manager.list_resources()]
    except Exception as exc:
        raise InstrumentError(f"Ressourcenliste nicht abrufbar: {exc}") from exc


def probe_tcp(host: str, port: int, timeout_s: float = 2.0) -> bool:
    """Prueft, ob auf host:port ueberhaupt etwas antwortet."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


# Die ersten Eintraege sind durch SYST:HELP:HEAD? des FPC1500 bestaetigt,
# der Rest deckt verwandte Geraete ab.
#
# Wichtig fuer den FPC1500: seine Signalquelle heisst durchgaengig "TG"
# (SOUR:TG:...). Das aus anderen Geraetefamilien gewohnte OUTP:STAT bzw.
# SOUR:POW kennt er nicht - die stehen hier nur am Ende als Rueckfall.
GENERATOR_STATE_CANDIDATES = (
    "SOUR:TG:STAT {state}",
    "SOUR:TG {state}",
    "SOUR:GEN:STAT {state}",
    "SOUR:STAT {state}",
    "OUTP:STAT {state}",
)

# Dieselbe Funktion in mehreren Schreibweisen: manche Geraete verlangen die
# Einheit, andere lehnen sie ab, und einige nehmen keine Nachkommastellen.
# Deshalb dieselbe Kommandowurzel mehrfach mit unterschiedlicher Formatierung.
GENERATOR_LEVEL_CANDIDATES = (
    "SOUR:TG:POW {level:.2f} dBm",
    "SOUR:TG:POW {level:.2f}",
    "SOUR:TG:POW {level:.0f} dBm",
    "SOUR:TG:POW {level:.0f}",
    "SOUR:GEN:POW {level:.2f} dBm",
    "SOUR:POW {level:.2f} dBm",
)

# Clear/Write statt Mittelung oder Max-Hold erzwingen, damit jeder Sweep fuer
# sich steht und kein Rest des vorherigen Pegels im Trace haengt. Das am
# Geraet tatsaechlich akzeptierte Kommando ist DISP:TRAC1:MODE WRIT; das
# naheliegende TRAC1:MODE kennt der FPC1500 nicht - daher die Reihenfolge.
TRACE_MODE_CANDIDATES = (
    "DISP:TRAC1:MODE WRIT",
    "DISP:TRAC:MODE WRIT",
    "DISP:WIND:TRAC1:MODE WRIT",
    "TRAC1:MODE WRIT",
)


class FPC1500:
    """Duenner SCPI-Wrapper um den FPC1500.

    Die Messroutine erwartet von einem Geraet genau diese Schnittstelle:
    ``open``, ``close``, ``identify``, ``configure``, ``frequency_axis``,
    ``generator``, ``set_generator_level``, ``sweep`` und ``read_trace``.

    Die Frequenzachse des Traces wird aus Start/Stopp und der Anzahl der
    Sweep-Punkte des Geraets berechnet; die Messwerte kommen in dBm.
    """

    def __init__(
        self,
        resource: str,
        timeout_ms: int = 20000,
        resource_manager: Optional[Any] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.resource = resource
        self.timeout_ms = timeout_ms
        self._rm = resource_manager
        self._sleep = sleep
        self._instrument: Optional[Any] = None
        self._start_hz = 0.0
        self._stop_hz = 0.0
        self._points = 0
        self._error_queue_ok = True
        self.rejected: List[str] = []
        self.generator_state_template: Optional[str] = None
        self.generator_level_template: Optional[str] = None
        self._last_rejections: List[str] = []

    # -- Verbindung ----------------------------------------------------
    def open(self) -> "FPC1500":
        if self._instrument is not None:
            return self
        if self._rm is None:
            self._rm = open_resource_manager()
        try:
            self._instrument = self._rm.open_resource(self.resource)
        except Exception as exc:
            raise InstrumentError(
                f"Verbindung zu {self.resource} fehlgeschlagen: {exc}"
            ) from exc
        self._instrument.timeout = self.timeout_ms
        if "SOCKET" in self.resource.upper():
            # Raw-Socket-Verbindungen brauchen ein explizites Zeilenende.
            # Beim FPC1500 kommt dieser Zweig nicht zum Tragen: er spricht nur
            # HiSLIP, wo VISA die Terminierung selbst regelt.
            self._instrument.read_termination = "\n"
            self._instrument.write_termination = "\n"
        return self

    def close(self) -> None:
        if self._instrument is not None:
            self._instrument.close()
            self._instrument = None

    def __enter__(self) -> "FPC1500":
        return self.open()

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # -- SCPI-Primitive ------------------------------------------------
    @property
    def session(self) -> Any:
        if self._instrument is None:
            raise InstrumentError("Keine offene VISA-Verbindung (open() aufrufen).")
        return self._instrument

    def write(self, command: str) -> None:
        try:
            self.session.write(command)
        except Exception as exc:
            raise InstrumentError(f"Schreiben fehlgeschlagen ('{command}'): {exc}") from exc

    def query(self, command: str) -> str:
        try:
            return str(self.session.query(command)).strip()
        except Exception as exc:
            raise InstrumentError(f"Abfrage fehlgeschlagen ('{command}'): {exc}") from exc

    def identify(self) -> str:
        return self.query("*IDN?")

    def wait(self) -> None:
        """Blockiert, bis das Geraet alle vorherigen Kommandos beendet hat."""
        self.query("*OPC?")

    def check_errors(self, limit: int = 20) -> List[str]:
        """Liest die SCPI-Fehlerwarteschlange leer und gibt die Eintraege zurueck.

        Ein abgelehnter Befehl loest ueber VISA keine Exception aus - er landet
        nur hier. Ohne diese Abfrage wuerde eine Messung mit falschen
        Einstellungen stillschweigend weiterlaufen.
        """
        errors: List[str] = []
        for _ in range(limit):
            answer = self.query("SYST:ERR?")
            code = answer.split(",")[0].strip().lstrip("+")
            if code == "0":
                break
            errors.append(answer)
        return errors

    # -- Konfiguration -------------------------------------------------
    def _pending_errors(self, report: Callable[[str], None]) -> List[str]:
        """Fehlerwarteschlange lesen; ein stummes Geraet schaltet die Pruefung ab."""
        if not self._error_queue_ok:
            return []
        try:
            return self.check_errors()
        except InstrumentError:
            self._error_queue_ok = False
            report("   SYST:ERR? wird nicht beantwortet - Fehlerpruefung aus")
            return []

    def _first_accepted(
        self,
        templates: Sequence[str],
        values: dict,
        report: Callable[[str], None],
    ) -> Optional[str]:
        """Sendet Varianten desselben Befehls, bis eine akzeptiert wird.

        Gibt die Vorlage zurueck, die durchgegangen ist - Geraete derselben
        Familie benennen dieselbe Funktion unterschiedlich.

        Das Durchprobieren geht nur, weil ein unbekanntes Kommando beim FPC1500
        folgenlos in der Fehlerwarteschlange landet, statt eine Exception
        auszuloesen. Abgelehnte Varianten aendern also nichts am Geraet.
        """
        self._last_rejections = []
        for template in templates:
            command = template.format(**values)
            report(command)
            self.write(command)
            errors = self._pending_errors(report)
            if not errors:
                return template
            report(f"   abgelehnt: {' | '.join(errors)}")
            self._last_rejections.append(f"{command} -> {' | '.join(errors)}")
        return None

    def configure(
        self,
        config: InstrumentConfig,
        start_hz: float,
        stop_hz: float,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Setzt Betriebsart, Frequenzbereich, Filter und Pegelbezug.

        Nach jedem Kommando wird die SCPI-Fehlerwarteschlange geprueft. Nur
        Frequenzbereich und Single-Sweep sind zwingend; alles andere wird bei
        Ablehnung in ``rejected`` vermerkt und gemeldet, statt die Messung
        abzubrechen. ``progress`` bekommt jedes Kommando gemeldet.
        """
        report = progress or (lambda _command: None)
        self.rejected = []
        self._error_queue_ok = True
        self.generator_state_template = config.generator_state_command
        self.generator_level_template = config.generator_level_command
        report("*CLS")
        self.write("*CLS")

        def send(command: str, essential: bool = False) -> bool:
            report(command)
            self.write(command)
            errors = self._pending_errors(report)
            if not errors:
                return True
            joined = " | ".join(errors)
            if essential:
                raise InstrumentError(f"Kommando abgelehnt: {command} -> {joined}")
            self.rejected.append(f"{command} -> {joined}")
            report(f"   abgelehnt: {joined}")
            return False

        send("INST:SEL SAN")
        send(f"FREQ:STAR {start_hz:.6f} Hz", essential=True)
        send(f"FREQ:STOP {stop_hz:.6f} Hz", essential=True)
        # Daempfung VOR dem Referenzpegel: der zulaessige Pegelbereich haengt
        # von der eingestellten Eingangsdaempfung ab. Am Geraet nachgemessen
        # gilt RLEV_max = INP:ATT - 10 dBm. In umgekehrter Reihenfolge wuerde
        # ein hoher Referenzpegel abgelehnt und das Geraet bliebe still auf
        # dem alten Wert stehen.
        if config.attenuation_db is None:
            send("INP:ATT:AUTO ON")
        else:
            send(f"INP:ATT {config.attenuation_db:.0f} dB")
        send(f"DISP:TRAC:Y:RLEV {config.ref_level_dbm:.2f} dBm")
        # Zur Wahl der Aufloesebandbreite: bei 100 kHz zeigt der FPC1500 ueber
        # den Span einen symmetrischen Bogen von rund 3 dB, der sich mit dem
        # Span mitdehnt - ein Fehler des Trackings, kein Effekt des Pruef-
        # lings. Bei 300 kHz sind davon nur noch 0.4 dB uebrig, deshalb ist
        # das der voreingestellte Wert in den Configs.
        if config.rbw_hz is None:
            send("BAND:AUTO ON")
        else:
            send(f"BAND {config.rbw_hz:.3f} Hz")
        if config.vbw_hz is None:
            send("BAND:VID:AUTO ON")
        else:
            send(f"BAND:VID {config.vbw_hz:.3f} Hz")
        # RMS-Detektor: mittelt die Leistung ueber das Pixelintervall, statt
        # den Spitzenwert zu nehmen. Fuer eine Leistungsmessung ist das der
        # richtige Detektor, und er macht die Pegel unabhaengiger davon, wie
        # viele Sweep-Punkte auf den Traeger fallen.
        send("DET RMS")
        # Signalquelle soll der Sweep-Frequenz folgen (Tracking) und nicht auf
        # einer festen CW-Frequenz stehen, falls das Geraet so hinterlassen wurde.
        send("SOUR:TG:FREQ:AUTO ON")
        if self._first_accepted(TRACE_MODE_CANDIDATES, {}, report) is None:
            self.rejected.append(
                "Trace-Modus: kein Kommando akzeptiert (Clear/Write ist Voreinstellung)"
            )
            report("   Trace-Modus nicht setzbar - Voreinstellung wird genutzt")
        send("FORM:DATA ASC")
        # Freilaufender Sweep ist nicht brauchbar: erst im Single-Sweep-Modus
        # gehoert der gelesene Trace sicher zum zuletzt gesetzten Pegel.
        # Deshalb essential - ohne das waeren alle Messwerte fragwuerdig.
        send("INIT:CONT OFF", essential=True)
        report("*OPC?")
        self.query("*OPC?")
        self._start_hz = float(start_hz)
        self._stop_hz = float(stop_hz)

        # Der FPC beantwortet SWE:POIN? nicht - die Punktzahl kommt deshalb
        # entweder aus der Config oder aus einem echten Testsweep.
        if config.sweep_points is not None:
            self._points = int(config.sweep_points)
        else:
            report("Testsweep zur Bestimmung der Sweep-Punkte")
            self.sweep(config.settle_s)
            self._points = int(self.read_trace().size)
        if self._points < 2:
            raise InstrumentError(f"Unplausible Anzahl Sweep-Punkte: {self._points}")

    def frequency_axis(self) -> np.ndarray:
        """Frequenzachse des zuletzt konfigurierten Sweeps.

        Das Geraet liefert zum Trace keine Frequenzen mit, nur die Pegelwerte.
        Die Achse wird deshalb aus Start, Stopp und Punktzahl rekonstruiert -
        und ist nur gueltig, solange nach ``configure`` niemand den
        Frequenzbereich am Geraet verstellt hat.
        """
        if self._points < 2:
            raise InstrumentError("Frequenzachse unbekannt - configure() zuerst aufrufen.")
        return np.linspace(self._start_hz, self._stop_hz, self._points)

    # -- Tracking-Generator --------------------------------------------
    def generator(
        self, on: bool, progress: Optional[Callable[[str], None]] = None
    ) -> None:
        """Schaltet die Signalquelle (Tracking-Generator) ein oder aus."""
        report = progress or (lambda _command: None)
        state = "ON" if on else "OFF"
        if self.generator_state_template is None:
            self.generator_state_template = self._first_accepted(
                GENERATOR_STATE_CANDIDATES, {"state": state}, report
            )
            if self.generator_state_template is None:
                raise InstrumentError(
                    "Kein akzeptiertes Kommando zum Schalten der Signalquelle gefunden: "
                    + " | ".join(self._last_rejections)
                )
            return
        self.write(self.generator_state_template.format(state=state))

    def generator_limits(self) -> Optional[Tuple[float, float]]:
        """Fragt den zulaessigen Pegelbereich der Signalquelle ab.

        SCPI erlaubt ``? MIN`` / ``? MAX`` auf Einstellbefehle. Beantwortet das
        Geraet das nicht, gibt die Methode None zurueck.

        Lieber am Geraet nachfragen als aus dem Datenblatt uebernehmen: der
        Bereich haengt an Optionen und Firmware. Beim FPC1500 sind es -30
        bis 0 dBm. None bedeutet nur "nicht abfragbar" - dann faellt diese
        Pruefung weg, die Pegel werden trotzdem gefahren.
        """
        template = self.generator_level_template or GENERATOR_LEVEL_CANDIDATES[0]
        header = template.split("{")[0].strip()
        try:
            low = float(self.query(f"{header}? MIN"))
            high = float(self.query(f"{header}? MAX"))
        except (InstrumentError, ValueError):
            return None
        return (low, high) if low <= high else (high, low)

    def set_generator_level(
        self, level_dbm: float, progress: Optional[Callable[[str], None]] = None
    ) -> None:
        """Setzt den Ausgangspegel der Signalquelle in dBm."""
        report = progress or (lambda _command: None)
        if self.generator_level_template is None:
            self.generator_level_template = self._first_accepted(
                GENERATOR_LEVEL_CANDIDATES, {"level": float(level_dbm)}, report
            )
            if self.generator_level_template is None:
                raise InstrumentError(
                    "Kein akzeptiertes Kommando fuer den Generatorpegel gefunden: "
                    + " | ".join(self._last_rejections)
                )
            return
        self.write(self.generator_level_template.format(level=float(level_dbm)))

    # -- Messung --------------------------------------------------------
    def sweep(self, settle_s: float = 0.0) -> None:
        # Die Wartezeit liegt VOR dem Sweep: sie gibt dem Generator und den
        # Filtern Zeit, dem gerade gesetzten Pegel zu folgen. Nach dem Sweep
        # zu warten wuerde nichts nuetzen - der Trace waere schon verfaelscht.
        if settle_s > 0:
            self._sleep(settle_s)
        self.write("INIT:IMM")
        # *OPC? blockiert bis zum Ende des Sweeps. Ohne das wuerde read_trace
        # einen halb aktualisierten Trace lesen.
        self.wait()

    def read_trace(self) -> np.ndarray:
        raw = self.query("TRAC:DATA? TRACE1")
        values: List[float] = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                values.append(float(item))
            except ValueError as exc:
                raise InstrumentError(f"Unlesbarer Trace-Wert: '{item}'") from exc
        if not values:
            raise InstrumentError("Das Geraet hat einen leeren Trace geliefert.")
        return np.asarray(values, dtype=float)


def strip_block_header(answer: str) -> str:
    """Entfernt den SCPI-Blockheader (#<n><laenge>) aus einer Antwort.

    Lange Antworten wie die Befehlsliste aus SYST:HELP:HEAD? kommen als
    SCPI-Blockdaten: '#' , eine Ziffer n fuer die Laenge der Laengenangabe,
    dann n Ziffern Laenge, dann die Nutzdaten. '#0' ist der Sonderfall
    "Laenge unbekannt" und hat nur die zwei Kopfzeichen.
    """
    if not answer.startswith("#"):
        return answer
    digits = answer[1:2]
    if not digits.isdigit() or digits == "0":
        return answer[2:]
    offset = 2 + int(digits)
    return answer[offset:]


class SimulatedFPC1500:
    """Ersatzgeraet fuer Trockenlaeufe ohne Hardware (--dry-run).

    Modelliert einen Verstaerker mit Kleinsignalverstaerkung, 1-dB-Kompression
    und leichtem Abfall der Verstaerkung zu hohen Frequenzen.
    """

    def __init__(
        self,
        gain_db: float = 20.0,
        p_sat_dbm: float = 10.0,
        rolloff_db_per_ghz: float = 2.0,
        points: int = 601,
        noise_floor_dbm: float = -90.0,
    ) -> None:
        self.gain_db = gain_db
        self.p_sat_dbm = p_sat_dbm
        self.rolloff_db_per_ghz = rolloff_db_per_ghz
        self.points = points
        self.noise_floor_dbm = noise_floor_dbm
        self._start_hz = 0.0
        self._stop_hz = 0.0
        self._level_dbm = -30.0
        self._on = False
        self.closed = False
        self.rejected: List[str] = []
        self.generator_state_template = "Simulation"
        self.generator_level_template = "Simulation"

    def open(self) -> "SimulatedFPC1500":
        return self

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "SimulatedFPC1500":
        return self.open()

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def identify(self) -> str:
        return "Simulation,FPC1500,0,0.0"

    def check_errors(self, limit: int = 20) -> List[str]:
        del limit
        return []

    def generator_limits(self) -> Optional[Tuple[float, float]]:
        return None

    def configure(
        self,
        config: InstrumentConfig,
        start_hz: float,
        stop_hz: float,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        del config
        self.rejected: List[str] = []
        if progress is not None:
            progress("Simulation: keine SCPI-Kommandos")
        self._start_hz = float(start_hz)
        self._stop_hz = float(stop_hz)

    def frequency_axis(self) -> np.ndarray:
        return np.linspace(self._start_hz, self._stop_hz, self.points)

    def generator(
        self, on: bool, progress: Optional[Callable[[str], None]] = None
    ) -> None:
        del progress
        self._on = bool(on)

    def set_generator_level(
        self, level_dbm: float, progress: Optional[Callable[[str], None]] = None
    ) -> None:
        del progress
        self._level_dbm = float(level_dbm)

    def sweep(self, settle_s: float = 0.0) -> None:
        del settle_s

    def read_trace(self) -> np.ndarray:
        axis = self.frequency_axis()
        if not self._on:
            return np.full_like(axis, self.noise_floor_dbm)
        gain = self.gain_db - self.rolloff_db_per_ghz * (axis / 1e9)
        linear_out = self._level_dbm + gain
        # weiche Saettigung Richtung p_sat_dbm: logaddexp biegt die Gerade
        # stetig in die Saettigung um, statt sie hart abzuschneiden. So hat
        # der Trockenlauf einen echten Kompressionsbereich, an dem sich die
        # Auswertung (P1dB-Suche) ueberhaupt erst zeigen kann.
        compressed = self.p_sat_dbm - np.logaddexp(0.0, self.p_sat_dbm - linear_out)
        return np.maximum(compressed, self.noise_floor_dbm)
