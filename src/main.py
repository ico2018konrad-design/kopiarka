"""Punkt wejścia agenta: python -m src.main"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from .auth import login, AUTH_STATE_PATH
from .db import init_db, save_practitioner
from .scraper import get_profile_urls, scrape_profile

# ──────────────────────────────────────────────────────────────────────────────
# Konfiguracja logowania
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agent do pobierania profili praktyków z Heallist."
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Uruchom przeglądarkę w trybie widocznym (do debugowania).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Pobierz tylko pierwsze N profili (0 = brak limitu).",
    )
    parser.add_argument(
        "--skip-login",
        action="store_true",
        help="Pomiń logowanie (użyj zapisanego stanu sesji).",
    )
    parser.add_argument(
        "--reset-session",
        action="store_true",
        help="Usuń zapisany stan sesji i zaloguj się od nowa.",
    )
    return parser.parse_args()


# ──────────────────────────────────────────────────────────────────────────────
# GŁÓWNA FUNKCJA
# ──────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    args = parse_args()

    # Wczytaj .env
    load_dotenv()
    email = os.getenv("HEALLIST_EMAIL", "")
    password = os.getenv("HEALLIST_PASSWORD", "")
    headless_env = os.getenv("HEADLESS", "true").lower() not in ("false", "0", "no")
    headless = headless_env and not args.headful

    if not email or not password:
        sys.exit(
            "BŁĄD: Brak danych logowania.\n"
            "Utwórz plik .env na podstawie .env.example i uzupełnij:\n"
            "  HEALLIST_EMAIL=twoj@email.com\n"
            "  HEALLIST_PASSWORD=twoje-haslo"
        )

    # Opcjonalnie wyczyść sesję
    if args.reset_session and AUTH_STATE_PATH.exists():
        AUTH_STATE_PATH.unlink()
        logger.info("Usunięto zapisaną sesję.")

    # Inicjalizacja bazy danych
    conn = init_db()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)

        # Załaduj zapisany stan sesji jeśli istnieje
        storage_state = str(AUTH_STATE_PATH) if AUTH_STATE_PATH.exists() else None
        context = await browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        # Logowanie
        page = await login(context, email, password)

        # Pobierz listę profili
        profile_urls = await get_profile_urls(page)

        if not profile_urls:
            logger.error(
                "Nie znaleziono żadnych profili. "
                "Uruchom z --headful i sprawdź selektory w scraper.py."
            )
            await browser.close()
            conn.close()
            return

        if args.limit:
            profile_urls = profile_urls[: args.limit]
            logger.info("Ograniczono do %d profili (--limit).", args.limit)

        total = len(profile_urls)
        logger.info("Do pobrania: %d profili.", total)

        # Pobierz każdy profil i zapisz do bazy
        ok = 0
        for idx, url in enumerate(profile_urls, 1):
            practitioner = await scrape_profile(page, url)
            if practitioner:
                save_practitioner(conn, practitioner)
                location = practitioner.city or practitioner.country or "?"
                logger.info(
                    "[%d/%d] Zapisano: %s (%s)",
                    idx, total,
                    practitioner.name or url,
                    location,
                )
                ok += 1
            else:
                logger.warning("[%d/%d] Pominięto: %s", idx, total, url)

        logger.info("Gotowe. Zapisano %d/%d profili.", ok, total)

        # Zapisz ostateczny stan sesji
        AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(AUTH_STATE_PATH))

        await browser.close()

    conn.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
