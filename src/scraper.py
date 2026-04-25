"""Pobieranie listy praktyków i szczegółów ich profili."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page, Request, Response

from .models import Practitioner

logger = logging.getLogger(__name__)

BASE_URL = "https://app.heallist.com"
PRACTITIONERS_URL = f"{BASE_URL}/network/practitioners"

# ──────────────────────────────────────────────────────────────────────────────
# KONFIGURACJA SELEKTORÓW (zaktualizuj po pierwszym uruchomieniu z --headful)
# ──────────────────────────────────────────────────────────────────────────────

# Selektor karty / linku do profilu na stronie listy
# TODO: zaktualizuj jeśli Heallist zmieni DOM
PROFILE_CARD_SELECTORS = [
    "a[href*='/practitioners/']",
    "a[href*='/profile/']",
    "a[href*='/practitioner/']",
    "[class*='PractitionerCard'] a",
    "[class*='practitioner-card'] a",
    "[class*='ProfileCard'] a",
    "[data-testid*='practitioner'] a",
]

# Selektory na stronie profilu
# TODO: zaktualizuj jeśli Heallist zmieni DOM
PROFILE_SELECTORS = {
    "name": [
        "h1",
        "[class*='PractitionerName']",
        "[class*='practitioner-name']",
        "[class*='ProfileName']",
        "[data-testid='name']",
    ],
    "bio": [
        "[class*='Bio']",
        "[class*='bio']",
        "[class*='About']",
        "[class*='about']",
        "[data-testid='bio']",
        "p[class*='description']",
    ],
    "specializations": [
        "[class*='Specialization']",
        "[class*='specialization']",
        "[class*='Modality']",
        "[class*='modality']",
        "[class*='Tag']",
        "[class*='tag']",
        "[data-testid*='specialization']",
    ],
    "email": [
        "a[href^='mailto:']",
        "[data-testid='email']",
    ],
    "phone": [
        "a[href^='tel:']",
        "[data-testid='phone']",
        "[class*='phone']",
        "[class*='Phone']",
    ],
    "website": [
        "a[href^='http'][rel~='noopener']:not([href*='heallist'])",
        "[data-testid='website']",
    ],
    "city": [
        "[class*='City']",
        "[class*='city']",
        "[class*='Location']",
        "[class*='location']",
        "[data-testid='city']",
        "[data-testid='location']",
    ],
    "country": [
        "[class*='Country']",
        "[class*='country']",
        "[data-testid='country']",
    ],
}

# Domeny social media
SOCIAL_DOMAINS = [
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "tiktok.com", "pinterest.com",
    "telegram.me", "t.me",
]


# ──────────────────────────────────────────────────────────────────────────────
# GŁÓWNA LOGIKA SCRAPOWANIA
# ──────────────────────────────────────────────────────────────────────────────

async def get_profile_urls(page: Page) -> list[str]:
    """
    Otwiera stronę listy praktyków i zbiera linki do wszystkich profili.

    Strategia 1 (preferowana): przechwytuje odpowiedzi API Heallist.
    Strategia 2 (fallback): parsuje DOM + infinite scroll.
    """
    logger.info("Wchodzę na listę praktyków: %s", PRACTITIONERS_URL)
    await page.goto(PRACTITIONERS_URL, wait_until="domcontentloaded")

    # Spróbuj wykryć API
    api_urls = await _try_intercept_list_api(page)
    if api_urls:
        logger.info("Znaleziono %d profili przez API.", len(api_urls))
        return api_urls

    # Fallback: DOM + scroll
    logger.info("Nie wykryto API — scrapuję DOM z infinite scrollem.")
    return await _scrape_list_dom(page)


async def _try_intercept_list_api(page: Page) -> list[str]:
    """
    Nasłuchuje na odpowiedzi sieciowe zawierające listę praktyków (JSON).
    Zwraca listę URL-i profili lub pustą listę jeśli nie wykryto API.
    """
    collected_urls: list[str] = []
    api_detected = asyncio.Event()

    async def handle_response(response: Response) -> None:
        url = response.url
        # Interesują nas tylko JSONy z API Heallist
        if "heallist.com" not in url:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            data = await response.json()
        except Exception:
            return

        urls = _extract_profile_urls_from_json(data)
        if urls:
            collected_urls.extend(urls)
            logger.debug("API: znaleziono %d URL-i w %s", len(urls), url)
            api_detected.set()

    page.on("response", handle_response)

    # Przewiń stronę żeby wyzwolić zapytania API
    await _infinite_scroll(page, max_scrolls=5, scroll_pause=2.0)

    page.remove_listener("response", handle_response)

    if not collected_urls:
        return []

    # Jeśli API zwraca tylko fragmenty (paginacja), kontynuuj scrol/paginację
    # i zbieraj kolejne odpowiedzi
    await _continue_scroll_and_collect(page, collected_urls)

    return list(dict.fromkeys(collected_urls))  # deduplikacja z zachowaniem kolejności


async def _continue_scroll_and_collect(page: Page, collected_urls: list[str]) -> None:
    """Kontynuuje przewijanie i zbieranie URLi z API."""
    prev_count = 0
    stale_rounds = 0

    async def handle(response: Response) -> None:
        if "heallist.com" not in response.url:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            data = await response.json()
        except Exception:
            return
        urls = _extract_profile_urls_from_json(data)
        collected_urls.extend(urls)

    page.on("response", handle)

    while stale_rounds < 4:
        await _scroll_once(page)
        await asyncio.sleep(2.5)
        current = len(set(collected_urls))
        if current == prev_count:
            stale_rounds += 1
        else:
            stale_rounds = 0
            prev_count = current
        logger.debug("Zebrano do tej pory %d URL-i (stale_rounds=%d)", current, stale_rounds)

    page.remove_listener("response", handle)


def _extract_profile_urls_from_json(data) -> list[str]:
    """Rekurencyjnie szuka URL-i profili w strukturze JSON."""
    urls: list[str] = []

    def recurse(obj):
        if isinstance(obj, dict):
            # Szukaj pól url/slug/username wskazujących na profil
            for key in ("url", "profileUrl", "profile_url", "slug", "username", "href", "link"):
                val = obj.get(key, "")
                if isinstance(val, str) and _is_profile_url(val):
                    urls.append(_normalize_url(val))
            for v in obj.values():
                recurse(v)
        elif isinstance(obj, list):
            for item in obj:
                recurse(item)

    recurse(data)
    return urls


def _is_profile_url(url: str) -> bool:
    """Sprawdza czy string wygląda jak URL profilu praktyka."""
    patterns = [
        r"/practitioners?/",
        r"/profile/",
        r"/practitioner/",
        r"/users?/",
    ]
    return any(re.search(p, url) for p in patterns)


def _normalize_url(url: str) -> str:
    """Zamienia względny URL na bezwzględny."""
    if url.startswith("http"):
        return url
    return urljoin(BASE_URL, url)


async def _scrape_list_dom(page: Page) -> list[str]:
    """Scrapuje linki do profili z DOM-u z infinite scrollem (fallback)."""
    await _infinite_scroll(page, max_scrolls=200, scroll_pause=1.5)
    return await _collect_profile_links(page)


async def _collect_profile_links(page: Page) -> list[str]:
    """Zbiera linki do profili z DOM-u."""
    urls: list[str] = []
    for sel in PROFILE_CARD_SELECTORS:
        elements = await page.locator(sel).all()
        for el in elements:
            href = await el.get_attribute("href")
            if href and _is_profile_url(href):
                urls.append(_normalize_url(href))
        if urls:
            break
    urls = list(dict.fromkeys(urls))
    logger.info("DOM: znaleziono %d linków do profili.", len(urls))
    return urls


async def _infinite_scroll(page: Page, max_scrolls: int = 100, scroll_pause: float = 1.5) -> None:
    """Przewija stronę w dół aż przestaną się ładować nowe elementy."""
    prev_height: int = 0
    stale = 0
    for i in range(max_scrolls):
        await _scroll_once(page)
        await asyncio.sleep(scroll_pause)
        height = await page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            stale += 1
            if stale >= 3:
                logger.debug("Brak nowych elementów po %d przewinięciach.", i + 1)
                break
        else:
            stale = 0
            prev_height = height

    # Kliknij "Load more" jeśli istnieje
    load_more_sels = [
        "button:has-text('Load more')",
        "button:has-text('Załaduj więcej')",
        "button:has-text('Show more')",
        "[data-testid='load-more']",
    ]
    for sel in load_more_sels:
        try:
            btn = page.locator(sel)
            while await btn.is_visible(timeout=1_000):
                await btn.click()
                await asyncio.sleep(2)
        except Exception:
            pass


async def _scroll_once(page: Page) -> None:
    """Jedno przewinięcie do dołu strony."""
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")


# ──────────────────────────────────────────────────────────────────────────────
# SZCZEGÓŁY PROFILU
# ──────────────────────────────────────────────────────────────────────────────

async def scrape_profile(page: Page, profile_url: str, retries: int = 3) -> Optional[Practitioner]:
    """
    Pobiera szczegóły profilu praktyka.

    Strategia 1: przechwytuje odpowiedź API JSON dla profilu.
    Strategia 2: parsuje DOM.
    """
    for attempt in range(1, retries + 1):
        try:
            return await _scrape_profile_once(page, profile_url)
        except Exception as exc:
            wait = 2 ** attempt
            logger.warning(
                "Błąd pobierania profilu %s (próba %d/%d): %s. Retry za %ds.",
                profile_url, attempt, retries, exc, wait,
            )
            if attempt < retries:
                await asyncio.sleep(wait)
    logger.error("Pominięto profil po %d nieudanych próbach: %s", retries, profile_url)
    return None


async def _scrape_profile_once(page: Page, profile_url: str) -> Practitioner:
    """Jedna próba pobrania profilu."""
    api_data: dict = {}

    async def handle_response(response: Response) -> None:
        url = response.url
        if "heallist.com" not in url:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        # Szukamy odpowiedzi dotyczącej konkretnego profilu
        path = urlparse(profile_url).path.rstrip("/").split("/")[-1]
        if path and path in url:
            try:
                data = await response.json()
                if isinstance(data, dict) and data:
                    api_data.update(data)
            except Exception:
                pass

    page.on("response", handle_response)
    await page.goto(profile_url, wait_until="networkidle", timeout=30_000)
    page.remove_listener("response", handle_response)

    # Jeśli przechwycono dane API — parsuj je
    if api_data:
        p = _parse_api_profile(api_data, profile_url)
        p.raw_json = json.dumps(api_data, ensure_ascii=False)
        return p

    # Fallback: parsowanie DOM
    return await _parse_dom_profile(page, profile_url)


def _parse_api_profile(data: dict, profile_url: str) -> Practitioner:
    """Parsuje profil z danych JSON API."""

    def get(*keys):
        """Pobiera wartość z zagnieżdżonej struktury wg listy kluczy."""
        for key in keys:
            if key in data:
                return data[key]
        return None

    # Imię i nazwisko
    name = (
        get("name", "fullName", "full_name", "displayName", "display_name")
        or f"{get('firstName', 'first_name') or ''} {get('lastName', 'last_name') or ''}".strip()
        or None
    )

    # Specjalizacje
    specs_raw = get("specializations", "modalities", "tags", "services")
    specializations: list[str] = []
    if isinstance(specs_raw, list):
        for s in specs_raw:
            if isinstance(s, str):
                specializations.append(s)
            elif isinstance(s, dict):
                specializations.append(
                    s.get("name") or s.get("title") or s.get("label") or str(s)
                )

    # Lokalizacja
    location = get("location", "address") or {}
    if isinstance(location, str):
        city = location
        country = None
        address = location
    else:
        city = (
            get("city")
            or (location.get("city") if isinstance(location, dict) else None)
        )
        country = (
            get("country")
            or (location.get("country") if isinstance(location, dict) else None)
        )
        address = (
            get("address", "fullAddress", "full_address")
            or (location.get("address") if isinstance(location, dict) else None)
        )

    # Social links
    social_links: dict = {}
    links_raw = get("socialLinks", "social_links", "links", "socials") or {}
    if isinstance(links_raw, dict):
        for k, v in links_raw.items():
            if v:
                social_links[k] = v
    elif isinstance(links_raw, list):
        for item in links_raw:
            if isinstance(item, dict):
                platform = item.get("platform") or item.get("type") or item.get("name")
                url = item.get("url") or item.get("href") or item.get("link")
                if platform and url:
                    social_links[platform] = url

    return Practitioner(
        profile_url=profile_url,
        name=name,
        specializations=specializations,
        email=get("email", "contactEmail", "contact_email"),
        phone=get("phone", "phoneNumber", "phone_number", "mobile"),
        website=get("website", "websiteUrl", "website_url", "url"),
        social_links=social_links,
        city=city,
        country=country,
        address=address,
        bio=get("bio", "about", "description", "summary"),
    )


async def _parse_dom_profile(page: Page, profile_url: str) -> Practitioner:
    """Parsuje dane profilu z DOM-u strony."""
    p = Practitioner(profile_url=profile_url)

    # Imię
    for sel in PROFILE_SELECTORS["name"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2_000):
                p.name = (await el.inner_text()).strip()
                break
        except Exception:
            pass

    # Bio
    for sel in PROFILE_SELECTORS["bio"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                p.bio = (await el.inner_text()).strip()
                break
        except Exception:
            pass

    # Specjalizacje (może być wiele elementów)
    for sel in PROFILE_SELECTORS["specializations"]:
        try:
            els = await page.locator(sel).all()
            texts = []
            for el in els:
                if await el.is_visible(timeout=500):
                    texts.append((await el.inner_text()).strip())
            if texts:
                p.specializations = texts
                break
        except Exception:
            pass

    # Email
    for sel in PROFILE_SELECTORS["email"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                href = await el.get_attribute("href") or ""
                p.email = href.replace("mailto:", "").strip() or (await el.inner_text()).strip()
                break
        except Exception:
            pass

    # Telefon
    for sel in PROFILE_SELECTORS["phone"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                href = await el.get_attribute("href") or ""
                p.phone = href.replace("tel:", "").strip() or (await el.inner_text()).strip()
                break
        except Exception:
            pass

    # Strona www
    for sel in PROFILE_SELECTORS["website"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                p.website = await el.get_attribute("href") or (await el.inner_text()).strip()
                break
        except Exception:
            pass

    # Miasto
    for sel in PROFILE_SELECTORS["city"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                p.city = (await el.inner_text()).strip()
                break
        except Exception:
            pass

    # Kraj
    for sel in PROFILE_SELECTORS["country"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1_500):
                p.country = (await el.inner_text()).strip()
                break
        except Exception:
            pass

    # Social media — szukaj linków do znanych platform
    social_links: dict = {}
    try:
        all_links = await page.locator("a[href]").all()
        for link in all_links:
            href = await link.get_attribute("href") or ""
            for domain in SOCIAL_DOMAINS:
                if domain in href:
                    platform = domain.split(".")[0]
                    social_links[platform] = href
                    break
        if social_links:
            p.social_links = social_links
    except Exception:
        pass

    # Zrzut HTML jako raw_json (przechowujemy surowy HTML)
    try:
        html_content = await page.content()
        p.raw_json = json.dumps(
            {"url": profile_url, "html_snippet": html_content[:5000]},
            ensure_ascii=False,
        )
    except Exception:
        pass

    return p
