"""Inicjalizacja bazy danych SQLite i operacje zapisu."""
import json
import logging
import sqlite3
from pathlib import Path

from .models import Practitioner

logger = logging.getLogger(__name__)

DB_PATH = Path("data/heallist.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS practitioners (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_url     TEXT UNIQUE NOT NULL,
    name            TEXT,
    specializations TEXT,
    email           TEXT,
    phone           TEXT,
    website         TEXT,
    social_links    TEXT,
    city            TEXT,
    country         TEXT,
    address         TEXT,
    bio             TEXT,
    raw_json        TEXT,
    scraped_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

UPSERT_SQL = """
INSERT INTO practitioners
    (profile_url, name, specializations, email, phone, website,
     social_links, city, country, address, bio, raw_json, scraped_at)
VALUES
    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
ON CONFLICT(profile_url) DO UPDATE SET
    name            = excluded.name,
    specializations = excluded.specializations,
    email           = excluded.email,
    phone           = excluded.phone,
    website         = excluded.website,
    social_links    = excluded.social_links,
    city            = excluded.city,
    country         = excluded.country,
    address         = excluded.address,
    bio             = excluded.bio,
    raw_json        = excluded.raw_json,
    scraped_at      = CURRENT_TIMESTAMP
"""


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Tworzy plik bazy (jeśli nie istnieje) i inicjalizuje schemat."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()
    logger.info("Baza danych gotowa: %s", db_path)
    return conn


def save_practitioner(conn: sqlite3.Connection, p: Practitioner) -> None:
    """Zapisuje (lub aktualizuje) profil praktyka w bazie."""
    conn.execute(
        UPSERT_SQL,
        (
            p.profile_url,
            p.name,
            json.dumps(p.specializations, ensure_ascii=False),
            p.email,
            p.phone,
            p.website,
            json.dumps(p.social_links, ensure_ascii=False),
            p.city,
            p.country,
            p.address,
            p.bio,
            p.raw_json,
        ),
    )
    conn.commit()
    logger.debug("Zapisano: %s", p.profile_url)
