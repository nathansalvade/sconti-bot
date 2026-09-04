# sconti-bot

Segue i prezzi di pagine prodotto su e-commerce generici e ti scrive su Telegram quando calano.

- **Bot interattivo**: aggiungi prodotti dal telefono con `/add <url>` mentre stai
  guardando il sito. Niente cron, niente SSH.
- **Controllo asincrono**: i prodotti vengono letti in parallelo (`aiohttp` + `asyncio`),
  ma con un semaforo e un ritmo minimo **per dominio**, per non farsi bannare.
- **Scraping generico**: prova prima i selettori CSS del sito (se configurati), poi ricade
  su JSON-LD (`schema.org/Product`), meta tag e un'euristica sulle classi "price". Molti
  e-commerce funzionano senza configurare niente.
- **Storico prezzi** in SQLite, così sai anche qual è il minimo storico.
- **Riepilogo periodico**: oltre agli avvisi sui cali, puoi farti mandare un riepilogo di
  tutti i prodotti seguiti a cadenza giornaliera o settimanale.
- **Niente spam**: notifica solo su un calo reale e non ripete lo stesso prezzo.

## Setup (5 minuti)

### 1. Crea il bot su Telegram

1. Apri Telegram e scrivi a **@BotFather**.
2. `/newbot` → scegli un nome e uno username che finisca per `bot`.
3. BotFather ti dà un **token** tipo `123456789:AAE...`. Copialo.
4. **Importante**: apri la chat con il tuo nuovo bot e premi *Start* (altrimenti non può scriverti).

### 2. Configura il progetto

```bash
git clone <url-di-questo-repo>
cd sconti-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env            # incolla il TELEGRAM_BOT_TOKEN
```

Per il chat id:

```bash
.venv/bin/python main.py chatid    # dopo aver scritto /start al bot
```

Copia il numero che esce in `.env` come `TELEGRAM_CHAT_ID`, poi verifica:

```bash
.venv/bin/python main.py test      # deve arrivarti un messaggio su Telegram
```

### 3. Avvia il bot

```bash
.venv/bin/python bot.py
```

Da qui in poi fai tutto da Telegram:

```
/add <url> [prezzo]   segue un prodotto, con obiettivo opzionale
/lista                i prodotti che segui
/rimuovi <id>         smette di seguirne uno
/controlla            controlla subito tutti i prezzi
/notifiche            riepilogo prezzi giornaliero, settimanale o mai
/aiuto                l'elenco dei comandi
```

Il bot ricontrolla da solo ogni `CHECK_INTERVAL_HOURS` ore (default 6) e ti scrive quando
un prezzo cala. Non serve cron: il job periodico gira nello stesso processo.

### Riepilogo prezzi periodico

Oltre agli avvisi sui cali, puoi farti mandare un riepilogo di tutti i prodotti seguiti
(prezzo attuale, minimo storico, target) a cadenza fissa, anche se nessun prezzo è calato:

```
/notifiche                 mostra la frequenza attuale
/notifiche giornaliero     un riepilogo al giorno
/notifiche settimanale     un riepilogo alla settimana
/notifiche mai             disattiva (default)
```

La preferenza è per singola chat: in più persone, ognuno sceglie la propria. Il bot
controlla ogni `RIEPILOGO_CHECK_MINUTES` minuti (default 30) chi ha un riepilogo dovuto
in base a quando l'ha ricevuto l'ultima volta — non è legato a un orario fisso del giorno,
ma a "sono passate 24h / 7 giorni da quando è partito l'ultimo".

Il bot **risponde solo agli id in `TELEGRAM_CHAT_ID`**. È l'unica protezione che ha: chiunque
trovi lo username del bot può scrivergli, ma riceverà solo "Questo bot è privato".

### Usarlo in più persone

Metti gli id separati da virgola:

```ini
TELEGRAM_CHAT_ID=123456789,987654321
```

Tutti quelli elencati possono dare comandi e **ricevono tutti gli avvisi**. Se uno ha
bloccato il bot, gli altri li ricevono lo stesso.

Due cose da sapere prima:

- **La lista prodotti è unica e condivisa.** Non c'è separazione per utente: il tuo amico
  vede i tuoi prodotti, e con `/rimuovi` può cancellarli. Va bene fra persone che si
  fidano; se servisse separarle, andrebbe aggiunta una colonna proprietario in `prodotti`.
- **Ognuno deve premere Start.** Un bot Telegram non può scrivere per primo a chi non gli
  ha mai parlato: finché il tuo amico non apre la chat col bot e preme Start, i messaggi
  verso di lui falliscono con `chat not found`. Per avere il suo id: lui preme Start, poi
  tu lanci `.venv/bin/python main.py chatid` e lo vedi comparire.

### 4. Tenerlo acceso

