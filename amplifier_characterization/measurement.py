"""Ablauf der S12-Messung: Amplituden- und Frequenzraster abfahren, CSV schreiben."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
import math
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np

from .config import Config
from .instrument import InstrumentError

CSV_FIELDS = [
    "timestamp",
    "frequency_hz",
    "p_in_dbm",
    "p_out_dbm",
    "gain_db",
    "p_ref_dbm",
    "gain_corr_db",
    "p_amp_dbm",
]


class MeasurementError(RuntimeError):
    """Fehler waehrend der Messung."""


class Overload(MeasurementError):
    """Der Analyzer-Eingang haette den erlaubten Pegel ueberschritten.

    Traegt die bis dahin gemessenen Punkte mit, damit sie nicht verloren gehen.
    """

    def __init__(self, message: str, points: Sequence["Point"]):
        super().__init__(message)
        self.points: List["Point"] = list(points)


@dataclass(frozen=True)
class Point:
    """Ein Messpunkt der S12-Matrix.

    ``p_out_dbm`` ist der Rohwert am Analyzer-Eingang - also inklusive
    Kabeldaempfung und externem Abschwaecher. ``gain_db`` ist die rohe
    Differenz P_out - P_in und enthaelt dieselben Verluste.

    Erst mit THRU-Referenz werden die Zahlen aussagekraeftig:
    ``gain_corr_db`` = P_out - P_ref ist die Verstaerkung des Verstaerkers allein, und
    ``p_amp_dbm`` = P_in + gain_corr ist die Leistung am Verstaerkerausgang. Beide
    kommen ohne Nennwerte aus - die Daempfung der Strecke steckt gemessen in
    der Referenz.
    """

    timestamp: str
    frequency_hz: float
    p_in_dbm: float
    p_out_dbm: float
    gain_db: float
    p_ref_dbm: Optional[float] = None
    gain_corr_db: Optional[float] = None
    p_amp_dbm: Optional[float] = None


@dataclass(frozen=True)
class LevelLimits:
    """Obergrenze des Pegelrasters aus einer a-priori bekannten Verstaerkung.

    Die Untergrenze wird nicht vorausberechnet, sondern waehrend der Messung
    an jedem einzelnen Pegel geprueft - siehe ``run_measurement``.
    """

    noise_dbm: float
    highest_usable_dbm: Optional[float]
    kept: np.ndarray
    dropped_high: np.ndarray


@dataclass(frozen=True)
class GainProbe:
    """Ergebnis des Vorab-Tests: gemessene Verstaerkung und sichere Obergrenze."""

    levels_dbm: List[float]
    peaks_dbm: List[float]
    gains_db: List[float]
    gain_db: float
    highest_safe_dbm: float


def reference_peaks(reference: Sequence[Point]) -> Dict[float, float]:
    """Hoechster Referenzpegel je Amplitudenstufe (ueber alle Frequenzen).

    Bewusst das Maximum und nicht der Mittelwert: die Abschaetzung soll gegen
    Uebersteuerung schuetzen, und dafuer zaehlt die unguenstigste Frequenz.
    """
    peaks: Dict[float, float] = {}
    for point in reference:
        # Auf 6 Stellen runden: die Pegel kommen aus np.linspace, sind also
        # nicht bitgenau reproduzierbar und taugen roh nicht als Dict-Schluessel.
        key = round(point.p_in_dbm, 6)
        peaks[key] = max(peaks.get(key, -math.inf), point.p_out_dbm)
    return peaks


def probe_gain(
    analyzer,
    config: Config,
    levels: np.ndarray,
    reference: Sequence[Point],
    report: Callable[[str], None],
) -> GainProbe:
    """Tastet die Verstaerkung von unten her ab, bevor voll aufgedreht wird.

    Startet beim kleinsten nutzbaren Pegel - also der geringstmoeglichen
    Leistung - und steigt nur so weit, wie die bereits gemessene Verstaerkung
    es als unbedenklich ausweist. Aus dem letzten Messpunkt folgt der hoechste
    Eingangspegel, bei dem der Analyzer ``max_input_dbm`` noch einhaelt.

    Die Verstaerkung ergibt sich als Differenz zur THRU-Referenz, die
    Streckendaempfung muss also nicht bekannt sein.
    """
    peaks = reference_peaks(reference)
    ceiling = config.measurement.max_input_dbm
    step = config.measurement.gain_probe_step_db
    wanted = config.measurement.gain_probe_points

    probed: List[float] = []
    measured: List[float] = []
    gains: List[float] = []
    report("  Vorab-Test (Verstaerkung messen):")
    # Das Amplitudenraster ist meist feiner als der Testschritt - hier werden
    # deshalb Stufen uebersprungen, bis der geforderte Abstand erreicht ist.
    for level in levels:
        if probed and level < probed[-1] + step - 1e-9:
            continue
        # Ohne Referenzwert laesst sich die Verstaerkung nicht ausrechnen;
        # solche Stufen werden uebergangen statt zu raten.
        ref = peaks.get(round(float(level), 6))
        if ref is None:
            continue
        analyzer.set_generator_level(float(level))
        analyzer.sweep(config.instrument.settle_s)
        trace = np.asarray(analyzer.read_trace(), dtype=float)
        if trace.size == 0:
            raise MeasurementError("Leerer Trace im Vorab-Test.")
        peak = float(trace.max())
        gain = peak - ref
        probed.append(float(level))
        measured.append(peak)
        gains.append(gain)
        report(
            f"    P_in {level:+7.2f} dBm  ->  Analyzer {peak:+7.2f} dBm  "
            f"->  Verstaerkung {gain:+6.2f} dB"
        )
        if peak > ceiling:
            break
        if len(probed) >= wanted:
            break
        # Der naechste Schritt wuerde den Analyzer im guenstigsten Fall (kein
        # Gewinn an Kompression) schon ueber die Grenze treiben - also hier
        # aufhoeren, statt die Stufe noch zu fahren.
        if peak + step > ceiling:
            report(
                "    (weitere Stufe waere zu hoch - Test hier beendet)"
            )
            break
    if not probed:
        raise MeasurementError(
            "Vorab-Test nicht moeglich: keine der Amplitudenstufen kommt in "
            "der Referenz vor."
        )
    # Lineare Fortsetzung vom letzten Messpunkt: 1 dB mehr Eingang = 1 dB mehr
    # Ausgang. Komprimiert der Verstaerker, liegt der echte Pegel darunter -
    # die Schranke ist also konservativ.
    highest_safe = probed[-1] + (ceiling - measured[-1])
    return GainProbe(
        levels_dbm=probed,
        peaks_dbm=measured,
        gains_db=gains,
        gain_db=float(np.median(gains)),
        highest_safe_dbm=highest_safe,
    )


def measure_noise_floor(analyzer, config: Config) -> float:
    """Misst das Eigenrauschen: Generator aus, ein Sweep, hoechster Trace-Wert.

    Das Maximum statt des Mittelwerts, damit einzelne Stoerlinien im Span
    (Einstreuungen, Reste eines Traegers) nicht unbemerkt als Messsignal
    durchgehen.

    Damit haengt die Abschaetzung nicht an Datenblattwerten, sondern an den
    tatsaechlich eingestellten Bandbreiten und der Eingangsdaempfung.
    """
    analyzer.generator(False)
    analyzer.sweep(config.instrument.settle_s)
    trace = np.asarray(analyzer.read_trace(), dtype=float)
    if trace.size == 0:
        raise MeasurementError("Leerer Trace bei der Rauschmessung.")
    return float(np.max(trace))


def level_limits(levels: np.ndarray, noise_dbm: float, config: Config,
                 use_gain: bool) -> LevelLimits:
    """Begrenzt das Amplitudenraster nach oben, solange nichts gemessen ist.

    Nur wirksam, wenn ``amplifier_gain_db`` gesetzt ist - also als Rueckfall,
    wenn der Vorab-Test mangels Referenz nicht laufen kann. Liegt eine Referenz
    vor, misst das Skript die Verstaerkung selbst und diese Schaetzung wird
    ueberfluessig.
    """
    pad = config.measurement.external_pad_db or 0.0
    highest: Optional[float] = None
    gain = config.measurement.amplifier_gain_db
    if use_gain and gain is not None:
        highest = config.measurement.max_input_dbm - gain + pad
    high_mask = (
        np.zeros_like(levels, dtype=bool)
        if highest is None
        else levels > highest + 1e-9
    )
    return LevelLimits(
        noise_dbm=noise_dbm,
        highest_usable_dbm=highest,
        kept=levels[~high_mask],
        dropped_high=levels[high_mask],
    )


def limit_report(limits: LevelLimits, config: Config, total: int) -> List[str]:
    """Erklaert in Klartext, was vorab weggelassen wird und warum."""
    lines = [
        f"  Rauschgrenze gemessen : {limits.noise_dbm:+.2f} dBm (Generator aus)",
        f"  Mindestabstand        : {config.measurement.noise_margin_db:.0f} dB "
        "- Stufen darunter werden waehrend der Messung verworfen",
    ]
    if limits.highest_usable_dbm is not None:
        lines.append(
            f"  Nutzbar bis           : {limits.highest_usable_dbm:+.2f} dBm "
            f"(Verstaerkung {config.measurement.amplifier_gain_db:+.0f} dB "
            f"laut Config, Grenze {config.measurement.max_input_dbm:+.1f} dBm)"
        )
    if limits.dropped_high.size:
        lines.append(
            f"  Hinweis: {limits.dropped_high.size} von {total} Stufen entfallen "
            f"oben ({limits.dropped_high[0]:+.1f} ... "
            f"{limits.dropped_high[-1]:+.1f} dBm) - zu hoher Pegel am Analyzer."
        )
    return lines


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_measurement(
    analyzer,
    config: Config,
    progress: Optional[Callable[[str], None]] = None,
    clock: Callable[[], str] = _now,
    is_thru: bool = False,
    reference: Optional[Sequence[Point]] = None,
) -> List[Point]:
    """Faehrt pro Amplitudenstufe einen vollen Frequenzsweep und interpoliert.

    Fuer jede Stufe des Tracking-Generators wird ein kompletter Trace gelesen
    und auf das in der Config gewuenschte Frequenzraster interpoliert.
    """
    report = progress or (lambda _message: None)
    frequencies = config.frequency_grid
    levels = config.amplitude_grid

    # Reihenfolge ab hier ist bindend: erst konfigurieren (das setzt auch die
    # Sweep-Punktzahl und damit die Frequenzachse), dann bei ausgeschaltetem
    # Generator das Rauschen messen, dann das Pegelraster beschneiden und erst
    # zuletzt den Generator einschalten und messen.
    analyzer.configure(config.instrument, float(frequencies[0]), float(frequencies[-1]))
    for entry in getattr(analyzer, "rejected", []):
        report(f"  Hinweis: Geraet hat abgelehnt: {entry}")
    axis = np.asarray(analyzer.frequency_axis(), dtype=float)
    if axis.size < 2:
        raise MeasurementError("Das Geraet meldet eine unbrauchbare Frequenzachse.")

    limits = getattr(analyzer, "generator_limits", lambda: None)()
    if limits is not None:
        low, high = limits
        outside = [
            float(level)
            for level in levels
            if level < low - 1e-9 or level > high + 1e-9
        ]
        if outside:
            raise MeasurementError(
                f"{len(outside)} Amplitudenstufen liegen ausserhalb des "
                f"Generatorbereichs ({low:+.2f} ... {high:+.2f} dBm), z.B. "
                f"{outside[0]:+.2f} dBm. amplitude.start_dbm / stop_dbm anpassen."
            )

    noise = measure_noise_floor(analyzer, config)
    # Beim THRU-Lauf haengt kein Verstaerker in der Strecke - eine in der
    # Config hinterlegte Verstaerkung darf das Raster dort nicht beschneiden.
    limits = level_limits(levels, noise, config, use_gain=not is_thru)
    for line in limit_report(limits, config, levels.size):
        report(line)
    if config.measurement.auto_limit_levels:
        if limits.kept.size == 0:
            raise MeasurementError(
                "Keine Amplitudenstufe liegt unter der Obergrenze "
                f"{limits.highest_usable_dbm:+.2f} dBm. Abschwaecher, "
                "Verstaerkung oder Amplitudenbereich pruefen."
            )
        levels = limits.kept
    elif limits.dropped_high.size:
        report("  (auto_limit_levels ist aus - es wird trotzdem alles gemessen)")

    if not is_thru and reference is not None and levels.size:
        # Die Rauschgrenze wird in jedem Lauf frisch gemessen und kann um
        # Bruchteile eines dB schwanken. Damit das Raster trotzdem exakt zur
        # Referenz passt, werden nur Stufen gemessen, die es dort auch gibt.
        available = set(reference_peaks(reference))
        missing = np.array(
            [round(float(level), 6) not in available for level in levels]
        )
        if missing.any():
            report(
                f"  Hinweis: {int(missing.sum())} Stufen fehlen in der "
                f"Referenz ({levels[missing][0]:+.1f} ... "
                f"{levels[missing][-1]:+.1f} dBm) und entfallen."
            )
            levels = levels[~missing]
        if levels.size == 0:
            raise MeasurementError(
                "Keine der nutzbaren Amplitudenstufen kommt in der Referenz "
                "vor. THRU-Messung mit derselben Config wiederholen."
            )

    analyzer.generator(True)
    if (
        not is_thru
        and reference
        and config.measurement.gain_probe
        and levels.size
    ):
        probe = probe_gain(analyzer, config, levels, reference, report)
        report(f"  Gemessene Verstaerkung: {probe.gain_db:+.2f} dB")
        report(
            f"  Sicher bis            : {probe.highest_safe_dbm:+.2f} dBm "
            f"Eingangspegel (Grenze {config.measurement.max_input_dbm:+.1f} dBm)"
        )
        unsafe = levels > probe.highest_safe_dbm + 1e-9
        if unsafe.any() and config.measurement.auto_limit_levels:
            report(
                f"  Hinweis: {int(unsafe.sum())} Stufen entfallen oben "
                f"({levels[unsafe][0]:+.1f} ... {levels[unsafe][-1]:+.1f} dBm) "
                "- gemessene Verstaerkung zu hoch."
            )
            levels = levels[~unsafe]
        elif unsafe.any():
            report(
                f"  WARNUNG: {int(unsafe.sum())} Stufen liegen ueber der "
                "sicheren Grenze (auto_limit_levels ist aus)."
            )
        if levels.size == 0:
            raise MeasurementError(
                "Selbst der kleinste Eingangspegel treibt den Analyzer ueber "
                f"{config.measurement.max_input_dbm:+.1f} dBm. Externen "
                "Abschwaecher vergroessern."
            )

    points: List[Point] = []
    above_reference = False
    try:
        for index, level in enumerate(levels, start=1):
            analyzer.set_generator_level(float(level))
            analyzer.sweep(config.instrument.settle_s)
            trace = np.asarray(analyzer.read_trace(), dtype=float)
            if trace.size != axis.size:
                raise MeasurementError(
                    f"Trace-Laenge {trace.size} passt nicht zur Frequenzachse "
                    f"({axis.size} Punkte)."
                )
            peak = float(trace.max())
            limit = config.measurement.max_input_dbm
            # Abbruch statt Ueberspringen: das Raster steigt monoton an, jede
            # weitere Stufe waere also noch heisser. Die bis hierher gemessenen
            # Punkte reist die Ausnahme mit, damit sie nicht verloren gehen.
            if peak > limit:
                raise Overload(
                    f"ABBRUCH bei P_in {level:+.2f} dBm: am Analyzer-Eingang "
                    f"liegen {peak:+.2f} dBm an, erlaubt sind "
                    f"{limit:+.2f} dBm (measurement.max_input_dbm). "
                    "Hoehere Pegel werden nicht mehr gefahren. "
                    "Externen Abschwaecher vergroessern oder "
                    "amplitude.stop_dbm senken.",
                    points,
                )
            if peak < noise + config.measurement.noise_margin_db:
                # Gemessen statt geschaetzt: was nicht genug ueber dem Rauschen
                # liegt, taugt weder als Messwert noch als Referenz.
                report(
                    f"  [{index}/{len(levels)}] P_in = {level:+7.2f} dBm  ->  "
                    f"verworfen, nur {peak - noise:.1f} dB ueber dem Rauschen"
                )
                continue
            if peak > config.instrument.ref_level_dbm and not above_reference:
                above_reference = True
                report(
                    f"  WARNUNG: {peak:+.2f} dBm liegen ueber dem Referenzpegel "
                    f"({config.instrument.ref_level_dbm:+.2f} dBm). Der Analyzer "
                    "kann dann komprimieren und zu niedrig anzeigen - "
                    "Referenzpegel anheben oder extern staerker daempfen."
                )
            values = np.interp(frequencies, axis, trace)
            stamp = clock()
            for frequency, p_out in zip(frequencies, values):
                points.append(
                    Point(
                        timestamp=stamp,
                        frequency_hz=float(frequency),
                        p_in_dbm=float(level),
                        p_out_dbm=float(p_out),
                        gain_db=float(p_out) - float(level),
                    )
                )
            report(
                f"  [{index}/{len(levels)}] P_in = {level:+7.2f} dBm  ->  "
                f"P_out {values.min():+7.2f} ... {values.max():+7.2f} dBm"
            )
            # Nur nach der ersten verwertbaren Stufe: eine Vorwarnung, ob das
            # Raster ueberhaupt bis oben durchlaeuft. Linear hochgerechnet, also
            # eine Obergrenze - mit Kompression faellt der echte Pegel kleiner
            # aus. Es wird nur gewarnt, nicht abgebrochen.
            if index == 1 and len(levels) > 1:
                projected = peak + float(levels[-1]) - float(level)
                if projected > config.measurement.max_input_dbm:
                    report(
                        f"  WARNUNG: linear hochgerechnet waeren bei P_in "
                        f"{levels[-1]:+.2f} dBm etwa {projected:+.2f} dBm zu "
                        f"erwarten - Grenze ist "
                        f"{config.measurement.max_input_dbm:+.2f} dBm. "
                        "Die Messung bricht ab, sobald sie erreicht wird."
                    )
        if not points:
            raise MeasurementError(
                "Keine einzige Amplitudenstufe lag ausreichend ueber dem "
                f"Rauschen ({noise:+.2f} dBm, gefordert sind "
                f"{config.measurement.noise_margin_db:.0f} dB Abstand). "
                "Externen Abschwaecher verkleinern oder Eingangsdaempfung "
                "senken."
            )
    finally:
        try:
            analyzer.generator(False)
        except InstrumentError:  # Generator-Abschaltung darf die Daten nicht kosten
            report("  Warnung: Tracking-Generator konnte nicht abgeschaltet werden.")
    return points


def write_csv(points: Sequence[Point], path: Path | str) -> Path:
    """Schreibt die Messpunkte als CSV (eine Zeile pro Frequenz/Pegel-Kombination)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for point in points:
            writer.writerow(asdict(point))
    return path


