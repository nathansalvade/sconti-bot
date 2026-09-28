"""Decide quali cali di prezzo meritano una notifica.

Sta in un modulo suo perché lo usano sia il bot (job periodico) sia la CLI:
la regola di "quando avvisare" dev'essere una sola.
"""

import math
import os
from dataclasses import dataclass

from . import db, notifier
from .scraper import Rilevazione, Scraper, ScrapeError

SOGLIA_PREDEFINITA = 1.0  # percentuale: usata se MIN_DROP_PERCENT manca o non è valida
# le parole con cui si toglie l'obiettivo, le stesse con cui si spegne /notifiche
SPEGNIMENTO = {"mai", "off", "no", "-"}


def leggi_obiettivo(testo):
    """Interpreta l'argomento di /target, per il bot e per la CLI insieme.

    Ritorna `(target, errore)`. Se `errore` è None l'input va bene, e `target` è
    il prezzo obiettivo oppure None quando l'obiettivo va tolto.

    Sta qui, accanto alla regola di "quando avvisare", perché i valori che
    rifiuta sono esattamente quelli che la romperebbero: `inf` renderebbe
    `sotto_target` sempre vera (avviso a ogni controllo), `nan` sempre falsa
    (obiettivo che non scatta mai, in silenzio), e uno zero o un negativo non
    sarebbero mai raggiungibili. `float()` accetta tutti e tre senza lamentarsi.
    """
    if testo.strip().lower() in SPEGNIMENTO:
        return None, None
    try:
        valore = float(testo.replace(",", "."))
    except ValueError:
        return None, "Il prezzo obiettivo dev'essere un numero."
    if not math.isfinite(valore):
        return None, "Il prezzo obiettivo dev'essere un numero finito."
    if valore <= 0:
        return None, "L'obiettivo deve essere maggiore di zero: a zero non scatterebbe mai."
    return valore, None


@dataclass
class Esito:
    prodotto: dict          # riga della tabella prodotti
    rilevazione: Rilevazione | None = None
    errore: str | None = None
    precedente: float | None = None
    minimo: float | None = None
    avvisa: bool = False

    @property
    def ok(self):
        return self.rilevazione is not None


def _valuta(esito):
    return esito.rilevazione.valuta if esito.ok else esito.prodotto["valuta"]


def soglia_minima():
    """Legge MIN_DROP_PERCENT dall'.env: la percentuale di calo minima per avvisare.

    Un valore mancante o non numerico ricade sul default. Una soglia negativa non
    ha senso (finirebbe per far scattare l'avviso sugli aumenti di prezzo, vedi
    `controlla`), quindi ricade sul default anche lei.
    """
    notifier.carica_env()
    try:
        valore = float(os.environ.get("MIN_DROP_PERCENT", SOGLIA_PREDEFINITA))
    except ValueError:
        return SOGLIA_PREDEFINITA
    return valore if valore >= 0 else SOGLIA_PREDEFINITA


async def controlla(conn, soglia_percentuale=None, scraper=None):
    """Controlla tutti i prodotti in parallelo. Non scrive nulla sul DB."""
    if soglia_percentuale is None:
        soglia_percentuale = soglia_minima()
    prodotti = db.elenca(conn)
    if not prodotti:
        return []

    async def _giro(s):
        return await s.leggi_molti([p["url"] for p in prodotti])

    if scraper is None:
        async with Scraper() as s:
            risultati = await _giro(s)
    else:
        risultati = await _giro(scraper)

    esiti = []
    for prodotto, risultato in zip(prodotti, risultati):
        esito = Esito(prodotto=prodotto)
        if isinstance(risultato, BaseException):
            esito.errore = (str(risultato) if isinstance(risultato, ScrapeError)
                            else f"{type(risultato).__name__}: {risultato}")
            esiti.append(esito)
            continue

        esito.rilevazione = risultato
        esito.precedente = prodotto["ultimo_prezzo"]
        esito.minimo = db.prezzo_minimo(conn, prodotto["id"])

        prezzo = risultato.prezzo
        target = prodotto["prezzo_target"]
        avvisato = prodotto["prezzo_avvisato"]

        sotto_target = target is not None and prezzo <= target
        calo = ((esito.precedente - prezzo) / esito.precedente * 100) if esito.precedente else 0
        # `calo > 0` è necessario oltre al confronto con la soglia: con soglia 0
        # (chi vuole "avvisami su qualsiasi calo") `calo >= soglia_percentuale`
        # sarebbe vera anche a prezzo invariato o in aumento, e avviserebbe sempre.
        calo_vero = esito.precedente is not None and calo > 0 and calo >= soglia_percentuale
        # non ripetere un avviso già mandato allo stesso prezzo (o superiore)
        gia_visto = avvisato is not None and prezzo >= avvisato

        esito.avvisa = (sotto_target or calo_vero) and not gia_visto
        esiti.append(esito)

    return esiti


def registra(conn, esito, notificato):
    """Salva la rilevazione. `notificato` = True solo se l'avviso è partito davvero."""
    if not esito.ok:
        return
    db.registra_prezzo(
        conn,
        esito.prodotto["id"],
        esito.rilevazione.prezzo,
        esito.rilevazione.titolo,
        avvisato=esito.rilevazione.prezzo if notificato else None,
    )


def testo_riepilogo(conn):
    """Elenco prodotti con prezzo attuale, minimo storico e target: usato da /lista e
    dal riepilogo periodico, così il formato resta uno solo."""
    righe = db.elenca(conn)
    if not righe:
        return None
    blocchi = []
    for r in righe:
        minimo = db.prezzo_minimo(conn, r["id"])
        val = r["valuta"]
        ora = f"{r['ultimo_prezzo']:.2f} {val}" if r["ultimo_prezzo"] else "—"
        riga = f"<b>[{r['id']}]</b> {(r['titolo'] or r['url'])[:60]}\n     {r['sito']} · {ora}"
        if minimo:
            riga += f" · min {minimo:.2f} {val}"
        if r["prezzo_target"]:
            riga += f" · 🎯 {r['prezzo_target']:.2f} {val}"
        blocchi.append(riga)
    return "\n\n".join(blocchi)


def riga_riassunto(esito):
    """Una riga di testo per la CLI o per il riepilogo in chat."""
    p = esito.prodotto
    if not esito.ok:
        return f"[{p['id']}] ✗ {esito.errore}"
    val = _valuta(esito)
    testo = f"[{p['id']}] {esito.rilevazione.prezzo:.2f} {val}"
    if esito.precedente:
        testo += f" (prima {esito.precedente:.2f} {val})"
    return f"{testo}  {(esito.rilevazione.titolo or p['url'])[:50]}"
