#!/usr/bin/env python3
"""CLI di sconti-bot — utile per provare i selettori senza far girare il bot.

    python main.py add <url> [--target 49.90]
    python main.py list
    python main.py remove <id>
    python main.py check [--dry-run]
    python main.py test          messaggio di prova su Telegram
    python main.py chatid        mostra il tuo chat id

Per l'uso normale conviene il bot interattivo:  python bot.py
"""

import argparse
import asyncio
import sys
import time

from tracker import controllo, db, notifier, scraper


async def cmd_add(args):
    conn = db.connect()
    if conn.execute("SELECT 1 FROM prodotti WHERE url = ?", (args.url,)).fetchone():
        print("Già presente in lista.")
        return 1
    try:
        ril = await scraper.leggi_prodotto(args.url)
    except scraper.ScrapeError as e:
        print(f"✗ {e}")
        return 1
    sito = scraper.nome_sito(args.url)
    pid = db.aggiungi(conn, args.url, ril.titolo, sito, ril.valuta, args.target)
    db.registra_prezzo(conn, pid, ril.prezzo, ril.titolo)
    print(f"✓ [{pid}] {ril.titolo}")
    print(f"  {sito} — prezzo attuale {ril.prezzo:.2f} {ril.valuta}")
    if args.target:
        print(f"  obiettivo {args.target:.2f} {ril.valuta}")
    print("  ⚠ se il prezzo non è quello giusto, aggiungi un selettore in config/sites.json")
    return 0


async def cmd_list(args):
    conn = db.connect()
    righe = db.elenca(conn)
    if not righe:
        print("Nessun prodotto seguito. Aggiungine uno con:  python main.py add <url>")
        return 0
    for r in righe:
        minimo = db.prezzo_minimo(conn, r["id"])
        val = r["valuta"]
        prezzo = f"{r['ultimo_prezzo']:.2f} {val}" if r["ultimo_prezzo"] else "—"
        print(f"[{r['id']}] {(r['titolo'] or r['url'])[:70]}")
        print(f"     {r['sito']} · ora {prezzo}" +
              (f" · min {minimo:.2f} {val}" if minimo else "") +
              (f" · target {r['prezzo_target']:.2f} {val}" if r["prezzo_target"] else ""))
    return 0


async def cmd_remove(args):
    conn = db.connect()
    print("✓ rimosso" if db.rimuovi(conn, args.id) else "id non trovato")
    return 0


async def cmd_check(args):
    conn = db.connect()
    inizio = time.monotonic()
    esiti = await controllo.controlla(conn, controllo.soglia_minima())
    if not esiti:
        print("Nessun prodotto da controllare.")
        return 0

    notifiche = 0
    for esito in esiti:
        print(controllo.riga_riassunto(esito))
        notificato = False
        if esito.avvisa and not args.dry_run:
            try:
                inviati, errori = await notifier.invia(notifier.messaggio_da_esito(esito))
                notificato = True
                notifiche += 1
                print(f"      → notifica inviata a {inviati} destinatari")
                for dest, err in errori:
                    print(f"        ✗ {dest}: {err}")
            except Exception as e:  # noqa: BLE001
                print(f"      → notifica fallita: {e}")
        elif esito.avvisa:
            print("      → (dry-run) avrei notificato")
        controllo.registra(conn, esito, notificato)

    durata = time.monotonic() - inizio
    print(f"\nFatto in {durata:.1f}s. {len(esiti)} prodotti, {notifiche} notifiche.")
    return 0


async def cmd_test(args):
    await notifier.invia("✅ <b>sconti-bot</b> è collegato correttamente.")
    print("✓ messaggio inviato, controlla Telegram.")
    return 0


async def cmd_chatid(args):
    chat = await notifier.chat_id_recenti()
    if not chat:
        print("Nessuna chat trovata. Apri Telegram, scrivi /start al tuo bot, e riprova.")
        return 1
    for cid, nome in chat.items():
        print(f"{cid}\t{nome}")
    print("\nCopia l'id in .env come TELEGRAM_CHAT_ID.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="aggiunge un prodotto")
    p.add_argument("url")
    p.add_argument("--target", type=float, help="prezzo obiettivo")
    p.set_defaults(func=cmd_add)

    sub.add_parser("list", help="elenca i prodotti").set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="rimuove un prodotto")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("check", help="controlla i prezzi")
    p.add_argument("--dry-run", action="store_true", help="non invia niente su Telegram")
    p.set_defaults(func=cmd_check)

    sub.add_parser("test", help="messaggio di prova").set_defaults(func=cmd_test)
    sub.add_parser("chatid", help="mostra il chat id").set_defaults(func=cmd_chatid)

    args = parser.parse_args()
    sys.exit(asyncio.run(args.func(args)))


if __name__ == "__main__":
    main()
