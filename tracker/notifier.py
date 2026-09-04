"""Composizione dei messaggi e invio via Telegram Bot API (asincrono)."""

import asyncio
import html
import os
import re
from pathlib import Path

import aiohttp

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
API = "https://api.telegram.org/bot{token}/{metodo}"


def carica_env():
    """Legge .env senza dipendenze esterne; le variabili d'ambiente vincono."""
    if ENV_PATH.exists():
        for riga in ENV_PATH.read_text(encoding="utf-8").splitlines():
            riga = riga.strip()
            if not riga or riga.startswith("#") or "=" not in riga:
                continue
            chiave, _, valore = riga.partition("=")
            os.environ.setdefault(chiave.strip(), valore.strip().strip('"').strip("'"))


def token():
    carica_env()
    valore = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not valore:
        raise RuntimeError("TELEGRAM_BOT_TOKEN mancante: copia .env.example in .env")
    return valore


def chat_ids():
    """Gli id autorizzati. TELEGRAM_CHAT_ID può contenerne più d'uno, separati da virgola."""
    carica_env()
    grezzo = os.environ.get("TELEGRAM_CHAT_ID", "")
    ids = [x for x in re.split(r"[,;\s]+", grezzo) if x]
    if not ids:
        raise RuntimeError("TELEGRAM_CHAT_ID mancante: copia .env.example in .env")
    for valore in ids:
        if not re.fullmatch(r"-?\d+", valore):
            raise RuntimeError(
                f"chat id non valido: {valore!r}. Devono essere numeri separati da virgola, "
                "es. TELEGRAM_CHAT_ID=123456789,987654321"
            )
    return ids


def chat_id():
    """Il primo id: è il destinatario di default e il 'proprietario' del bot."""
    return chat_ids()[0]


async def _invia_a_uno(sess, destinatario, testo):
    async with sess.post(
        API.format(token=token(), metodo="sendMessage"),
        json={"chat_id": destinatario, "text": testo,
              "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        corpo = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"Telegram ha risposto {resp.status}: {corpo}")
        return corpo


async def invia(testo, destinatario=None):
    """Invia il messaggio. Senza `destinatario`, lo manda a tutti gli id autorizzati.

    Se uno dei destinatari fallisce (ha bloccato il bot, non gli ha mai scritto…)
    gli altri ricevono comunque: si solleva un errore solo se falliscono tutti.
    """
    destinatari = [destinatario] if destinatario else chat_ids()
    async with aiohttp.ClientSession() as sess:
        esiti = await asyncio.gather(
            *(_invia_a_uno(sess, d, testo) for d in destinatari), return_exceptions=True
        )
    errori = [(d, e) for d, e in zip(destinatari, esiti) if isinstance(e, BaseException)]
    if len(errori) == len(destinatari):
        raise RuntimeError("; ".join(f"{d}: {e}" for d, e in errori))
    return len(destinatari) - len(errori), errori


async def chat_id_recenti():
    """Chat che hanno scritto al bot di recente: serve a scoprire il proprio chat id."""
    async with aiohttp.ClientSession() as sess:
        async with sess.get(API.format(token=token(), metodo="getUpdates"),
                            timeout=aiohttp.ClientTimeout(total=20)) as resp:
            dati = await resp.json()
    trovate = {}
    for upd in dati.get("result", []):
        chat = (upd.get("message") or upd.get("channel_post") or {}).get("chat") or {}
        if chat.get("id"):
            trovate[chat["id"]] = (chat.get("username") or chat.get("title")
                                   or chat.get("first_name") or "?")
    return trovate


def messaggio_sconto(titolo, url, sito, prezzo, precedente, target=None, minimo=None,
                     valuta="CHF"):
    calo = precedente - prezzo
    perc = calo / precedente * 100 if precedente else 0
    righe = [
        "🔥 <b>Prezzo in calo!</b>",
        "",
        f"<b>{html.escape(titolo or url)}</b>",
        f"<i>{html.escape(sito)}</i>",
        "",
        f"💶 <b>{prezzo:.2f} {valuta}</b>  (prima {precedente:.2f} {valuta})",
        f"📉 −{calo:.2f} {valuta} ({perc:.1f}%)",
    ]
    if target:
        righe.append(f"🎯 Obiettivo: {target:.2f} {valuta}")
    if minimo is not None and prezzo <= minimo:
        righe.append("🏆 È il prezzo più basso mai registrato.")
    righe += ["", f'<a href="{html.escape(url, quote=True)}">Vai al prodotto</a>']
    return "\n".join(righe)


def messaggio_da_esito(esito):
    p = esito.prodotto
    r = esito.rilevazione
    return messaggio_sconto(r.titolo or p["titolo"], p["url"], p["sito"],
                            r.prezzo, esito.precedente or r.prezzo,
                            p["prezzo_target"], esito.minimo, r.valuta)
