#!/usr/bin/env python3
"""Verifica il controllo di autorizzazione e i tipi di update anomali.

Il caso che ha fatto crashare il bot in produzione: un messaggio MODIFICATO arriva
con `update.message = None`, e `update.message.reply_text` esplode. Qui si controlla
che il percorso regga con messaggi nuovi, modificati e update senza messaggio.
"""

import asyncio
import os
import types

import bot as B


class MessaggioFinto:
    def __init__(self):
        self.risposte = []

    async def reply_text(self, testo, **kwargs):
        self.risposte.append(testo)
        return self


class UpdateFinto:
    """Ha solo ciò che il codice usa davvero: effective_message ed effective_chat."""

    def __init__(self, chat_id, con_messaggio=True):
        self.effective_message = MessaggioFinto() if con_messaggio else None
        self.effective_chat = types.SimpleNamespace(id=chat_id) if con_messaggio else None


async def main():
    os.environ["TELEGRAM_CHAT_ID"] = "111,222"
    eseguito = []

    @B.solo_proprietario
    async def comando(update, context):
        eseguito.append(update.effective_chat.id)
        await update.effective_message.reply_text("ok")

    ok = True

    # messaggio normale dal proprietario
    u = UpdateFinto(111)
    await comando(u, None)
    passa = eseguito == [111] and u.effective_message.risposte == ["ok"]
    print(f"  proprietario (id 111)      -> {u.effective_message.risposte}  {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # secondo autorizzato: l'amico
    u = UpdateFinto(222)
    await comando(u, None)
    passa = 222 in eseguito
    print(f"  amico (id 222)             -> {u.effective_message.risposte}  {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # estraneo
    u = UpdateFinto(999)
    await comando(u, None)
    passa = u.effective_message.risposte == ["Questo bot è privato."] and 999 not in eseguito
    print(f"  estraneo (id 999)          -> {u.effective_message.risposte}  {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # update senza messaggio: è il crash di produzione
    u = UpdateFinto(111, con_messaggio=False)
    try:
        await comando(u, None)
        passa = True
    except AttributeError as e:
        passa = False
        print(f"    AttributeError: {e}")
    print(f"  update senza messaggio     -> ignorato senza crash  {'OK' if passa else 'FALLITO'}")
    ok &= passa

    # configurazione rotta: deve rispondere, non restare muto
    os.environ["TELEGRAM_CHAT_ID"] = "non-un-numero"
    u = UpdateFinto(111)
    await comando(u, None)
    passa = bool(u.effective_message.risposte) and "Configurazione" in u.effective_message.risposte[0]
    print(f"  chat id malformato         -> risponde invece di tacere  {'OK' if passa else 'FALLITO'}")
    ok &= passa

    print("\n" + ("TUTTI I CONTROLLI OK" if ok else "QUALCOSA NON VA"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
