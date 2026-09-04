#!/usr/bin/env python3
"""sconti-bot — bot Telegram che segue i prezzi e avvisa quando calano.

Avvio:  .venv/bin/python bot.py

Resta in ascolto dei comandi e, in parallelo, ricontrolla i prezzi ogni
CHECK_INTERVAL_HOURS ore. Un solo processo: niente cron.
"""

import logging
import os
from datetime import datetime, timedelta
from functools import wraps

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from tracker import controllo, db, notifier, scraper

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s — %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("sconti-bot")

AIUTO = (
    "🏷 <b>sconti-bot</b>\n\n"
    "<b>/add</b> <i>url</i> [<i>prezzo</i>] — segue un prodotto, con obiettivo opzionale\n"
    "<b>/lista</b> — i prodotti che segui\n"
    "<b>/rimuovi</b> <i>id</i> — smette di seguirne uno\n"
    "<b>/controlla</b> — controlla subito tutti i prezzi\n"
    "<b>/notifiche</b> [<i>giornaliero|settimanale|mai</i>] — riepilogo prezzi periodico\n"
    "<b>/aiuto</b> — questo messaggio\n\n"
    "Ti avviso da solo quando un prezzo cala."
)

FREQUENZE = {
    "giornaliero": "giornaliero", "giornaliera": "giornaliero", "daily": "giornaliero",
    "settimanale": "settimanale", "weekly": "settimanale",
    "mai": "mai", "off": "mai", "no": "mai",
}
ETICHETTE_FREQUENZA = {"giornaliero": "ogni giorno", "settimanale": "ogni settimana", "mai": "disattivato"}


def _impostazione(nome, default):
    notifier.carica_env()
    try:
        return float(os.environ.get(nome, default))
    except ValueError:
        return float(default)