def _optional_float(row: dict, key: str) -> Optional[float]:
    value = (row.get(key) or "").strip()
    return float(value) if value else None


def read_csv(path: Path | str) -> List[Point]:
    """Liest eine zuvor geschriebene CSV wieder ein.

    Aeltere Dateien ohne Referenzspalten werden mitgelesen. Fehlt nur
    ``p_amp_dbm``, wird es aus ``p_in_dbm + gain_corr_db`` ergaenzt - Messungen
    von vor der Einfuehrung der Spalte bleiben damit voll auswertbar.
    """
    path = Path(path)
    points: List[Point] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            gain_corr = _optional_float(row, "gain_corr_db")
            p_amp = _optional_float(row, "p_amp_dbm")
            if p_amp is None and gain_corr is not None:
                p_amp = float(row["p_in_dbm"]) + gain_corr
            points.append(
                Point(
                    timestamp=row["timestamp"],
                    frequency_hz=float(row["frequency_hz"]),
                    p_in_dbm=float(row["p_in_dbm"]),
                    p_out_dbm=float(row["p_out_dbm"]),
                    gain_db=float(row["gain_db"]),
                    p_ref_dbm=_optional_float(row, "p_ref_dbm"),
                    gain_corr_db=gain_corr,
                    p_amp_dbm=p_amp,
                )
            )
    return points


