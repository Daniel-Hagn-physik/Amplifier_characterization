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
