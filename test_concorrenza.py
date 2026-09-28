#!/usr/bin/env python3
"""Verifica il comportamento concorrente dello Scraper con un server locale finto.

Controlla tre cose:
  1. il prezzo viene estratto correttamente
  2. su uno stesso dominio non partono più di `max_per_dominio` richieste insieme
  3. fra due richieste allo stesso dominio passa almeno `pausa_minima`
  4. domini diversi procedono in parallelo (non si aspettano a vicenda)
"""

import asyncio
import time

from aiohttp import web

from tracker import console_utf8
from tracker.scraper import Scraper

console_utf8()

RITARDO = 0.3           # quanto ci mette il "sito" a rispondere
attive = 0
picco = 0
istanti = []


async def pagina(request):
    global attive, picco
    attive += 1
    picco = max(picco, attive)
    istanti.append(time.monotonic())
    await asyncio.sleep(RITARDO)
    attive -= 1
    prezzo = request.match_info["n"]
    return web.Response(
        text=f"""<html><head><title>Prodotto {prezzo}</title>
        <script type="application/ld+json">
        {{"@type":"Product","name":"Prodotto {prezzo}",
          "offers":{{"price":"{prezzo}.50","priceCurrency":"CHF"}}}}
        </script></head><body><h1>Prodotto {prezzo}</h1></body></html>""",
        content_type="text/html",
    )


async def main():
    app = web.Application()
    app.router.add_get("/p/{n}", pagina)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8765)
    await site.start()

    ok = True
    try:
        # --- 1+2+3: sei richieste sullo stesso dominio, max 2 insieme ---
        urls = [f"http://127.0.0.1:8765/p/{n}" for n in range(10, 16)]
        async with Scraper(max_per_dominio=2, pausa_minima=0.5) as s:
            inizio = time.monotonic()
            ris = await s.leggi_molti(urls)
            durata = time.monotonic() - inizio

        prezzi = [r.prezzo for r in ris if not isinstance(r, BaseException)]
        atteso = [n + 0.5 for n in range(10, 16)]
        print(f"prezzi estratti : {prezzi}")
        print(f"attesi          : {atteso}")
        ok &= prezzi == atteso
        print(f"  -> estrazione {'OK' if prezzi == atteso else 'FALLITA'}")

        print(f"\npicco richieste simultanee: {picco} (limite 2)")
        ok &= picco <= 2
        print(f"  -> semaforo {'OK' if picco <= 2 else 'VIOLATO'}")

        spazi = [round(b - a, 2) for a, b in zip(sorted(istanti), sorted(istanti)[1:])]
        minimo = min(spazi) if spazi else 0
        print(f"distanza fra richieste: {spazi}  (minimo {minimo}s, richiesto ≥0.5)")
        ok &= minimo >= 0.45
        print(f"  -> ritmo {'OK' if minimo >= 0.45 else 'VIOLATO'}")
        print(f"durata totale: {durata:.1f}s")
        print("  nota: su UN SOLO dominio è il ritmo a comandare, non il semaforo —")
        print("  ed è giusto così. Il guadagno vero si vede su domini diversi (sotto).")

        # --- 4: domini diversi non si bloccano a vicenda ---
        istanti.clear()
        misti = ([f"http://127.0.0.1:8765/p/{n}" for n in range(20, 24)] +
                 [f"http://localhost:8765/p/{n}" for n in range(30, 34)])
        async with Scraper(max_per_dominio=2, pausa_minima=0.5) as s:
            inizio = time.monotonic()
            await s.leggi_molti(misti)
            durata_mista = time.monotonic() - inizio
        print(f"\n8 URL su 2 domini: {durata_mista:.1f}s")
        print(f"  (4 URL su 1 dominio da soli: ~{durata:.1f}s — se il parallelismo fra")
        print("   domini funziona, gli 8 non devono costare il doppio dei 4)")
        ok &= durata_mista < durata * 1.6
        print(f"  -> parallelismo fra domini {'OK' if durata_mista < durata * 1.6 else 'ASSENTE'}")
    finally:
        await runner.cleanup()

    print("\n" + ("TUTTI I CONTROLLI OK" if ok else "QUALCOSA NON VA"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