def _key(point: Point) -> tuple:
    # Gerundet, weil Frequenz- und Pegelraster aus np.linspace stammen: zwei
    # Laeufe mit derselben Config ergeben rechnerisch gleiche, aber nicht
    # bitgleiche Werte. Ohne Rundung faende die Referenzsuche nichts wieder.
    return (round(point.frequency_hz, 3), round(point.p_in_dbm, 6))


def apply_reference(
    points: Sequence[Point], reference: Sequence[Point]
) -> List[Point]:
    """Zieht eine THRU-Messung punktweise von der Verstaerkermessung ab.

    Die Kabeldaempfung und die Pegelabweichung des Generators stecken in beiden
    Messungen gleichermassen und fallen bei der Differenz heraus. Beide
    Messungen muessen auf demselben Frequenz- und Amplitudenraster liegen.
    """
    if not reference:
        raise MeasurementError("Die Referenzmessung enthaelt keine Punkte.")
    index = {_key(point): point.p_out_dbm for point in reference}
    corrected: List[Point] = []
    missing = 0
    for point in points:
        value = index.get(_key(point))
        if value is None:
            missing += 1
            continue
        gain = point.p_out_dbm - value
        corrected.append(
            replace(
                point,
                p_ref_dbm=value,
                gain_corr_db=gain,
                # Die Streckendaempfung steckt gemessen in der Referenz, also
                # folgt die Ausgangsleistung des Verstaerkers ohne jeden Nennwert.
                p_amp_dbm=point.p_in_dbm + gain,
            )
        )
    if missing:
        raise MeasurementError(
            f"{missing} von {len(points)} Messpunkten fehlen in der Referenz. "
            "Verstaerker- und THRU-Messung muessen mit derselben Config laufen."
        )
    return corrected


