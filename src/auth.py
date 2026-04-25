"""Logowanie do Heallist przez Playwright."""
from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import Page, BrowserContext

logger = logging.getLogger(__name__)

AUTH_STATE_PATH = Path("data/auth_state.json")

# Możliwe adresy strony logowania (agent sprawdzi oba)
LOGIN_URLS = [
    "https://app.heallist.com/login",
    "https://app.heallist.com/sign-in",
]

# Selektor elementu świadczącego o pomyślnym zalogowaniu
# TODO: zaktualizuj jeśli Heallist zmieni strukturę DOM
LOGGED_IN_SELECTORS = [
    "[data-testid='user-menu']",
    "nav [href*='/network']",
    "header img[alt*='avatar']",
    ".user-avatar",
    "[class*='UserMenu']",
    "[class*='userMenu']",
    "[class*='ProfileMenu']",
    "a[href*='/dashboard']",
    "a[href*='/profile']",
]


async def login(
    context: BrowserContext,
    email: str,
    password: str,
) -> Page:
    """
    Loguje się do Heallist.

    1. Jeśli istnieje zapisany stan sesji i sesja jest wciąż ważna — pomija logowanie.
    2. W przeciwnym razie otwiera stronę logowania, wypełnia formularz i zapisuje nowy stan.

    Zwraca aktywną stronę przegotowaną do dalszej pracy.
    """
    page = await context.new_page()

    # Sprawdź czy sesja zapisana jest wciąż ważna
    if AUTH_STATE_PATH.exists():
        logger.info("Znaleziono zapisany stan sesji, próbuję go użyć…")
        await page.goto("https://app.heallist.com/network/practitioners", wait_until="domcontentloaded")
        if await _is_logged_in(page):
            logger.info("Sesja ważna — pomijam logowanie.")
            return page
        logger.info("Sesja wygasła — loguję od nowa.")

    # Próba logowania pod różnymi adresami
    login_url = await _find_login_url(page)
    logger.info("Strona logowania: %s", login_url)
    await page.goto(login_url, wait_until="networkidle")

    # Wypełnienie formularza
    await _fill_login_form(page, email, password)

    # Czekamy na zalogowanie
    try:
        await page.wait_for_url(
            lambda url: "/login" not in url and "/sign-in" not in url,
            timeout=30_000,
        )
    except Exception:
        pass  # niektóre SPA nie zmieniają URL

    # Dodatkowa weryfikacja
    if not await _is_logged_in(page):
        raise RuntimeError(
            "Logowanie nie powiodło się. Sprawdź dane w pliku .env:\n"
            "  HEALLIST_EMAIL — adres e-mail\n"
            "  HEALLIST_PASSWORD — hasło\n"
            "Uruchom z flagą --headful żeby zobaczyć co się dzieje w przeglądarce."
        )

    # Zapisz stan sesji
    AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(AUTH_STATE_PATH))
    logger.info("Stan sesji zapisano: %s", AUTH_STATE_PATH)
    return page


async def _find_login_url(page: Page) -> str:
    """Sprawdza który z kandydatów to rzeczywista strona logowania."""
    for url in LOGIN_URLS:
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            if resp and resp.ok:
                return url
        except Exception:
            continue
    # Fallback — wracamy do pierwszego
    return LOGIN_URLS[0]


async def _fill_login_form(page: Page, email: str, password: str) -> None:
    """Wypełnia pola e-mail i hasła i klika submit."""
    # Czekamy na pojawienie się pola email
    # TODO: zaktualizuj selektor jeśli Heallist zmieni DOM
    email_selectors = [
        "input[type='email']",
        "input[name='email']",
        "input[placeholder*='email' i]",
        "input[placeholder*='Email' i]",
        "#email",
    ]
    password_selectors = [
        "input[type='password']",
        "input[name='password']",
        "#password",
    ]
    submit_selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Log in')",
        "button:has-text('Sign in')",
        "button:has-text('Zaloguj')",
    ]

    email_field = await _find_visible(page, email_selectors)
    password_field = await _find_visible(page, password_selectors)

    if not email_field or not password_field:
        raise RuntimeError(
            "Nie znaleziono pól formularza logowania. "
            "Uruchom z --headful i sprawdź selektory w auth.py."
        )

    await email_field.fill(email)
    await password_field.fill(password)

    submit = await _find_visible(page, submit_selectors)
    if submit:
        await submit.click()
    else:
        await password_field.press("Enter")

    # Poczekaj chwilę na reakcję SPA
    await page.wait_for_timeout(3_000)


async def _is_logged_in(page: Page) -> bool:
    """Zwraca True jeśli strona wskazuje na zalogowanego użytkownika."""
    for sel in LOGGED_IN_SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2_000):
                return True
        except Exception:
            continue
    # Dodatkowa heurystyka: jeśli URL nie zawiera login/sign-in to zakładamy sukces
    from urllib.parse import urlparse
    parsed = urlparse(page.url)
    is_heallist = parsed.hostname == "app.heallist.com"
    return "/login" not in parsed.path and "/sign-in" not in parsed.path and is_heallist


async def _find_visible(page: Page, selectors: list[str]):
    """Zwraca pierwszy widoczny element spośród podanych selektorów lub None."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                return el
        except Exception:
            continue
    return None
