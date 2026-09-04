"""Decide quali cali di prezzo meritano una notifica.

Sta in un modulo suo perché lo usano sia il bot (job periodico) sia la CLI:
la regola di "quando avvisare" dev'essere una sola.
"""

from dataclasses import dataclass

from . import db
from .scraper import Rilevazione, Scraper, ScrapeError


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


async def controlla(conn, soglia_percentuale=1.0, scraper=None):
    """Controlla tutti i prodotti in parallelo. Non scrive nulla sul DB."""
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
        calo_vero = esito.precedente is not None and calo >= soglia_percentuale
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
