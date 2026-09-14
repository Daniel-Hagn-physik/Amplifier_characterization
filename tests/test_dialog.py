"""Tests fuer den THRU-Kalibrierungshinweis."""

from __future__ import annotations

import sys
import types

import pytest

from amplifier_characterization import dialog


class FakeRoot:
    def __init__(self, fail=False):
        self.withdrawn = False
        self.destroyed = False
        self.attributes_set = []
        if fail:
            raise RuntimeError("kein Display")

    def withdraw(self):
        self.withdrawn = True

    def attributes(self, *args):
        self.attributes_set.append(args)

    def destroy(self):
        self.destroyed = True


def install_fake_tkinter(monkeypatch, fail=False, shown=None):
    shown = [] if shown is None else shown
    tkinter = types.ModuleType("tkinter")
    messagebox = types.ModuleType("tkinter.messagebox")
    tkinter.Tk = lambda: FakeRoot(fail=fail)
    messagebox.showinfo = lambda title, message: shown.append((title, message))
    tkinter.messagebox = messagebox
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", messagebox)


def test_tk_dialog_shows_message(monkeypatch):
    shown = []
    install_fake_tkinter(monkeypatch, shown=shown)
    assert dialog._show_tk_dialog("Kabel durchverbinden") is True
    assert shown == [(dialog.TITLE, "Kabel durchverbinden")]


def test_tk_dialog_without_tkinter(monkeypatch):
    monkeypatch.setitem(sys.modules, "tkinter", None)
    assert dialog._show_tk_dialog("x") is False


def test_tk_dialog_without_display(monkeypatch):
    install_fake_tkinter(monkeypatch, fail=True)
    assert dialog._show_tk_dialog("x") is False


def test_prompt_returns_after_gui_dialog():
    calls = []

    def fake_dialog(message, title):
        calls.append((message, title))
        return True

    def forbidden(_prompt):  # pragma: no cover - darf nicht aufgerufen werden
        raise AssertionError("Konsole darf nicht genutzt werden")

    dialog.prompt_thru_calibration(
        "THRU bitte", dialog_func=fake_dialog, input_func=forbidden
    )
    assert calls == [("THRU bitte", dialog.TITLE)]


def test_prompt_falls_back_to_console_when_gui_fails():
    lines = []
    answered = []
    dialog.prompt_thru_calibration(
        "THRU bitte",
        dialog_func=lambda _m, _t: False,
        input_func=lambda prompt: answered.append(prompt) or "",
        printer=lines.append,
    )
    assert "THRU bitte" in lines
    assert answered


def test_prompt_skips_gui_when_disabled():
    lines = []
    dialog.prompt_thru_calibration(
        "THRU bitte",
        use_gui=False,
        dialog_func=lambda _m, _t: pytest.fail("GUI darf nicht kommen"),
        input_func=lambda _p: "",
        printer=lines.append,
    )
    assert dialog.TITLE in lines