def solo_proprietario(func):
    """Il bot risponde solo al TELEGRAM_CHAT_ID configurato."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # `update.message` è None sui messaggi MODIFICATI: usare sempre
        # effective_message, che copre sia i nuovi sia le modifiche.
        if update.effective_message is None or update.effective_chat is None:
            log.warning("update senza messaggio, ignorato: %s", update)
            return
        try:
            autorizzati = notifier.chat_ids()
        except RuntimeError as e:
            # senza questo il comando morirebbe in silenzio e tu resteresti ad aspettare
            await update.effective_message.reply_text(f"⚠️ Configurazione incompleta: {e}")
            return
        if str(update.effective_chat.id) not in autorizzati:
            log.warning("comando rifiutato da chat %s", update.effective_chat.id)
            await update.effective_message.reply_text("Questo bot è privato.")
            return
        return await func(update, context)
    return wrapper


async def gestore_errori(update, context):
    """Qualunque eccezione non gestita: la scrivo nei log E in chat, mai silenzio."""
    log.exception("errore non gestito", exc_info=context.error)
    messaggio = getattr(update, "effective_message", None)
    if messaggio:
        try:
            await messaggio.reply_text(f"⚠️ Errore imprevisto: {context.error}")
        except Exception:  # noqa: BLE001 — se non riesco nemmeno a rispondere, basta il log
            pass


# --------------------------------------------------------------------------
# comandi
# --------------------------------------------------------------------------

@solo_proprietario
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(AIUTO, parse_mode=ParseMode.HTML)


@solo_proprietario
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Uso: /add <url> [prezzo obiettivo]")
        return
    url = context.args[0]
    target = None
    if len(context.args) > 1:
        try:
            target = float(context.args[1].replace(",", "."))
        except ValueError:
            await update.effective_message.reply_text("Il prezzo obiettivo dev'essere un numero.")
            return

    conn = db.connect()
    try:
        if conn.execute("SELECT 1 FROM prodotti WHERE url = ?", (url,)).fetchone():
            await update.effective_message.reply_text("Lo sto già seguendo.")
            return
        avviso = await update.effective_message.reply_text("⏳ leggo la pagina…")
        try:
            ril = await scraper.leggi_prodotto(url)
        except scraper.ScrapeError as e:
            await avviso.edit_text(f"✗ {e}")
            return

        sito = scraper.nome_sito(url)
        pid = db.aggiungi(conn, url, ril.titolo, sito, ril.valuta, target)
        db.registra_prezzo(conn, pid, ril.prezzo, ril.titolo)
        testo = (f"✓ <b>[{pid}] {ril.titolo}</b>\n{sito} — "
                 f"<b>{ril.prezzo:.2f} {ril.valuta}</b>")
        if target:
            testo += f"\n🎯 obiettivo {target:.2f} {ril.valuta}"
        testo += "\n\n<i>Se il prezzo non è quello giusto, serve un selettore per questo sito.</i>"
        await avviso.edit_text(testo, parse_mode=ParseMode.HTML)
    finally:
        conn.close()


@solo_proprietario
async def cmd_lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db.connect()
    try:
        testo = controllo.testo_riepilogo(conn)
        if not testo:
            await update.effective_message.reply_text("Non segui ancora niente. Usa /add <url>")
            return
        await update.effective_message.reply_text(
            testo, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    finally:
        conn.close()


@solo_proprietario
async def cmd_notifiche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db.connect()
    try:
        chat_id = str(update.effective_chat.id)
        if not context.args:
            row = db.notifica_di(conn, chat_id)
            attuale = row["frequenza"] if row else "mai"
            await update.effective_message.reply_text(
                f"Riepilogo prezzi attuale: <b>{ETICHETTE_FREQUENZA[attuale]}</b>.\n\n"
                "Uso: /notifiche giornaliero | settimanale | mai",
                parse_mode=ParseMode.HTML,
            )
            return
        scelta = FREQUENZE.get(context.args[0].lower())
        if scelta is None:
            await update.effective_message.reply_text(
                "Non capito. Uso: /notifiche giornaliero | settimanale | mai")
            return
        db.imposta_notifica(conn, chat_id, scelta)
        await update.effective_message.reply_text(
            f"✓ riepilogo prezzi: <b>{ETICHETTE_FREQUENZA[scelta]}</b>.",
            parse_mode=ParseMode.HTML,
        )
    finally:
        conn.close()


@solo_proprietario
async def cmd_rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Uso: /rimuovi <id>  (l'id lo vedi in /lista)")
        return
    conn = db.connect()
    try:
        tolto = db.rimuovi(conn, int(context.args[0]))
        await update.effective_message.reply_text("✓ rimosso" if tolto else "id non trovato")
    finally:
        conn.close()


@solo_proprietario
async def cmd_controlla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    avviso = await update.effective_message.reply_text("⏳ controllo i prezzi…")
    esiti = await _giro_di_controllo(context, silenzioso=False)
    if esiti is None:
        await avviso.edit_text("Non segui ancora niente. Usa /add <url>")
        return
    righe = [controllo.riga_riassunto(e) for e in esiti]
    falliti = sum(1 for e in esiti if not e.ok)
    coda = f"\n\n{len(esiti)} controllati, {falliti} falliti." if falliti else ""
    await avviso.edit_text("\n".join(righe)[:3900] + coda)


# --------------------------------------------------------------------------
# controllo periodico
# --------------------------------------------------------------------------

async def _giro_di_controllo(context: ContextTypes.DEFAULT_TYPE, silenzioso=True):
    """Un giro completo: legge i prezzi in parallelo, notifica i cali, salva."""
    conn = db.connect()
    try:
        soglia = _impostazione("MIN_DROP_PERCENT", 1.0)
        esiti = await controllo.controlla(conn, soglia)
        if not esiti:
            return None
        for esito in esiti:
            notificato = False
            if esito.avvisa:
                testo = notifier.messaggio_da_esito(esito)
                # l'avviso va a tutti gli autorizzati: se uno ha bloccato il bot,
                # gli altri lo ricevono lo stesso
                for destinatario in notifier.chat_ids():
                    try:
                        await context.bot.send_message(chat_id=destinatario, text=testo,
                                                       parse_mode=ParseMode.HTML)
                        notificato = True
                    except Exception:  # noqa: BLE001 — un errore non ferma il giro
                        log.exception("notifica fallita per %s (prodotto %s)",
                                      destinatario, esito.prodotto["id"])
            controllo.registra(conn, esito, notificato)
            if not silenzioso:
                log.info(controllo.riga_riassunto(esito))
        return esiti
    finally:
        conn.close()


async def job_periodico(context: ContextTypes.DEFAULT_TYPE):
    log.info("giro di controllo periodico")
    esiti = await _giro_di_controllo(context)
    if esiti:
        log.info("controllati %d prodotti, %d avvisi",
                 len(esiti), sum(1 for e in esiti if e.avvisa))


async def job_riepiloghi(context: ContextTypes.DEFAULT_TYPE):
    """Manda a chi lo ha attivato il riepilogo prezzi, quando è dovuto.

    Non è legato a un orario fisso: gira ogni RIEPILOGO_CHECK_MINUTES minuti e
    controlla per ogni chat se sono trascorse abbastanza ore dall'ultimo invio.
    """
    conn = db.connect()
    try:
        da_inviare = db.notifiche_da_inviare(conn)
        if not da_inviare:
            return
        testo_prodotti = controllo.testo_riepilogo(conn)
        if not testo_prodotti:
            return
        ora = datetime.now()
        for riga in da_inviare:
            soglia_ore = db.ORE_PER_FREQUENZA.get(riga["frequenza"])
            if soglia_ore is None:
                continue
            if riga["ultimo_invio"]:
                trascorso = ora - datetime.fromisoformat(riga["ultimo_invio"])
                if trascorso < timedelta(hours=soglia_ore):
                    continue
            titolo = ("🗓 <b>Riepilogo settimanale prezzi</b>" if riga["frequenza"] == "settimanale"
                     else "🗓 <b>Riepilogo prezzi di oggi</b>")
            try:
                await context.bot.send_message(
                    chat_id=riga["chat_id"], text=f"{titolo}\n\n{testo_prodotti}",
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                )
                db.registra_invio_notifica(conn, riga["chat_id"])
            except Exception:  # noqa: BLE001 — un errore non ferma il giro sulle altre chat
                log.exception("riepilogo periodico fallito per %s", riga["chat_id"])
    finally:
        conn.close()


# --------------------------------------------------------------------------

def main():
    notifier.carica_env()
    ore = _impostazione("CHECK_INTERVAL_HOURS", 6)

    app = Application.builder().token(notifier.token()).build()
    app.add_handler(CommandHandler(["start", "aiuto", "help"], cmd_start))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler(["lista", "list"], cmd_lista))
    app.add_handler(CommandHandler(["rimuovi", "remove"], cmd_rimuovi))
    app.add_handler(CommandHandler(["controlla", "check"], cmd_controlla))
    app.add_handler(CommandHandler(["notifiche", "report"], cmd_notifiche))

    app.add_error_handler(gestore_errori)
    app.job_queue.run_repeating(job_periodico, interval=ore * 3600, first=60)
    minuti_riepilogo = _impostazione("RIEPILOGO_CHECK_MINUTES", 30)
    app.job_queue.run_repeating(job_riepiloghi, interval=minuti_riepilogo * 60, first=120)

    log.info("bot avviato — controllo ogni %g ore", ore)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
