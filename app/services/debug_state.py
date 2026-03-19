from __future__ import annotations

from contextvars import ContextVar

_debug_notes_var: ContextVar[list[str]] = ContextVar("_debug_notes_var", default=[])


def reset_debug_notes() -> None:
    _debug_notes_var.set([])


def add_debug_note(note: str) -> None:
    notes = list(_debug_notes_var.get())
    notes.append(note)
    _debug_notes_var.set(notes)


def get_debug_notes() -> list[str]:
    return list(_debug_notes_var.get())
