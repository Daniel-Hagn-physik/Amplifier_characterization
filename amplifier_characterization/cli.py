"""Kommandozeile: Config laden, THRU-Hinweis zeigen, messen, speichern, plotten."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import __version__
from .config import (
    THRU_SUFFIX,
    AmplitudeConfig,
    Config,
    ConfigError,
    FrequencyConfig,
    apply_auto_levels,
    describe,
    find_latest_reference,
    load_config,
    make_output_directory,
)
from .dialog import prompt_thru_calibration
from .instrument import (
    FPC1500,
    InstrumentError,
    SimulatedFPC1500,
    list_resources,
    probe_tcp,
    strip_block_header,
)
from .measurement import (
    MeasurementError,
    Overload,
    Point,
    apply_reference,
    cable_loss,
    read_csv,
    run_measurement,
    write_csv,
)
from .plotting import plot_all

DEFAULT_CONFIG = "config/default.json"

# Die drei ueblichen SCPI-Zugaenge. Beim FPC1500 antwortet in der Praxis nur
# HiSLIP auf 4880: Port 5025 ist zu und VXI-11 scheitert. Die anderen beiden
# werden trotzdem mitgeprueft - antwortet naemlich gar keiner, liegt es am
# Netz oder an der IP und nicht am Resource-String.
DIAGNOSTIC_PORTS: tuple = (
    (5025, "SCPI Raw Socket"),
    (111, "VXI-11 Portmapper"),
    (4880, "HiSLIP"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amplifier-characterization",
        description=(
            "S12-Messung (Ausgangsleistung ueber Eingangsleistung und Frequenz) "
            "mit dem Rohde & Schwarz FPC1500."
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_CONFIG,
        help=f"Pfad zur JSON-Konfiguration (Standard: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Ueberschreibt output.directory aus der Config (Wurzelverzeichnis).",
    )
    parser.add_argument(
        "--resource",
        default=None,
        help="Ueberschreibt die VISA-Adresse aus der Config.",
    )
    parser.add_argument(
        "--thru",
        action="store_true",
        help=(
            "THRU-Referenzmessung (Kabel ohne Verstaerker). Schreibt nach "
            f"<datum>_<name>{THRU_SUFFIX}. Dafuer gibt es run_thru.py."
        ),
    )
    parser.add_argument(
        "--no-reference",
        action="store_true",
        help="Ohne Kabelkorrektur messen (keine THRU-Referenz verwenden).",
    )
    parser.add_argument(
        "--rbw",
        type=float,
        default=None,
        help="Ueberschreibt instrument.rbw_hz (Aufloesebandbreite in Hz).",
    )
    parser.add_argument(
        "--vbw",
        type=float,
        default=None,
        help="Ueberschreibt instrument.vbw_hz (Videobandbreite in Hz).",
    )
    parser.add_argument(
        "--freq",
        type=float,
        nargs=3,
        metavar=("START_HZ", "STOP_HZ", "PUNKTE"),
        default=None,
        help="Ueberschreibt den Frequenzbereich, z.B. --freq 60e6 140e6 401.",
    )
    parser.add_argument(
        "--levels",
        type=float,
        nargs=3,
        metavar=("START_DBM", "STOP_DBM", "STUFEN"),
        default=None,
        help="Ueberschreibt den Pegelbereich, z.B. --levels -10 -10 1.",
    )
    parser.add_argument(
        "--attenuation",
        type=float,
        default=None,
        help="Ueberschreibt instrument.attenuation_db (Eingangsdaempfung in dB).",
    )
    parser.add_argument(
        "--ref-level",
        type=float,
        default=None,
        help="Ueberschreibt instrument.ref_level_dbm (Referenzpegel in dBm).",
    )
    parser.add_argument(
        "--reference",
        metavar="THRU_CSV",
        default=None,
        help=(
            "CSV einer THRU-Messung (Kabel ohne Verstaerker). Wird punktweise "
            "abgezogen, damit die Kabeldaempfung herausfaellt."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simuliertes Geraet statt echter Hardware (zum Testen des Ablaufs).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Nur die Verbindung zum Geraet pruefen (*IDN?) und beenden.",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Geraete-Selbsttest: alle SCPI-Kommandos und einen Sweep pruefen.",
    )
    parser.add_argument(
        "--query",
        metavar="SCPI",
        default=None,
        help=(
            "SCPI-Kommandos an das Geraet schicken, mit ; getrennt. Mit "
            "Fragezeichen wird abgefragt und die Antwort ausgegeben, ohne "
            "nur gesetzt - so lassen sich gekoppelte Grenzen ermitteln, z.B. "
            "--query \"INP:ATT 40 dB; DISP:TRAC:Y:RLEV? MAX\"."
        ),
    )
    parser.add_argument(
        "--dump-commands",
        metavar="DATEI",
        nargs="?",
        const="scpi_commands.txt",
        default=None,
        help="Befehlsliste des Geraets (SYST:HELP:HEAD?) in eine Datei schreiben.",
    )
    parser.add_argument(
        "--list-resources",
        action="store_true",
        help="Alle sichtbaren VISA-Ressourcen auflisten und beenden.",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Ueberspringt den THRU-Kalibrierungshinweis.",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="THRU-Hinweis in der Konsole statt als Fenster.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Nur CSV schreiben, keine Plots erzeugen.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    """Baut die Config mit den CLI-Overrides neu auf."""
    import dataclasses

    if args.output is not None:
        config = dataclasses.replace(
            config, output=dataclasses.replace(config.output, directory=args.output)
        )
    overrides = {}
    if args.resource is not None:
        overrides["resource"] = args.resource
    if args.rbw is not None:
        overrides["rbw_hz"] = args.rbw
    if args.vbw is not None:
        overrides["vbw_hz"] = args.vbw
    if args.attenuation is not None:
        overrides["attenuation_db"] = args.attenuation
    if args.ref_level is not None:
        overrides["ref_level_dbm"] = args.ref_level
    if overrides:
        config = dataclasses.replace(
            config, instrument=dataclasses.replace(config.instrument, **overrides)
        )
    if args.freq is not None:
        config = dataclasses.replace(
            config,
            frequency=FrequencyConfig(
                start_hz=args.freq[0], stop_hz=args.freq[1],
                points=int(args.freq[2]),
            ),
        )
    if args.levels is not None:
        config = dataclasses.replace(
            config,
            amplitude=AmplitudeConfig(
                start_dbm=args.levels[0], stop_dbm=args.levels[1],
                points=int(args.levels[2]),
            ),
        )
    config.validate()
    return config


def make_analyzer(config: Config, dry_run: bool):
    """Erzeugt echtes oder simuliertes Geraet."""
    if dry_run:
        return SimulatedFPC1500(
            gain_db=config.simulation.gain_db,
            p_sat_dbm=config.simulation.p_sat_dbm,
            rolloff_db_per_ghz=config.simulation.rolloff_db_per_ghz,
            points=config.simulation.points,
            noise_floor_dbm=config.simulation.noise_floor_dbm,
        )
    return FPC1500(config.instrument.resource, timeout_ms=config.instrument.timeout_ms)


def host_of(resource: str) -> str:
    """Zieht den Hostnamen bzw. die IP aus einer VISA-Adresse."""
    parts = [part for part in resource.split("::") if part]
    return parts[1] if len(parts) > 1 else resource


def diagnose_network(
    resource: str,
    printer: Callable[[str], None],
    probe: Optional[Callable[[str, int], bool]] = None,
) -> None:
    """Sagt, ob das Geraet ueberhaupt im Netz antwortet - und auf welchem Port."""
    probe = probe or probe_tcp
    host = host_of(resource)
    printer(f"  Netzwerktest zu {host}:")
    reachable = []
    for port, label in DIAGNOSTIC_PORTS:
        answered = probe(host, port)
        if answered:
            reachable.append(port)
        printer(
            f"    Port {port:>5} ({label}): "
            f"{'antwortet' if answered else 'keine Antwort'}"
        )
    if reachable:
        printer("  -> Das Geraet ist erreichbar, nur der Resource-String passt nicht.")
        if 5025 in reachable:
            printer(f"     Versuch: --resource TCPIP0::{host}::5025::SOCKET")
        if 4880 in reachable:
            printer(f"     Versuch: --resource TCPIP0::{host}::hislip0::INSTR")
    else:
        printer("  -> Keine Antwort von dieser Adresse.")
        printer("     Bei DHCP kann sich die IP geaendert haben: am FPC1500")
        printer("     SETUP > Instrument Setup > Network die aktuelle IP ablesen")
        printer("     und mit --resource oder in der Config eintragen.")


def show_resources(
    printer: Callable[[str], None],
    lister: Optional[Callable[[], List[str]]] = None,
) -> int:
    """Gibt aus, was das VISA-Backend an Geraeten sieht."""
    try:
        resources = (lister or list_resources)()
    except InstrumentError as exc:
        printer(f"VISA-Fehler: {exc}")
        return 1
    if not resources:
        printer("Keine VISA-Ressourcen gefunden.")
        return 1
    printer("Gefundene VISA-Ressourcen:")
    for resource in resources:
        printer(f"  {resource}")
    return 0


# Aus der langen Befehlsliste des Geraets werden nur die Zeilen zur
# Signalquelle hervorgehoben - genau an denen hing die Inbetriebnahme, weil
# der FPC1500 sie unter "TG" fuehrt und nicht unter OUTP/SOUR:POW.
INTERESTING_PREFIXES = ("SOUR", "OUTP", "GEN", "POW", "TG")


def dump_commands(
    config: Config, path: str, printer: Callable[[str], None]
) -> int:
    """Fragt die Befehlsliste des Geraets ab und legt sie als Datei ab.

    Viele R&S-Geraete beantworten ``SYST:HELP:HEAD?`` mit allen implementierten
    Kommandos - damit muss man die richtige Schreibweise nicht raten.
    """
    device = FPC1500(
        config.instrument.resource,
        timeout_ms=max(config.instrument.timeout_ms, 60000),
    )
    try:
        device.open()
        printer(f"  Geraet: {device.identify()}")
        printer("  Frage SYST:HELP:HEAD? ab (kann eine Weile dauern) ...")
        answer = strip_block_header(device.query("SYST:HELP:HEAD?"))
    except InstrumentError as exc:
        printer(f"  FEHLER: {exc}")
        printer("  Das Geraet unterstuetzt SYST:HELP:HEAD? offenbar nicht.")
        return 1
    finally:
        device.close()

    commands = [line.strip() for line in answer.replace(",", "\n").splitlines()]
    commands = [line for line in commands if line]
    target = Path(path)
    target.write_text("\n".join(commands) + "\n", encoding="utf-8")
    printer(f"  {len(commands)} Kommandos geschrieben nach {target}")
    printer("  Treffer zur Signalquelle:")
    hits = [
        command
        for command in commands
        if any(prefix in command.upper() for prefix in INTERESTING_PREFIXES)
    ]
    for command in hits[:60]:
        printer(f"    {command}")
    if not hits:
        printer("    (keine)")
    return 0


# Genau die Einstellungen, die den gemessenen Pegel verschieben, ohne dass man
# es den Kurven ansieht. Was hier nicht steht (Ausgabepfad, Amplitudenraster),
# kuerzt sich in der Differenz von Verstaerker- und Referenzmessung heraus.
COMPARED_SETTINGS = (
    ("rbw_hz", "Aufloesebandbreite"),
    ("vbw_hz", "Videobandbreite"),
    ("ref_level_dbm", "Referenzpegel"),
    ("attenuation_db", "Eingangsdaempfung"),
    ("sweep_points", "Sweep-Punkte"),
)


def compare_reference_settings(
    config: Config, reference_path: Path, printer: Callable[[str], None]
) -> bool:
    """Warnt, wenn die Referenz mit anderen Geraeteeinstellungen lief.

    Die Korrektur hebt nur das auf, was in beiden Messungen gleich war. Ein
    anderer Referenzpegel oder eine andere Bandbreite verschiebt die Pegel
    systematisch und wuerde als Verstaerkung des Verstaerkers erscheinen.
    """
    # save_run legt neben jede Messung eine Kopie der benutzten Config.
    # Fehlt sie (Messung von Hand kopiert, aelterer Lauf), wird nicht gewarnt -
    # ein fehlender Vergleich ist kein Befund.
    saved = reference_path.parent / "config_used.json"
    if not saved.is_file():
        return True
    try:
        stored = json.loads(saved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    differences = []
    for key, label in COMPARED_SETTINGS:
        old = stored.get("instrument", {}).get(key)
        new = getattr(config.instrument, key)
        if old != new:
            differences.append(f"{label}: Referenz {old}, jetzt {new}")
    old_freq = stored.get("frequency", {})
    new_freq = vars(config.frequency)
    if old_freq and old_freq != new_freq:
        differences.append(f"Frequenzraster: Referenz {old_freq}, jetzt {new_freq}")
    if not differences:
        return True
    printer("")
    printer("  ACHTUNG: Die THRU-Referenz lief mit anderen Einstellungen:")
    for entry in differences:
        printer(f"    {entry}")
    printer("  Die Kabelkorrektur ist dann nicht exakt. Referenz mit den")
    printer("  aktuellen Einstellungen neu aufnehmen: python run_thru.py")
    printer("")
    return False


def pad_note(config: Config, for_thru: bool) -> str:
    """Hinweistext zum externen Abschwaecher, falls die Config einen verlangt."""
    pad = config.measurement.external_pad_db
    if pad is None:
        return ""
    if for_thru:
        return (
            f"\n\nWICHTIG: Der {pad:.0f} dB Abschwaecher muss auch bei dieser\n"
            "Referenzmessung schon in der Strecke sitzen - nur dann kuerzt\n"
            "er sich in der spaeteren Korrektur exakt heraus."
        )
    # Der Hinweis nennt bewusst die Abbruchschwelle der Messung (+30 dBm) und
    # nicht die Zerstoerschwelle des Eingangs (+33 dBm CW) - die 3 dB Abstand
    # sind die Sicherheitsreserve und nicht zur Ausnutzung gedacht.
    return (
        f"\n\nWICHTIG: {pad:.0f} dB Abschwaecher zwischen Verstaerkerausgang\n"
        "und Analyzer-Eingang! Ohne ihn zerstoert die Ausgangsleistung des\n"
        "Verstaerkers den Eingang des FPC1500 (max. +30 dBm).\n"
        "Der Abschwaecher muss fuer die volle Ausgangsleistung des\n"
        "Verstaerkers ausgelegt sein."
    )


def resolve_reference(
    config: Config, args: argparse.Namespace, printer: Callable[[str], None]
) -> Optional[Path]:
    """Bestimmt die zu verwendende THRU-Referenz und meldet das Ergebnis."""
    if args.thru or args.no_reference:
        return None
    if args.reference:
        explicit = Path(args.reference)
        compare_reference_settings(config, explicit, printer)
        return explicit
    found = find_latest_reference(config)
    if found is None:
        printer("")
        printer("  ACHTUNG: Keine THRU-Referenz gefunden.")
        printer("  Die Kabeldaempfung wird dann NICHT herausgerechnet.")
        printer("  Zuerst einmalig mit den direkt verbundenen Kabeln:")
        printer("      python run_thru.py")
        printer("  (oder bewusst ohne Korrektur messen: --no-reference)")
        printer("")
        return None
    printer(f"  Referenz gefunden : {found}")
    compare_reference_settings(config, found, printer)
    return found


def run_query(
    config: Config, commands: str, printer: Callable[[str], None]
) -> int:
    """Schickt eine oder mehrere SCPI-Abfragen und gibt die Antworten aus.

    Gedacht, um Geraetegrenzen direkt am Geraet nachzuschlagen statt sie aus
    dem Datenblatt zu uebernehmen - SCPI beantwortet ``? MAX`` und ``? MIN``
    auf den meisten Einstellbefehlen.
    """
    device = FPC1500(
        config.instrument.resource, timeout_ms=config.instrument.timeout_ms
    )
    try:
        device.open()
        printer(f"  Geraet: {device.identify()}")
        for command in [c.strip() for c in commands.split(";") if c.strip()]:
            if "?" in command:
                printer(f"  {command}  ->  {device.query(command)}")
            else:
                # Ohne Fragezeichen antwortet das Geraet nicht - nur setzen.
                device.write(command)
                printer(f"  {command}  (gesetzt)")
    except InstrumentError as exc:
        printer(f"  FEHLER: {exc}")
        return 1
    finally:
        device.close()
    return 0


def probe_instrument(
    config: Config, dry_run: bool, printer: Callable[[str], None]
) -> int:
    """Faehrt den kompletten Messaufbau einmal durch - ohne die eigentliche Messung.

    Prueft, ob das Geraet alle verwendeten SCPI-Kommandos akzeptiert, wie viele
    Sweep-Punkte es liefert und ob ein Trace lesbar ist.
    """
    frequencies = config.frequency_grid
    analyzer = make_analyzer(config, dry_run)
    try:
        analyzer.open()
        printer(f"  Geraet            : {analyzer.identify()}")
        printer("  SCPI-Kommandos    :")
        analyzer.configure(
            config.instrument,
            float(frequencies[0]),
            float(frequencies[-1]),
            progress=lambda command: printer(f"    -> {command}"),
        )
        for entry in getattr(analyzer, "rejected", []):
            printer(f"  Abgelehnt         : {entry}")
        axis = analyzer.frequency_axis()
        printer(
            f"  Sweep-Punkte      : {axis.size} "
            f"({axis[0] / 1e6:.3f} - {axis[-1] / 1e6:.3f} MHz, "
            f"{(axis[1] - axis[0]) / 1e3:.1f} kHz Raster)"
        )
        step = lambda command: printer(f"    -> {command}")
        printer("  Signalquelle      :")
        analyzer.generator(True, progress=step)
        limits = analyzer.generator_limits()
        level = float(config.amplitude_grid[0])
        if limits is None:
            printer("    Pegelbereich: vom Geraet nicht abfragbar")
        else:
            printer(f"    Pegelbereich: {limits[0]:+.2f} ... {limits[1]:+.2f} dBm")
            level = min(max(level, limits[0]), limits[1])
        analyzer.set_generator_level(level, progress=step)
        printer(f"    ein/aus  : {analyzer.generator_state_template}")
        printer(f"    Pegel    : {analyzer.generator_level_template}")
        if limits is not None:
            outside = [
                value
                for value in config.amplitude_grid
                if value < limits[0] - 1e-9 or value > limits[1] + 1e-9
            ]
            if outside:
                printer(
                    f"  WARNUNG           : {len(outside)} der {len(config.amplitude_grid)} "
                    f"Amplitudenstufen liegen ausserhalb "
                    f"({outside[0]:+.2f} ... {outside[-1]:+.2f} dBm)"
                )
        analyzer.sweep(config.instrument.settle_s)
        trace = analyzer.read_trace()
        printer(
            f"  Trace             : {trace.size} Werte, "
            f"{trace.min():+.2f} ... {trace.max():+.2f} dBm"
        )
        analyzer.generator(False)
        errors = analyzer.check_errors()
        if errors:
            printer("  Abgelehnte Kommandos:")
            for entry in errors:
                printer(f"    {entry}")
            return 1
        printer("  Alle SCPI-Kommandos akzeptiert - bereit fuer die Messung.")
        return 0
    except InstrumentError as exc:
        printer(f"  FEHLER: {exc}")
        return 1
    finally:
        analyzer.close()


def check_connection(
    config: Config, dry_run: bool, printer: Callable[[str], None]
) -> int:
    """Fragt nur *IDN? ab - schneller Test, ob das Geraet erreichbar ist."""
    analyzer = make_analyzer(config, dry_run)
    printer(f"Verbindungstest zu {config.instrument.resource} ...")
    try:
        analyzer.open()
        printer(f"  OK - Geraet meldet: {analyzer.identify()}")
        return 0
    except InstrumentError as exc:
        printer(f"  FEHLER: {exc}")
        diagnose_network(config.instrument.resource, printer)
        return 1
    finally:
        analyzer.close()


def save_run(
    config: Config,
    points: Sequence[Point],
    directory: Path,
    make_plots: bool,
    printer: Callable[[str], None],
) -> List[Path]:
    """Schreibt CSV, Config-Kopie und optional die Plots."""
    written: List[Path] = [write_csv(points, directory / "s12_measurement.csv")]
    # Die Config-Kopie ist nicht nur Dokumentation: compare_reference_settings
    # liest sie spaeter wieder ein, um zu pruefen, ob eine THRU-Referenz mit
    # denselben Geraeteeinstellungen aufgenommen wurde.
    config_copy = directory / "config_used.json"
    config_copy.write_text(
        json.dumps(
            {
                "instrument": vars(config.instrument),
                "frequency": vars(config.frequency),
                "amplitude": vars(config.amplitude),
                "output": vars(config.output),
                "measurement": vars(config.measurement),
                "simulation": vars(config.simulation),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    written.append(config_copy)
    if make_plots:
        written.extend(plot_all(points, directory))
    for path in written:
        printer(f"  geschrieben: {path}")
    return written


def main(
    argv: Optional[Sequence[str]] = None,
    printer: Callable[[str], None] = print,
    today: Optional[date] = None,
) -> int:
    """Einstiegspunkt. Gibt 0 bei Erfolg, 1 bei erwarteten Fehlern zurueck."""
    args = build_parser().parse_args(argv)
    level_notes: List[str] = []
    try:
        config = load_config(args.config)
        # Automatik zuerst, damit --ref-level / --attenuation sie ueberstimmen.
        if args.ref_level is None and args.attenuation is None:
            config, level_notes = apply_auto_levels(config)
        config = apply_overrides(config, args)
    except ConfigError as exc:
        printer(f"Konfigurationsfehler: {exc}")
        return 1

    printer("THRU-Referenzmessung FPC1500" if args.thru else "S12-Messung FPC1500")
    for line in describe(config):
        printer("  " + line)
    for line in level_notes:
        printer(line)

    if args.list_resources:
        return show_resources(printer)

    if args.query:
        return run_query(config, args.query, printer)

    if args.dump_commands:
        return dump_commands(config, args.dump_commands, printer)

    if args.check:
        return check_connection(config, args.dry_run, printer)

    if args.probe:
        return probe_instrument(config, args.dry_run, printer)

    reference_path = resolve_reference(config, args, printer)

    if not (args.no_prompt or config.measurement.skip_thru_prompt):
        if args.thru:
            message = config.measurement.thru_message + pad_note(config, True)
        else:
            message = config.measurement.amplifier_message + pad_note(config, False)
            if reference_path is None:
                message += (
                    "\n\nACHTUNG: Keine THRU-Referenz - die Kabeldaempfung\n"
                    "wird nicht herausgerechnet. Abbrechen mit Strg+C und\n"
                    "zuerst run_thru.py ausfuehren."
                )
            else:
                message += f"\n\nKabelreferenz: {reference_path}"
        prompt_thru_calibration(
            message,
            use_gui=not args.no_gui,
            title="THRU-Referenz" if args.thru else "Verstaerkermessung",
            printer=printer,
        )

    # Die Referenz wird vor dem Verbindungsaufbau gelesen: ist sie unbrauchbar,
    # soll das auffallen, bevor jemand den Aufbau umsteckt und gemessen wird.
    reference: Optional[List[Point]] = None
    if reference_path is not None:
        try:
            reference = read_csv(reference_path)
            printer(f"  Kabeldaempfung im Mittel: {cable_loss(reference):+.2f} dB")
        except (OSError, KeyError, ValueError) as exc:
            printer(f"Referenz nicht lesbar: {reference_path} ({exc})")
            return 1

    aborted = False
    analyzer = make_analyzer(config, args.dry_run)
    try:
        analyzer.open()
        printer(f"  Geraet: {analyzer.identify()}")
        points = run_measurement(
            analyzer,
            config,
            progress=printer,
            is_thru=args.thru,
            reference=reference,
        )
    except Overload as exc:
        # Kein Abbruch mit leeren Haenden: die Stufen unterhalb der Grenze sind
        # gueltig gemessen und werden weiter unten normal gespeichert. Der
        # Rueckgabewert bleibt aber 1, damit ein Skript den Abbruch bemerkt.
        aborted = True
        points = exc.points
        printer("")
        printer(f"  {exc}")
        printer("")
        if not points:
            printer("Keine Messpunkte aufgenommen.")
            return 1
        printer("  Die bereits gemessenen Punkte werden trotzdem gespeichert.")
    except (InstrumentError, MeasurementError) as exc:
        printer(f"Messfehler: {exc}")
        return 1
    finally:
        analyzer.close()

    if reference is not None:
        try:
            points = apply_reference(points, reference)
        except MeasurementError as exc:
            printer(f"Referenzfehler: {exc}")
            return 1

    directory = make_output_directory(
        config, today, THRU_SUFFIX if args.thru else ""
    )
    save_run(config, points, directory, not args.no_plot, printer)
    if aborted:
        printer(
            f"Abgebrochen nach {len(points)} Messpunkten - gespeichert in {directory}"
        )
        return 1
    printer(f"Fertig: {len(points)} Messpunkte in {directory}")
    if args.thru:
        printer("")
        printer("Die Kabelreferenz steht. Jetzt den Verstaerker einschleifen und")
        printer("die eigentliche Messung starten:")
        printer("    python run_s12.py")
        printer("Die Referenz wird dabei automatisch gefunden und abgezogen.")
    return 0


def main_thru(
    argv: Optional[Sequence[str]] = None,
    printer: Callable[[str], None] = print,
    today: Optional[date] = None,
) -> int:
    """Einstiegspunkt fuer die THRU-Referenzmessung (run_thru.py)."""
    return main(["--thru", *(argv or [])], printer=printer, today=today)
