#!/usr/bin/env python3
"""Verifica la regola di "quando avvisare" (MIN_DROP_PERCENT) in tracker/controllo.py.

Il caso che protegge davvero: con MIN_DROP_PERCENT=0 (chi vuole "avvisami su
qualsiasi calo reale") un prezzo INVARIATO non deve far scattare l'avviso —
`calo >= soglia` da sola è vera anche con calo 0 e soglia 0, e il bot avviserebbe
a ogni giro di controllo. Il contrario di "niente spam" (vedi README).
"""

import asyncio
import os
import tempfile
from pathlib import Path

from tracker import console_utf8, controllo, db
from tracker.scraper import Rilevazione

console_utf8()


class ScraperFinto:
    """Ritorna sempre lo stesso prezzo per ogni url, senza toccare la rete."""

    def __init__(self, prezzo):
        self.prezzo = prezzo

    async def leggi_molti(self, urls):
        return [Rilevazione(self.prezzo, "Prodotto di prova", "CHF") for _ in urls]


async def _controlla(prezzo_iniziale, prezzo_nuovo, target=None, soglia=1.0):
    """Un prodotto in un DB temporaneo, un giro di controllo finto, l'esito che ne esce."""
    with tempfile.TemporaryDirectory() as tmp:
        vecchio_path = db.DB_PATH
        db.DB_PATH = Path(tmp) / "test.db"
        conn = None
        try:
            conn = db.connect()
            pid = db.aggiungi(conn, "http://esempio.test/p", "Prodotto di prova",
                              "esempio", "CHF", target)
            db.registra_prezzo(conn, pid, prezzo_iniziale, "Prodotto di prova")
            esiti = await controllo.controlla(conn, soglia, scraper=ScraperFinto(prezzo_nuovo))
            return esiti[0]
        finally:
            if conn is not None:
                conn.close()
            db.DB_PATH = vecchio_path


async def main():
    ok = True

    # calo sopra soglia -> avvisa
    esito = await _controlla(100, 90, soglia=5.0)
    passa = esito.avvisa
    print(f"  calo sopra soglia (100 -> 90, soglia 5%)        -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # calo sotto soglia -> non avvisa
    esito = await _controlla(100, 98, soglia=5.0)
    passa = not esito.avvisa
    print(f"  calo sotto soglia (100 -> 98, soglia 5%)        -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # il bug: prezzo invariato con soglia 0 non deve avvisare
    esito = await _controlla(100, 100, soglia=0.0)
    passa = not esito.avvisa
    print(f"  prezzo invariato, soglia 0% -> non avvisa       -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # un aumento di prezzo non è un calo, non deve avvisare nemmeno a soglia 0
    esito = await _controlla(100, 110, soglia=0.0)
    passa = not esito.avvisa
    print(f"  aumento di prezzo, soglia 0% -> non avvisa      -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # MIN_DROP_PERCENT malformato -> fallback al default, nessuna eccezione
    os.environ["MIN_DROP_PERCENT"] = "non-un-numero"
    try:
        valore = controllo.soglia_minima()
        passa = valore == controllo.SOGLIA_PREDEFINITA
    except Exception as e:  # noqa: BLE001 — non deve mai arrivare qui
        passa = False
        print(f"    eccezione inattesa: {e}")
    print(f"  MIN_DROP_PERCENT malformato -> fallback default -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # MIN_DROP_PERCENT negativo -> non ha senso, fallback al default
    os.environ["MIN_DROP_PERCENT"] = "-5"
    valore = controllo.soglia_minima()
    passa = valore == controllo.SOGLIA_PREDEFINITA
    print(f"  MIN_DROP_PERCENT negativo -> fallback default   -> {'OK' if passa else 'FALLITO'}")
    ok &= passa
    del os.environ["MIN_DROP_PERCENT"]

    # sotto il prezzo target: avvisa anche se il calo è sotto soglia
    esito = await _controlla(100, 99, target=99.5, soglia=50.0)
    passa = esito.avvisa
    print(f"  sotto il target, calo sotto soglia -> avvisa    -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    print("\n" + ("TUTTI I CONTROLLI OK" if ok else "QUALCOSA NON VA"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