def has_reference(points: Sequence[Point]) -> bool:
    """True, wenn zu allen Punkten eine THRU-Referenz vorliegt.

    Dann stehen ``gain_corr_db`` und ``p_amp_dbm`` zur Verfuegung und alle
    Auswertungen beziehen sich auf die Ebene des Verstaerkers statt auf den
    Analyzer-Eingang.
    """
    return bool(points) and all(point.p_amp_dbm is not None for point in points)


def cable_loss(reference: Sequence[Point]) -> float:
    """Mittlere Einfuegedaempfung der Kabelstrecke aus einer THRU-Messung.

    Nur zur Anzeige und zur Plausibilitaetspruefung - in die Korrektur geht
    dieser Mittelwert nicht ein. Die zieht ``apply_reference`` punktweise ab
    und erfasst damit auch den Frequenzgang der Strecke.
    """
    if not reference:
        raise MeasurementError("Die Referenzmessung enthaelt keine Punkte.")
    return float(np.mean([point.p_out_dbm - point.p_in_dbm for point in reference]))


def to_matrix(points: Iterable[Point]):
    """Sortiert die Punkte in Raster zurueck: (frequenzen, pegel, p_out, gain)."""
    points = list(points)
    if not points:
        raise MeasurementError("Keine Messpunkte vorhanden.")
    frequencies = np.array(sorted({p.frequency_hz for p in points}))
    levels = np.array(sorted({p.p_in_dbm for p in points}))
    # Mit NaN vorbelegt: fehlt eine Kombination aus Pegel und Frequenz - etwa
    # weil eine Stufe im Rauschen verworfen oder die Messung abgebrochen wurde -
    # bleibt die Luecke als NaN stehen und wird von matplotlib ausgelassen,
    # statt als 0 dBm eine Messung vorzutaeuschen.
    p_out = np.full((levels.size, frequencies.size), np.nan)
    freq_index = {value: i for i, value in enumerate(frequencies)}
    level_index = {value: i for i, value in enumerate(levels)}
    gain = np.full_like(p_out, np.nan)
    corrected = has_reference(points)
    for point in points:
        row = level_index[point.p_in_dbm]
        column = freq_index[point.frequency_hz]
        p_out[row, column] = (
            point.p_amp_dbm if corrected else point.p_out_dbm
        )
        if corrected:
            gain[row, column] = point.gain_corr_db
    if not corrected:
        # Ohne Referenz bleibt nur die rohe Differenz zum Generatorpegel - sie
        # enthaelt noch die Daempfung von Kabeln und Abschwaecher.
        gain = p_out - levels[:, None]
    return frequencies, levels, p_out, gain
