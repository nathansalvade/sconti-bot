"""Pacchetto tracker — moduli condivisi da bot, CLI e test."""

import sys


def console_utf8():
    """Forza stdout/stderr a UTF-8.

    Il progetto è pieno di accenti italiani e qualche emoji (messaggi
    Telegram, log). Su Windows la console usa di default una codifica legacy
    (es. cp1252) che non li rappresenta: il primo carattere non mappabile fa
    esplodere `print`/`logging` con UnicodeEncodeError. Va chiamata il prima
    possibile in ogni entrypoint, prima di qualunque stampa o configurazione
    del logging — altrimenti l'handler cattura lo stream con la codifica
    vecchia.

    `reconfigure` esiste solo su `io.TextIOWrapper`: se stdout/stderr sono
    stati sostituiti (pipe, redirect, subprocess, cattura dell'output) può
    mancare o fallire. Non è un problema bloccante: in quel caso si prosegue
    con la codifica che c'è, invece di far fallire l'avvio del programma.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, ValueError, OSError):
                pass
