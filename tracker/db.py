"""Storage SQLite: prodotti seguiti e storico prezzi."""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "sconti.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS prodotti (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL UNIQUE,
    titolo          TEXT,
    sito            TEXT,
    valuta          TEXT NOT NULL DEFAULT 'CHF',
    prezzo_target   REAL,
    ultimo_prezzo   REAL,
    prezzo_avvisato REAL,
    creato_il       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storico (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prodotto_id INTEGER NOT NULL REFERENCES prodotti(id) ON DELETE CASCADE,
    prezzo      REAL NOT NULL,
    rilevato_il TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_storico_prodotto ON storico(prodotto_id);

CREATE TABLE IF NOT EXISTS notifiche_periodiche (
    chat_id      TEXT PRIMARY KEY,
    frequenza    TEXT NOT NULL DEFAULT 'mai',
    ultimo_invio TEXT
);
"""

ORE_PER_FREQUENZA = {"giornaliero": 24, "settimanale": 24 * 7}


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    # DB creati prima dell'aggiunta della valuta
    colonne = {r["name"] for r in conn.execute("PRAGMA table_info(prodotti)")}
    if "valuta" not in colonne:
        conn.execute("ALTER TABLE prodotti ADD COLUMN valuta TEXT NOT NULL DEFAULT 'CHF'")
        conn.commit()
    return conn


def aggiungi(conn, url, titolo, sito, valuta="CHF", prezzo_target=None):
    cur = conn.execute(
        "INSERT INTO prodotti (url, titolo, sito, valuta, prezzo_target, creato_il)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (url, titolo, sito, valuta, prezzo_target,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid


def elenca(conn):
    return conn.execute("SELECT * FROM prodotti ORDER BY id").fetchall()


def rimuovi(conn, prodotto_id):
    cur = conn.execute("DELETE FROM prodotti WHERE id = ?", (prodotto_id,))
    conn.execute("DELETE FROM storico WHERE prodotto_id = ?", (prodotto_id,))
    conn.commit()
    return cur.rowcount


def imposta_target(conn, prodotto_id, prezzo_target):
    """Cambia il prezzo obiettivo di un prodotto già seguito (`None` lo toglie).

    Azzera anche `prezzo_avvisato`, e non è un dettaglio: la regola in
    `controllo.controlla` sopprime un avviso quando il prezzo è >= a quello già
    annunciato. Senza l'azzeramento, un obiettivo impostato SOPRA il prezzo
    attuale di un prodotto già notificato non scatterebbe mai — `sotto_target`
    sarebbe vera e `gia_visto` la annullerebbe. Un obiettivo nuovo è
    un'intenzione nuova: la memoria dell'ultimo avviso si riferisce alla regola
    vecchia e va dimenticata.

    Ritorna il numero di righe toccate: 0 se l'id non esiste.
    """
    cur = conn.execute(
        "UPDATE prodotti SET prezzo_target = ?, prezzo_avvisato = NULL WHERE id = ?",
        (prezzo_target, prodotto_id),
    )
    conn.commit()
    return cur.rowcount


def registra_prezzo(conn, prodotto_id, prezzo, titolo=None, avvisato=None):
    """Salva la rilevazione nello storico e aggiorna lo stato del prodotto."""
    conn.execute(
        "INSERT INTO storico (prodotto_id, prezzo, rilevato_il) VALUES (?, ?, ?)",
        (prodotto_id, prezzo, datetime.now().isoformat(timespec="seconds")),
    )
    conn.execute("UPDATE prodotti SET ultimo_prezzo = ? WHERE id = ?", (prezzo, prodotto_id))
    if titolo:
        conn.execute(
            "UPDATE prodotti SET titolo = COALESCE(NULLIF(titolo, ''), ?) WHERE id = ?",
            (titolo, prodotto_id),
        )
    if avvisato is not None:
        conn.execute(
            "UPDATE prodotti SET prezzo_avvisato = ? WHERE id = ?", (avvisato, prodotto_id)
        )
    conn.commit()


def prezzo_minimo(conn, prodotto_id):
    row = conn.execute(
        "SELECT MIN(prezzo) AS m FROM storico WHERE prodotto_id = ?", (prodotto_id,)
    ).fetchone()
    return row["m"]


def imposta_notifica(conn, chat_id, frequenza):
    """Imposta la frequenza del riepilogo periodico per una chat ('giornaliero'/'settimanale'/'mai')."""
    conn.execute(
        "INSERT INTO notifiche_periodiche (chat_id, frequenza) VALUES (?, ?)"
        " ON CONFLICT(chat_id) DO UPDATE SET frequenza = excluded.frequenza",
        (str(chat_id), frequenza),
    )
    conn.commit()


def notifica_di(conn, chat_id):
    return conn.execute(
        "SELECT frequenza, ultimo_invio FROM notifiche_periodiche WHERE chat_id = ?",
        (str(chat_id),),
    ).fetchone()


def registra_invio_notifica(conn, chat_id):
    conn.execute(
        "UPDATE notifiche_periodiche SET ultimo_invio = ? WHERE chat_id = ?",
        (datetime.now().isoformat(timespec="seconds"), str(chat_id)),
    )
    conn.commit()


def notifiche_da_inviare(conn):
    """Chat con un riepilogo attivo (frequenza diversa da 'mai')."""
    return conn.execute(
        "SELECT chat_id, frequenza, ultimo_invio FROM notifiche_periodiche"
        " WHERE frequenza != 'mai'"
    ).fetchall()