Un file di servizio di esempio è in `deploy/sconti-bot.service` — sostituisci `CAMBIAMI`
con il tuo utente e il percorso dove hai clonato il repo, poi:

```bash
sudo cp deploy/sconti-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sconti-bot
systemctl status sconti-bot            # deve dire "active (running)"
journalctl -u sconti-bot -f            # per seguire i log
```

> Se sul tuo sistema preferisci un servizio utente (`systemctl --user`) invece che di
> sistema, funziona lo stesso: basta che il lingering sia abilitato per il tuo utente
> (`loginctl enable-linger $USER`), altrimenti il servizio muore al logout.

Da lì riparte da solo a ogni riavvio e dopo ogni crash (con 30s di attesa).

Non scendere sotto l'ora di intervallo: controlli troppo frequenti fanno scattare i
blocchi anti-bot.

### CLI (opzionale)

Comoda per provare i selettori di un sito senza far girare il bot:

```bash
.venv/bin/python main.py add "https://www.esempio-shop.it/prodotto-123" --target 50
.venv/bin/python main.py list
.venv/bin/python main.py check --dry-run   # controlla senza inviare niente
```

## Aggiungere un sito che non funziona

Se `add` dice *"prezzo non trovato"*:

1. Apri la pagina nel browser, tasto destro sul prezzo → **Ispeziona**.
2. Trova una classe o un id stabile dell'elemento (es. `<span class="product-price__current">`).
3. Aggiungi il dominio in `config/sites.json`:

```json
"www.nuovosito.it": {
  "nome": "Nuovo Sito",
  "prezzo": ["span.product-price__current", ".price"],
  "titolo": ["h1"]
}
```

Puoi elencare più selettori: si usa il primo che dà un numero valido.

Molti siti funzionano senza configurare nulla, perché lo scraper prova anche JSON-LD
(`schema.org/Product`), meta tag e un'euristica generica prima di arrendersi. Alcuni siti
grossi (Amazon, e altri con protezioni anti-bot tipo Akamai/Cloudflare) rispondono 403 a
uno script: per quelli servirebbe un browser headless (vedi "Limiti" sotto).

**La valuta viene rilevata da sola** dal JSON-LD/meta tag della pagina o dai simboli nel
testo (CHF, EUR, USD, GBP); il bot non fa conversioni fra valute diverse.

## Limiti da sapere

- **Anti-bot**: alcuni siti grossi rispondono 403 a uno script. Per quelli servirebbe
  Playwright con browser headless (`pip install playwright`), che è il prossimo passo
  naturale se ti serve un sito protetto.
- **Siti in JavaScript**: se il prezzo compare solo dopo il caricamento JS, `aiohttp` non lo
  vede. Stessa soluzione: Playwright per i soli domini che lo richiedono.
- **Pagine con più varianti** (es. taglie o formati diversi): lo scraper prende il primo
  prezzo che trova, che di solito è la prima variante. Controlla sempre il valore che
  `add` ti stampa.
- **I siti cambiano layout**: quando succede, il selettore va aggiornato. È la manutenzione
  normale di questo tipo di progetto.

## Come funziona la concorrenza

Il punto delicato: leggere 20 prodotti in parallelo è facile, ma 20 richieste simultanee
sullo stesso sito sono il modo più rapido per prendersi un 403. Quindi `Scraper` tiene,
**per ogni dominio**:

- un **semaforo** (max 2 richieste insieme), che limita i picchi;
- un **ritmo minimo** (1.5s + jitter fra una richiesta e la successiva), che limita la frequenza.

Domini diversi non si aspettano a vicenda: procedono in parallelo, ognuno col suo ritmo.
Il parsing HTML, che è CPU-bound, gira in `asyncio.to_thread` per non bloccare il loop.

`test_concorrenza.py` verifica tutto questo contro un server locale finto:

```bash
.venv/bin/python test_concorrenza.py
```

## Struttura

```
bot.py                il bot Telegram: comandi + job periodici
main.py               CLI (add / list / remove / check / test / chatid)
tracker/scraper.py    fetch asincrono + estrazione prezzo/titolo/valuta
tracker/controllo.py  la regola di "quando avvisare", condivisa fra bot e CLI
tracker/db.py         SQLite: prodotti, storico prezzi, preferenze di notifica
tracker/notifier.py   composizione e invio dei messaggi Telegram
config/sites.json     selettori CSS per dominio (esempio, personalizzalo)
deploy/sconti-bot.service  file di servizio systemd di esempio
test_concorrenza.py   test del semaforo e del ritmo per dominio
test_comandi.py       test dell'autorizzazione dei comandi
.env                  token e chat id (non committare — è in .gitignore)
```

## Licenza

Distribuito con licenza MIT — vedi [LICENSE](LICENSE).
