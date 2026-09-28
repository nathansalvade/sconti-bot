#!/usr/bin/env python3
"""Verifica db.imposta_target: il comando /target che cambia il prezzo obiettivo.

Il caso che protegge davvero è l'ultimo: `controllo.controlla` sopprime un avviso
quando il prezzo è >= a quello già annunciato (`gia_visto`). Se imposti un
obiettivo SOPRA il prezzo attuale di un prodotto già notificato, `sotto_target`
è vera ma `gia_visto` la annullerebbe, e il nuovo obiettivo non scatterebbe mai.
Per questo `imposta_target` azzera `prezzo_avvisato`: senza quell'azzeramento il
test "obiettivo sopra il prezzo su prodotto già avvisato" fallisce.
"""

import asyncio
import tempfile
from pathlib import Path

from tracker import console_utf8, controllo, db
from tracker.scraper import Rilevazione

console_utf8()

URL = "http://esempio.test/p"


class ScraperFinto:
    """Ritorna sempre lo stesso prezzo per ogni url, senza toccare la rete."""

    def __init__(self, prezzo):
        self.prezzo = prezzo

    async def leggi_molti(self, urls):
        return [Rilevazione(self.prezzo, "Prodotto di prova", "CHF") for _ in urls]


class DbTemporaneo:
    """Un DB sqlite usa e getta, con un prodotto già dentro.

    La connessione va chiusa prima che la tempdir venga rimossa, altrimenti su
    Windows il file resta bloccato e il cleanup fallisce.
    """

    def __init__(self, prezzo=100.0, target=None):
        self.prezzo = prezzo
        self.target = target

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._vecchio_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name) / "test.db"
        self.conn = db.connect()
        self.pid = db.aggiungi(self.conn, URL, "Prodotto di prova", "esempio",
                               "CHF", self.target)
        db.registra_prezzo(self.conn, self.pid, self.prezzo, "Prodotto di prova")
        return self

    def __exit__(self, *_):
        self.conn.close()
        db.DB_PATH = self._vecchio_path
        self._tmp.cleanup()
        return False

    def riga(self):
        return self.conn.execute(
            "SELECT prezzo_target, prezzo_avvisato FROM prodotti WHERE id = ?", (self.pid,)
        ).fetchone()


async def main():
    ok = True

    # cambia l'obiettivo di un prodotto che ne aveva già uno
    with DbTemporaneo(target=80.0) as t:
        db.imposta_target(t.conn, t.pid, 70.0)
        passa = t.riga()["prezzo_target"] == 70.0
    print(f"  cambia l'obiettivo (80 -> 70)                     -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # imposta un obiettivo su un prodotto che non ne aveva
    with DbTemporaneo() as t:
        db.imposta_target(t.conn, t.pid, 50.0)
        passa = t.riga()["prezzo_target"] == 50.0
    print(f"  imposta un obiettivo dove non c'era               -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # None toglie l'obiettivo
    with DbTemporaneo(target=80.0) as t:
        db.imposta_target(t.conn, t.pid, None)
        passa = t.riga()["prezzo_target"] is None
    print(f"  None toglie l'obiettivo                           -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # azzera la memoria dell'ultimo avviso
    with DbTemporaneo() as t:
        db.registra_prezzo(t.conn, t.pid, 100.0, avvisato=100.0)
        prima = t.riga()["prezzo_avvisato"]
        db.imposta_target(t.conn, t.pid, 90.0)
        passa = prima == 100.0 and t.riga()["prezzo_avvisato"] is None
    print(f"  azzera prezzo_avvisato                            -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # id inesistente: 0 righe toccate, nessuna eccezione
    with DbTemporaneo() as t:
        passa = db.imposta_target(t.conn, 9999, 50.0) == 0
    print(f"  id inesistente -> 0 righe toccate                 -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # IL TEST CHE CONTA: obiettivo sopra il prezzo attuale su un prodotto già
    # avvisato a quel prezzo. Senza l'azzeramento di prezzo_avvisato, `gia_visto`
    # sopprimerebbe l'avviso e il nuovo obiettivo non scatterebbe mai.
    with DbTemporaneo() as t:
        db.registra_prezzo(t.conn, t.pid, 100.0, avvisato=100.0)
        db.imposta_target(t.conn, t.pid, 120.0)
        esiti = await controllo.controlla(t.conn, 1.0, scraper=ScraperFinto(100.0))
        passa = esiti[0].avvisa
    print(f"  obiettivo sopra il prezzo, già avvisato -> avvisa  -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # controprova: nessun obiettivo e prezzo invariato già avvisato -> non avvisa,
    # così si vede che il caso sopra passa per il target e non perché avvisa sempre
    with DbTemporaneo() as t:
        db.registra_prezzo(t.conn, t.pid, 100.0, avvisato=100.0)
        esiti = await controllo.controlla(t.conn, 1.0, scraper=ScraperFinto(100.0))
        passa = not esiti[0].avvisa
    print(f"  controprova senza obiettivo -> non avvisa          -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # --- parsing dell'argomento di /target (bot e CLI usano questa sola funzione) ---

    buoni = {"59.90": 59.90, "59,90": 59.90, " 12 ": 12.0}
    passa = all(controllo.leggi_obiettivo(t) == (atteso, None) for t, atteso in buoni.items())
    print(f"  prezzi validi (punto, virgola, spazi)             -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    passa = all(controllo.leggi_obiettivo(p) == (None, None)
                for p in ("mai", "off", "no", "-", "MAI", " Off "))
    passa &= all(controllo.leggi_obiettivo(p)[1] is None for p in controllo.SPEGNIMENTO)
    print(f"  mai/off/no/- togliono l'obiettivo                 -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    passa = all(controllo.leggi_obiettivo(p)[1] is not None for p in ("abc", "", "1.2.3"))
    print(f"  non numerico -> errore                            -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    passa = all(controllo.leggi_obiettivo(p)[1] is not None for p in ("0", "-5", "0.0"))
    print(f"  zero o negativo -> errore                         -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # inf e nan passano da float() e sfuggono al controllo `<= 0` (inf <= 0 e
    # nan <= 0 sono entrambi False): inf renderebbe sotto_target sempre vera
    # (avviso a ogni giro), nan sempre falsa (obiettivo muto). Vanno rifiutati.
    passa = all(controllo.leggi_obiettivo(p)[1] is not None
                for p in ("inf", "-inf", "nan", "Infinity", "1e400"))
    print(f"  inf/nan -> errore, non finiscono nel DB           -> {'OK' if passa else 'FALLITO'}")
    ok &= passa

    print("\n" + ("TUTTI I CONTROLLI OK" if ok else "QUALCOSA NON VA"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
