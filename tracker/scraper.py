"""Estrazione asincrona del prezzo da pagine prodotto.

Strategia di estrazione, in ordine:
  1. selettori CSS specifici del dominio (config/sites.json)
  2. JSON-LD `Product > offers.price`   <- funziona su moltissimi e-commerce
  3. meta tag (og:price:amount, product:price:amount) e microdata itemprop="price"
  4. euristica: elementi con classe/id "price"/"prezzo" che mostrano una valuta

Concorrenza: le richieste partono in parallelo, ma ogni dominio ha un proprio
semaforo (max N richieste insieme) e una pausa minima fra una richiesta e la
successiva. Serve a non farsi bannare: 20 richieste simultanee sullo stesso
sito sono il modo più rapido per prendersi un 403.
"""

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sites.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class ScrapeError(Exception):
    pass


@dataclass
class Rilevazione:
    prezzo: float
    titolo: str | None
    valuta: str


# --------------------------------------------------------------------------
# configurazione
# --------------------------------------------------------------------------

_config_cache = None


def carica_config(ricarica=False):
    global _config_cache
    if _config_cache is None or ricarica:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            dati = json.load(f)
        _config_cache = {k: v for k, v in dati.items() if not k.startswith("_")}
    return _config_cache


def dominio(url):
    return urlparse(url).netloc.lower()


def nome_sito(url):
    cfg = carica_config().get(dominio(url))
    if cfg and cfg.get("nome"):
        return cfg["nome"]
    return dominio(url).replace("www.", "")


# --------------------------------------------------------------------------
# parsing (sincrono: sono funzioni pure su HTML già scaricato)
# --------------------------------------------------------------------------

def pulisci_prezzo(testo):
    """'1.299,00 €' -> 1299.0 ; "CHF 1'299.00" -> 1299.0 ; '49€90' -> 49.90"""
    if testo is None:
        return None
    testo = str(testo).replace("\xa0", " ")
    # separatore migliaia svizzero: 1'299.00 (anche con apostrofo tipografico)
    testo = re.sub(r"(?<=\d)['’´](?=\d)", "", testo)
    # alcuni siti scrivono il prezzo come "49€90"
    testo = re.sub(r"(\d)\s*[€$£]\s*(\d{2})\b", r"\1,\2", testo)
    match = re.search(r"\d[\d.,\s]*\d|\d", testo)
    if not match:
        return None
    num = match.group(0).replace(" ", "")

    if "," in num and "." in num:
        # l'ultimo separatore che compare è quello decimale
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        # virgola decimale solo se seguita da 1-2 cifre finali
        num = num.replace(",", ".") if re.search(r",\d{1,2}$", num) else num.replace(",", "")
    elif "." in num:
        if not re.search(r"\.\d{1,2}$", num):
            num = num.replace(".", "")

    try:
        valore = float(num)
    except ValueError:
        return None
    return valore if valore > 0 else None


def _da_selettori(soup, selettori):
    for sel in selettori or []:
        for el in soup.select(sel):
            prezzo = pulisci_prezzo(el.get("content") or el.get_text(" ", strip=True))
            if prezzo:
                return prezzo
    return None


def _testo_da_selettori(soup, selettori):
    for sel in selettori or []:
        el = soup.select_one(sel)
        if el:
            testo = el.get_text(" ", strip=True)
            if testo:
                return testo[:200]
    return None


