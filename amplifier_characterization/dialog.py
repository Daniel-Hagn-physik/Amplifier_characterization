"""Hinweisdialog zur THRU-Kalibrierung vor dem Start der Messung."""

from __future__ import annotations

from typing import Callable, Optional

TITLE = "THRU-Kalibrierung"


def _show_tk_dialog(message: str, title: str = TITLE) -> bool:
    """Zeigt eine wegklickbare Messagebox. Gibt False zurueck, wenn kein GUI da ist."""
    try:
        import tkinter
        from tkinter import messagebox
    except Exception:
        return False
    try:
        root = tkinter.Tk()
        # withdraw blendet das leere Hauptfenster aus, topmost holt die Box
        # nach vorn - sonst versteckt sie sich hinter dem Terminal und die
        # Messung scheint zu haengen, waehrend sie auf den Klick wartet.
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, message)
        root.destroy()
        return True
    except Exception:
        return False


def prompt_thru_calibration(
    message: str,
    use_gui: bool = True,
    title: str = TITLE,
    input_func: Callable[[str], str] = input,
    printer: Callable[[str], None] = print,
    dialog_func: Optional[Callable[[str, str], bool]] = None,
) -> None:
    """Fordert zur THRU-Kalibrierung auf und wartet auf das Wegklicken.

    Bevorzugt ein Tk-Fenster; ohne GUI (z.B. Remote-Shell) wird auf eine
    Konsolenabfrage zurueckgefallen.

    Der Aufruf blockiert mit Absicht: erst wenn am Aufbau umgesteckt und
    bestaetigt wurde, darf die Messung weiterlaufen.
    """
    dialog = dialog_func or _show_tk_dialog
    if use_gui and dialog(message, title):
        return
    printer("")
    printer("=" * 70)
    printer(title)
    printer(message)
    printer("=" * 70)
    input_func("Mit ENTER fortfahren ... ")


def _tk_open_csv(initial_dir: str, title: str) -> Optional[str]:
    """Dateiauswahl-Dialog. Gibt None zurueck, wenn kein GUI da ist."""
    try:
        import tkinter
        from tkinter import filedialog
    except Exception:
        return None
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(
            title=title,
            initialdir=initial_dir,
            filetypes=[("Messdaten (CSV)", "*.csv"), ("Alle Dateien", "*.*")],
        )
        root.destroy()
        return selected or ""
    except Exception:
        return None


def ask_for_csv(
    initial_dir: str = ".",
    title: str = "Messdatei auswaehlen",
    chooser: Optional[Callable[[str, str], Optional[str]]] = None,
    input_func: Callable[[str], str] = input,
    printer: Callable[[str], None] = print,
) -> Optional[str]:
    """Laesst den Nutzer eine CSV auswaehlen.

    Bevorzugt ein Dateiauswahl-Fenster; ohne GUI wird nach dem Pfad gefragt.
    Gibt None zurueck, wenn der Nutzer abbricht.
    """
    selected = (chooser or _tk_open_csv)(initial_dir, title)
    if selected is not None:
        return selected or None
    printer(f"{title} (Verzeichnis: {initial_dir})")
    # Pfade aus dem Dateimanager kommen oft in Anfuehrungszeichen - die
    # wuerden sonst Teil des Dateinamens.
    answer = input_func("Pfad zur CSV (leer = abbrechen): ").strip().strip('"')
    return answer or None