def _da_jsonld(soup):
    """Cerca uno schema.org Product con offers.price."""
    def offerte(nodo):
        if isinstance(nodo, list):
            for x in nodo:
                yield from offerte(x)
        elif isinstance(nodo, dict):
            if "offers" in nodo:
                off = nodo["offers"]
                for o in off if isinstance(off, list) else [off]:
                    if isinstance(o, dict):
                        yield o
            for v in nodo.values():
                if isinstance(v, (dict, list)):
                    yield from offerte(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            dati = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for off in offerte(dati):
            prezzo = pulisci_prezzo(off.get("price") or off.get("lowPrice"))
            if prezzo:
                return prezzo
    return None


def _da_meta(soup):
    for attr, chiave in (("property", "product:price:amount"), ("property", "og:price:amount"),
                         ("itemprop", "price"), ("name", "twitter:data1")):
        el = soup.find("meta", attrs={attr: chiave})
        if el:
            prezzo = pulisci_prezzo(el.get("content"))
            if prezzo:
                return prezzo
    el = soup.find(attrs={"itemprop": "price"})
    if el:
        return pulisci_prezzo(el.get("content") or el.get_text(" ", strip=True))
    return None


VALUTA = re.compile(r"[€$£]\s?\d|\d\s?[€$£]|\b(EUR|CHF|Fr\.?)\b", re.IGNORECASE)


def _da_euristica(soup):
    """Ultima spiaggia: un elemento la cui classe/id parla di prezzo e che mostra una valuta."""
    selettore = ("[class*=price i], [id*=price i], [class*=prezzo i], "
                 "[id*=prezzo i], [data-testid*=price i]")
    for el in soup.select(selettore):
        testo = el.get_text(" ", strip=True)
        if not testo or len(testo) > 40 or not VALUTA.search(testo):
            continue
        prezzo = pulisci_prezzo(testo)
        if prezzo:
            return prezzo
    return None


SIMBOLI = {"CHF": "CHF", "FR.": "CHF", "€": "EUR", "EUR": "EUR",
           "$": "USD", "USD": "USD", "£": "GBP", "GBP": "GBP"}


def _da_jsonld_valuta(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        match = re.search(r'"priceCurrency"\s*:\s*"([A-Z]{3})"', tag.string or "")
        if match:
            return match.group(1)
    return None


def rileva_valuta(soup, default="CHF"):
    """Valuta della pagina: JSON-LD, poi meta tag, poi simboli nel testo."""
    valuta = _da_jsonld_valuta(soup)
    if valuta:
        return valuta
    for attr, chiave in (("property", "product:price:currency"),
                         ("property", "og:price:currency"),
                         ("itemprop", "priceCurrency")):
        el = soup.find("meta", attrs={attr: chiave})
        if el and el.get("content"):
            return el["content"].strip().upper()[:3]
    testo = soup.get_text(" ", strip=True)[:20000].upper()
    for simbolo, codice in SIMBOLI.items():
        if simbolo in testo:
            return codice
    return default


def _titolo(soup, selettori):
    og = soup.find("meta", property="og:title")
    return (
        _testo_da_selettori(soup, selettori)
        or (og.get("content") if og else None)
        or _testo_da_selettori(soup, ["h1"])
        or (soup.title.get_text(strip=True)[:200] if soup.title else None)
    )


def estrai(html, url):
    """Da HTML a Rilevazione. Separata dal fetch così è testabile senza rete."""
    cfg = carica_config().get(dominio(url), {})
    soup = BeautifulSoup(html, "lxml")
    prezzo = (
        _da_selettori(soup, cfg.get("prezzo"))
        or _da_jsonld(soup)
        or _da_meta(soup)
        or _da_euristica(soup)
    )
    if prezzo is None:
        raise ScrapeError(
            f"prezzo non trovato: aggiungi un selettore CSS per '{dominio(url)}'"
            " in config/sites.json"
        )
    return Rilevazione(prezzo, _titolo(soup, cfg.get("titolo")),
                       rileva_valuta(soup, cfg.get("valuta", "CHF")))


# --------------------------------------------------------------------------
# fetch asincrono
# --------------------------------------------------------------------------

class Scraper:
    """Client asincrono, educato: limita la concorrenza e la frequenza per dominio.

        async with Scraper() as s:
            ril = await s.leggi(url)
            risultati = await s.leggi_molti([url1, url2, ...])
    """

    def __init__(self, max_per_dominio=2, pausa_minima=1.5, timeout=25):
        self.max_per_dominio = max_per_dominio
        self.pausa_minima = pausa_minima
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._semafori = {}
        self._ritmo = {}      # dominio -> (Lock, istante dell'ultima richiesta)
        self._session = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(headers=HEADERS, timeout=self.timeout)
        return self

    async def __aexit__(self, *exc):
        await self._session.close()
        self._session = None

    def _semaforo(self, dom):
        if dom not in self._semafori:
            self._semafori[dom] = asyncio.Semaphore(self.max_per_dominio)
        return self._semafori[dom]

    async def _attendi_turno(self, dom):
        """Garantisce almeno `pausa_minima` secondi fra due richieste allo stesso sito."""
        if dom not in self._ritmo:
            self._ritmo[dom] = [asyncio.Lock(), 0.0]
        stato = self._ritmo[dom]
        async with stato[0]:
            # l'istante va letto DENTRO il lock: leggerlo prima significa che due
            # coroutine calcolano l'attesa sullo stesso valore stantio e partono insieme
            attesa = self.pausa_minima + random.uniform(0, 0.8) - (time.monotonic() - stato[1])
            if attesa > 0:
                await asyncio.sleep(attesa)
            stato[1] = time.monotonic()

    async def _scarica(self, url):
        dom = dominio(url)
        async with self._semaforo(dom):
            await self._attendi_turno(dom)
            try:
                async with self._session.get(url) as resp:
                    if resp.status == 403:
                        raise ScrapeError("403: il sito blocca le richieste automatiche")
                    resp.raise_for_status()
                    return await resp.text()
            except asyncio.TimeoutError as e:
                raise ScrapeError("timeout: il sito non ha risposto in tempo") from e
            except aiohttp.ClientError as e:
                raise ScrapeError(f"richiesta fallita: {e}") from e

    async def leggi(self, url):
        html = await self._scarica(url)
        # il parsing è CPU-bound: in un thread separato non blocca il resto del loop
        return await asyncio.to_thread(estrai, html, url)

    async def leggi_molti(self, urls):
        """Legge tutti gli URL in parallelo. Ritorna una lista di Rilevazione o Exception."""
        return await asyncio.gather(*(self.leggi(u) for u in urls), return_exceptions=True)


async def leggi_prodotto(url):
    """Comodità per leggere un singolo URL senza gestire il context manager."""
    async with Scraper() as s:
        return await s.leggi(url)
