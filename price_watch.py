#!/usr/bin/env python3
"""
price_watch.py – Monitor cen nart Rossignol ARCADE82 LTD (RROFY08, 176 cm)
w polskich sklepach internetowych.

Użycie:
    python price_watch.py            # normalny run
    python price_watch.py --dry-run  # nie zapisuje state.json
    python price_watch.py --test     # uruchamia testy jednostkowe
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Konfiguracja
# ---------------------------------------------------------------------------

TARGET_LENGTH = "176"
TARGET_SKU = "RROFY08"
STATE_FILE = Path("state.json")
SITES_FILE = Path("sites.json")
REQUEST_TIMEOUT = 15  # sekund
MAX_RETRIES = 2
RETRY_DELAY = 1.5  # sekund (mnożone przez numer próby)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("price_watch")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}

# ---------------------------------------------------------------------------
# Dataclass wynikowy
# ---------------------------------------------------------------------------


@dataclass
class ScrapeResult:
    url: str
    name: str
    price: float | None = None          # PLN
    availability: str = "unknown"       # "in_stock" | "out_of_stock" | "unknown"
    variant_confirmed: bool = False     # True jeśli wariant 176 potwierdzony
    sku_confirmed: bool = False         # True jeśli RROFY08 potwierdzony
    raw_price_str: str | None = None
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_state_dict(self) -> dict:
        """Zwraca tylko pola zapisywane w state.json."""
        return {
            "price": self.price,
            "availability": self.availability,
            "variant_confirmed": self.variant_confirmed,
            "sku_confirmed": self.sku_confirmed,
            "timestamp": self.timestamp,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Parsowanie ceny
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(
    r"[\d\u00a0\s]+[,.][\d]{2}",  # np. "1 749,99" lub "1749.99"
)


def parse_price(raw: str | None) -> float | None:
    """
    Parsuje cenę z różnych formatów polskich sklepów:
      "1 749,99 zł", "1\u00a0749,99\u00a0zł", "2120.00", "1749,99"
    Zwraca float lub None.
    """
    if not raw:
        return None
    # Usuń walutę i inne znaki
    s = raw.strip()
    s = re.sub(r"[zł ZŁPLNpln]", "", s)
    # Zamień NBSP i spacje na nic (separator tysięcy)
    s = s.replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    # Zamień przecinek dziesiętny na kropkę
    s = s.replace(",", ".")
    # Usuń wszystko poza cyframi i kropką
    s = re.sub(r"[^\d.]", "", s)
    # Jeśli jest więcej niż jedna kropka, zostaw tylko ostatnią jako dziesiętną
    parts = s.split(".")
    if len(parts) > 2:
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        val = float(s)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def extract_price_from_text(text: str) -> float | None:
    """Szuka wzorca ceny w surowym tekście HTML (fallback regex)."""
    matches = _PRICE_RE.findall(text)
    for m in matches:
        p = parse_price(m)
        if p and p > 100:  # filtruj śmieci
            return p
    return None


# ---------------------------------------------------------------------------
# HTTP helper z retry
# ---------------------------------------------------------------------------


def fetch(
    url: str,
    session: requests.Session,
    retries: int = MAX_RETRIES,
    timeout: int = REQUEST_TIMEOUT,
    extra_headers: dict | None = None,
) -> tuple[requests.Response | None, str | None]:
    """
    Pobiera URL z retry i backoff.
    Zwraca (Response, None) lub (None, error_str).
    """
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    last_error = "unknown"
    for attempt in range(1, retries + 2):
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp, None
            elif resp.status_code == 403:
                last_error = f"HTTP 403 Forbidden (bot protection)"
                log.warning("Próba %d/%d: %s → %s", attempt, retries + 1, url, last_error)
            elif resp.status_code == 404:
                return None, f"HTTP 404 Not Found"
            else:
                last_error = f"HTTP {resp.status_code}"
                log.warning("Próba %d/%d: %s → %s", attempt, retries + 1, url, last_error)
        except requests.exceptions.Timeout:
            last_error = "Timeout"
            log.warning("Próba %d/%d: %s → Timeout", attempt, retries + 1, url)
        except requests.exceptions.ConnectionError as e:
            last_error = f"ConnectionError: {e}"
            log.warning("Próba %d/%d: %s → %s", attempt, retries + 1, url, last_error)
        except Exception as e:
            last_error = str(e)
            log.warning("Próba %d/%d: %s → %s", attempt, retries + 1, url, last_error)

        if attempt <= retries:
            sleep_time = RETRY_DELAY * attempt
            log.info("Czekam %.1fs przed kolejną próbą...", sleep_time)
            time.sleep(sleep_time)

    return None, last_error


# ---------------------------------------------------------------------------
# Parser: Shopify (product.json endpoint)
# ---------------------------------------------------------------------------


def _shopify_product_json_url(page_url: str) -> str:
    """
    Zamienia URL strony produktu Shopify na URL endpointu product.json.
    np. https://shop.pl/products/slug → https://shop.pl/products/slug.json
    """
    parsed = urlparse(page_url)
    path = parsed.path.rstrip("/")
    if not path.endswith(".json"):
        path += ".json"
    return parsed._replace(path=path, query="", fragment="").geturl()


def _shopify_availability(variant: dict) -> str:
    """
    Próbuje określić dostępność wariantu Shopify.
    Publiczny endpoint product.json nie zawsze zwraca pole 'available'.
    """
    if "available" in variant:
        return "in_stock" if variant["available"] else "out_of_stock"
    # Heurystyka: jeśli inventory_management jest ustawione, sklep śledzi stany
    # ale nie wiemy ile jest – oznaczamy jako unknown
    return "unknown"


def parse_shopify(site: dict, session: requests.Session) -> ScrapeResult:
    """
    Parser dla sklepów Shopify.
    Używa endpointu /products/<handle>.json do pobrania wariantu 176.
    """
    url = site["url"]
    name = site.get("name", url)
    result = ScrapeResult(url=url, name=name)

    json_url = _shopify_product_json_url(url)
    log.info("[%s] Shopify JSON: %s", name, json_url)

    resp, err = fetch(json_url, session)
    if err:
        result.error = err
        log.error("[%s] Błąd pobierania: %s", name, err)
        return result

    try:
        data = resp.json()
    except Exception as e:
        result.error = f"JSON parse error: {e}"
        return result

    product = data.get("product", {})

    # Sprawdź SKU / tytuł / tagi / handle (slug URL)
    title = product.get("title", "")
    tags = " ".join(product.get("tags", []) if isinstance(product.get("tags"), list) else [product.get("tags", "")])
    handle = product.get("handle", "")
    sku_hint = site.get("sku_hint", TARGET_SKU)
    searchable = (title + " " + tags + " " + handle).upper()
    if sku_hint.upper() in searchable:
        result.sku_confirmed = True

    # Znajdź wariant 176
    variants = product.get("variants", [])
    target_variant = None
    for v in variants:
        # Sprawdź opcje: option1, option2, option3 lub title
        options = [
            str(v.get("option1", "")),
            str(v.get("option2", "")),
            str(v.get("option3", "")),
            str(v.get("title", "")),
        ]
        if TARGET_LENGTH in options:
            target_variant = v
            break

    if target_variant is None:
        result.error = f"Wariant {TARGET_LENGTH} nie znaleziony w product.json"
        log.warning("[%s] %s", name, result.error)
        return result

    result.variant_confirmed = True

    # Cena
    raw_price = target_variant.get("price", "")
    result.raw_price_str = raw_price
    result.price = parse_price(str(raw_price))

    # Dostępność
    result.availability = _shopify_availability(target_variant)

    # Dodatkowe sprawdzenie SKU przez pole sku wariantu
    variant_sku = target_variant.get("sku") or ""
    if sku_hint.upper() in variant_sku.upper():
        result.sku_confirmed = True

    log.info(
        "[%s] Wariant %s: cena=%s PLN, dostępność=%s, SKU=%s",
        name, TARGET_LENGTH, result.price, result.availability, result.sku_confirmed
    )
    return result


# ---------------------------------------------------------------------------
# Parser: JSON-LD / Schema.org (ogólny)
# ---------------------------------------------------------------------------


def _extract_jsonld_product(soup: BeautifulSoup) -> list[dict]:
    """Zwraca listę obiektów JSON-LD typu Product ze strony."""
    results = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    results.append(item)
        elif isinstance(data, dict):
            if data.get("@type") == "Product":
                results.append(data)
            # Obsługa @graph
            for node in data.get("@graph", []):
                if isinstance(node, dict) and node.get("@type") == "Product":
                    results.append(node)
    return results


def _parse_jsonld_offers(products: list[dict]) -> tuple[float | None, str, bool]:
    """
    Szuka oferty dla wariantu 176 w JSON-LD.
    Zwraca (cena, dostępność, variant_confirmed).
    """
    availability_map = {
        "InStock": "in_stock",
        "http://schema.org/InStock": "in_stock",
        "https://schema.org/InStock": "in_stock",
        "OutOfStock": "out_of_stock",
        "http://schema.org/OutOfStock": "out_of_stock",
        "https://schema.org/OutOfStock": "out_of_stock",
        "LimitedAvailability": "in_stock",
        "PreOrder": "out_of_stock",
    }

    for product in products:
        offers = product.get("offers", [])
        if isinstance(offers, dict):
            offers = [offers]

        # Szukaj oferty z wariantem 176
        for offer in offers:
            offer_name = str(offer.get("name", ""))
            offer_sku = str(offer.get("sku", ""))
            # Sprawdź czy oferta dotyczy wariantu 176
            if TARGET_LENGTH in offer_name or TARGET_LENGTH in offer_sku:
                price = parse_price(str(offer.get("price", "")))
                avail_raw = offer.get("availability", "")
                avail = availability_map.get(avail_raw, "unknown")
                return price, avail, True

        # Jeśli tylko jedna oferta (bez wariantów), weź ją
        if len(offers) == 1:
            offer = offers[0]
            price = parse_price(str(offer.get("price", "")))
            avail_raw = offer.get("availability", "")
            avail = availability_map.get(avail_raw, "unknown")
            return price, avail, False  # variant_confirmed=False – nie wiemy czy to 176

    return None, "unknown", False


def _parse_meta_price(soup: BeautifulSoup) -> float | None:
    """Szuka ceny w meta tagach og:price:amount / product:price:amount."""
    for prop in ("product:price:amount", "og:price:amount"):
        tag = soup.find("meta", property=prop)
        if tag and tag.get("content"):
            p = parse_price(tag["content"])
            if p:
                return p
    return None


# ---------------------------------------------------------------------------
# Parser: Intersport.pl
# ---------------------------------------------------------------------------


def parse_intersport(site: dict, session: requests.Session) -> ScrapeResult:
    """
    Parser dla Intersport.pl.
    Próbuje: JSON-LD → meta tagi → regex HTML.
    Intersport może blokować boty (403), wtedy oznacza error=blocked_403.
    """
    url = site["url"]
    name = site.get("name", url)
    result = ScrapeResult(url=url, name=name)

    log.info("[%s] Pobieranie: %s", name, url)
    resp, err = fetch(url, session)
    if err:
        result.error = err
        log.error("[%s] Błąd: %s", name, err)
        return result

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    # Sprawdź SKU w treści strony
    sku_hint = site.get("sku_hint", TARGET_SKU)
    if sku_hint.upper() in html.upper():
        result.sku_confirmed = True

    # 1. Próba JSON-LD
    products = _extract_jsonld_product(soup)
    if products:
        price, avail, variant_confirmed = _parse_jsonld_offers(products)
        if price:
            result.price = price
            result.availability = avail
            result.variant_confirmed = variant_confirmed
            log.info("[%s] Cena z JSON-LD: %s PLN, dostępność: %s", name, price, avail)
            return result

    # 2. Próba meta tagów
    meta_price = _parse_meta_price(soup)
    if meta_price:
        result.price = meta_price
        result.raw_price_str = str(meta_price)
        log.info("[%s] Cena z meta: %s PLN", name, meta_price)
        # Sprawdź dostępność przez meta
        avail_meta = soup.find("meta", property="product:availability")
        if avail_meta:
            av = avail_meta.get("content", "").lower()
            if "instock" in av or "in stock" in av:
                result.availability = "in_stock"
            elif "outofstock" in av or "out of stock" in av:
                result.availability = "out_of_stock"
        return result

    # 3. Fallback: regex w HTML – szukaj ceny blisko "176"
    # Znajdź fragment HTML zawierający "176" i szukaj ceny w okolicy
    idx = html.find(TARGET_LENGTH)
    if idx != -1:
        snippet = html[max(0, idx - 200): idx + 500]
        price = extract_price_from_text(snippet)
        if price:
            result.price = price
            result.variant_confirmed = True
            log.info("[%s] Cena z regex (blisko '176'): %s PLN", name, price)
            return result

    # 4. Ostateczny fallback: jakakolwiek cena na stronie
    price = extract_price_from_text(html)
    if price:
        result.price = price
        result.variant_confirmed = False  # nie wiemy czy to wariant 176
        log.info("[%s] Cena z regex (ogólna): %s PLN (wariant niepewny)", name, price)
        return result

    result.error = "Nie znaleziono ceny na stronie"
    log.warning("[%s] %s", name, result.error)
    return result


# ---------------------------------------------------------------------------
# Parser: Generic (fallback dla nieznanych sklepów)
# ---------------------------------------------------------------------------


def parse_generic(site: dict, session: requests.Session) -> ScrapeResult:
    """
    Ogólny parser dla nieznanych sklepów.
    Kolejność: JSON-LD → meta → regex.
    """
    url = site["url"]
    name = site.get("name", url)
    result = ScrapeResult(url=url, name=name)

    log.info("[%s] Generic parser: %s", name, url)
    resp, err = fetch(url, session)
    if err:
        result.error = err
        return result

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    sku_hint = site.get("sku_hint", TARGET_SKU)
    if sku_hint.upper() in html.upper():
        result.sku_confirmed = True

    # JSON-LD
    products = _extract_jsonld_product(soup)
    if products:
        price, avail, variant_confirmed = _parse_jsonld_offers(products)
        if price:
            result.price = price
            result.availability = avail
            result.variant_confirmed = variant_confirmed
            return result

    # Meta
    meta_price = _parse_meta_price(soup)
    if meta_price:
        result.price = meta_price
        return result

    # Regex
    price = extract_price_from_text(html)
    if price:
        result.price = price
        result.variant_confirmed = False

    return result


# ---------------------------------------------------------------------------
# Parser: SkiRaceCenter.pl (PrestaShop)
# ---------------------------------------------------------------------------


def parse_skiracecenter(site: dict, session: requests.Session) -> ScrapeResult:
    """
    Parser dla skiracecenter.pl (PrestaShop).
    Strona zawiera cenę i wariant 176 bezpośrednio w HTML.
    Kolejność: JSON-LD → meta → regex blisko '176'.
    """
    url = site["url"]
    name = site.get("name", url)
    result = ScrapeResult(url=url, name=name)

    log.info("[%s] Pobieranie: %s", name, url)
    resp, err = fetch(url, session)
    if err:
        result.error = err
        log.error("[%s] Błąd: %s", name, err)
        return result

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    sku_hint = site.get("sku_hint", TARGET_SKU)
    if sku_hint.upper() in html.upper():
        result.sku_confirmed = True

    # 1. JSON-LD
    products = _extract_jsonld_product(soup)
    if products:
        price, avail, variant_confirmed = _parse_jsonld_offers(products)
        if price:
            result.price = price
            result.availability = avail
            result.variant_confirmed = variant_confirmed
            log.info("[%s] Cena z JSON-LD: %s PLN, dostępność: %s", name, price, avail)
            return result

    # 2. Meta
    meta_price = _parse_meta_price(soup)
    if meta_price:
        result.price = meta_price
        log.info("[%s] Cena z meta: %s PLN", name, meta_price)
        return result

    # 3. PrestaShop: szukaj ceny w data-* atrybutach lub itemprop
    itemprop_price = soup.find(attrs={"itemprop": "price"})
    if itemprop_price:
        raw = itemprop_price.get("content") or itemprop_price.get_text()
        p = parse_price(raw)
        if p:
            result.price = p
            result.raw_price_str = raw
            # Sprawdź czy strona dotyczy wariantu 176
            if TARGET_LENGTH in html:
                result.variant_confirmed = True
            log.info("[%s] Cena z itemprop: %s PLN", name, p)
            return result

    # 4. Regex blisko "176"
    idx = html.find(TARGET_LENGTH)
    if idx != -1:
        snippet = html[max(0, idx - 300): idx + 600]
        price = extract_price_from_text(snippet)
        if price:
            result.price = price
            result.variant_confirmed = True
            log.info("[%s] Cena z regex (blisko '176'): %s PLN", name, price)
            return result

    # 5. Ogólny regex
    price = extract_price_from_text(html)
    if price:
        result.price = price
        result.variant_confirmed = False
        log.info("[%s] Cena z regex (ogólna): %s PLN (wariant niepewny)", name, price)
        return result

    result.error = "Nie znaleziono ceny na stronie"
    log.warning("[%s] %s", name, result.error)
    return result


# ---------------------------------------------------------------------------
# Parser: Allegro.pl
# ---------------------------------------------------------------------------


def parse_allegro(site: dict, session: requests.Session) -> ScrapeResult:
    """
    Parser dla Allegro.pl.
    Allegro renderuje ceny przez JS i blokuje boty.
    Próba: JSON-LD (czasem obecne w SSR) → meta → regex.
    Jeśli zablokowane → error z instrukcją Playwright.
    """
    url = site["url"]
    name = site.get("name", url)
    result = ScrapeResult(url=url, name=name)

    log.info("[%s] Pobieranie: %s", name, url)
    resp, err = fetch(url, session, extra_headers={"Referer": "https://allegro.pl/"})
    if err:
        result.error = f"{err} | Allegro wymaga Playwright – patrz README"
        log.warning("[%s] %s", name, result.error)
        return result

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    sku_hint = site.get("sku_hint", TARGET_SKU)
    if sku_hint.upper() in html.upper():
        result.sku_confirmed = True

    # JSON-LD (Allegro czasem wstrzykuje w SSR)
    products = _extract_jsonld_product(soup)
    if products:
        price, avail, variant_confirmed = _parse_jsonld_offers(products)
        if price:
            result.price = price
            result.availability = avail
            result.variant_confirmed = variant_confirmed
            log.info("[%s] Cena z JSON-LD: %s PLN", name, price)
            return result

    # Meta OG
    meta_price = _parse_meta_price(soup)
    if meta_price:
        result.price = meta_price
        if TARGET_LENGTH in html:
            result.variant_confirmed = True
        log.info("[%s] Cena z meta: %s PLN", name, meta_price)
        return result

    # Regex blisko "176"
    idx = html.find(TARGET_LENGTH)
    if idx != -1:
        snippet = html[max(0, idx - 300): idx + 600]
        price = extract_price_from_text(snippet)
        if price:
            result.price = price
            result.variant_confirmed = True
            log.info("[%s] Cena z regex: %s PLN", name, price)
            return result

    result.error = "Brak ceny w HTML (JS-rendered) – wymagany Playwright"
    log.warning("[%s] %s", name, result.error)
    return result


# ---------------------------------------------------------------------------
# Parser: Ceneo.pl (agregator cen)
# ---------------------------------------------------------------------------


def parse_ceneo(site: dict, session: requests.Session) -> ScrapeResult:
    """
    Parser dla Ceneo.pl.
    Ceneo to agregator – pokazuje najniższą cenę z wielu sklepów.
    Ceny są częściowo w SSR HTML (meta OG) i częściowo JS-rendered.
    Wynik: najniższa cena z meta/JSON-LD, variant_confirmed=False (ceneo nie filtruje po rozmiarze).
    """
    url = site["url"]
    name = site.get("name", url)
    result = ScrapeResult(url=url, name=name)

    log.info("[%s] Pobieranie: %s", name, url)
    resp, err = fetch(url, session)
    if err:
        result.error = err
        log.error("[%s] Błąd: %s", name, err)
        return result

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    # Ceneo: cena w og:description lub meta description (np. "od 1999,99 zł")
    desc_tag = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
    if desc_tag:
        desc = desc_tag.get("content", "")
        # Szukaj wzorca "od X zł" lub "X zł"
        m = re.search(r"od\s+([\d\s\u00a0]+[,.][\d]{2})\s*z", desc)
        if not m:
            m = re.search(r"([\d\s\u00a0]+[,.][\d]{2})\s*z", desc)
        if m:
            p = parse_price(m.group(1))
            if p:
                result.price = p
                result.raw_price_str = m.group(1)
                result.variant_confirmed = False  # Ceneo nie filtruje po rozmiarze
                result.sku_confirmed = True if TARGET_SKU.upper() in html.upper() else False
                log.info("[%s] Cena z og:description: %s PLN (agregator, wariant niepewny)", name, p)
                return result

    # JSON-LD
    products = _extract_jsonld_product(soup)
    if products:
        price, avail, variant_confirmed = _parse_jsonld_offers(products)
        if price:
            result.price = price
            result.availability = avail
            result.variant_confirmed = False  # agregator
            log.info("[%s] Cena z JSON-LD: %s PLN", name, price)
            return result

    # Meta price
    meta_price = _parse_meta_price(soup)
    if meta_price:
        result.price = meta_price
        result.variant_confirmed = False
        log.info("[%s] Cena z meta: %s PLN", name, meta_price)
        return result

    result.error = "Brak ceny w HTML (JS-rendered)"
    log.warning("[%s] %s", name, result.error)
    return result


# ---------------------------------------------------------------------------
# Parser: playwright_required (stub – gdy Playwright nie zainstalowany)
# ---------------------------------------------------------------------------


def parse_playwright_required(site: dict, session: requests.Session) -> ScrapeResult:
    """
    Stub dla sklepów wymagających Playwright.
    Loguje instrukcję i zwraca error bez alertu.
    """
    url = site["url"]
    name = site.get("name", url)
    result = ScrapeResult(url=url, name=name)
    result.error = (
        "Parser wymaga Playwright (JS-rendered). "
        "Zainstaluj: pip3 install playwright && "
        "/Users/kacpersaj/Library/Python/3.11/bin/playwright install chromium. "
        "Następnie zmień parser na 'playwright_generic' w sites.json."
    )
    log.warning("[%s] %s", name, result.error)
    return result


# ---------------------------------------------------------------------------
# Parser: playwright_generic (pełny – wymaga zainstalowanego Playwright)
# ---------------------------------------------------------------------------


def parse_playwright_generic(site: dict, session: requests.Session) -> ScrapeResult:
    """
    Parser Playwright dla stron renderowanych JS (Intersport, 8a.pl, Allegro itp.).
    Uruchamia headless Chromium, czeka na załadowanie strony, pobiera HTML,
    a następnie stosuje ten sam pipeline: JSON-LD → meta → itemprop → regex.

    Wymaga: pip3 install playwright && playwright install chromium
    """
    url = site["url"]
    name = site.get("name", url)
    result = ScrapeResult(url=url, name=name)
    sku_hint = site.get("sku_hint", TARGET_SKU)

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        result.error = "Playwright nie zainstalowany: pip3 install playwright"
        log.error("[%s] %s", name, result.error)
        return result

    log.info("[%s] Playwright: %s", name, url)

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/121.0.0.0 Safari/537.36"
                    ),
                    locale="pl-PL",
                    extra_http_headers={"Accept-Language": "pl-PL,pl;q=0.9"},
                )
                page = context.new_page()
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                # Poczekaj na załadowanie ceny (max 8s)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except PWTimeout:
                    pass  # Kontynuuj z tym co jest
                html = page.content()
                browser.close()
            break  # sukces
        except PWTimeout:
            log.warning("[%s] Playwright timeout (próba %d/%d)", name, attempt, MAX_RETRIES + 1)
            if attempt <= MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                result.error = "Playwright timeout po wszystkich próbach"
                return result
        except Exception as e:
            log.warning("[%s] Playwright błąd (próba %d/%d): %s", name, attempt, MAX_RETRIES + 1, e)
            if attempt <= MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                result.error = f"Playwright error: {e}"
                return result

    soup = BeautifulSoup(html, "html.parser")

    # SKU check
    if sku_hint.upper() in html.upper():
        result.sku_confirmed = True

    # 1. JSON-LD
    products = _extract_jsonld_product(soup)
    if products:
        price, avail, variant_confirmed = _parse_jsonld_offers(products)
        if price:
            result.price = price
            result.availability = avail
            result.variant_confirmed = variant_confirmed
            log.info("[%s] Playwright JSON-LD: %s PLN, dostępność: %s", name, price, avail)
            return result

    # 2. Meta
    meta_price = _parse_meta_price(soup)
    if meta_price:
        result.price = meta_price
        if TARGET_LENGTH in html:
            result.variant_confirmed = True
        log.info("[%s] Playwright meta: %s PLN", name, meta_price)
        return result

    # 3. itemprop=price
    itemprop_price = soup.find(attrs={"itemprop": "price"})
    if itemprop_price:
        raw = itemprop_price.get("content") or itemprop_price.get_text()
        p = parse_price(raw)
        if p:
            result.price = p
            result.raw_price_str = raw
            if TARGET_LENGTH in html:
                result.variant_confirmed = True
            log.info("[%s] Playwright itemprop: %s PLN", name, p)
            return result

    # 4. Regex blisko "176"
    idx = html.find(TARGET_LENGTH)
    if idx != -1:
        snippet = html[max(0, idx - 300): idx + 600]
        price = extract_price_from_text(snippet)
        if price:
            result.price = price
            result.variant_confirmed = True
            log.info("[%s] Playwright regex (blisko '176'): %s PLN", name, price)
            return result

    # 5. Ogólny regex
    price = extract_price_from_text(html)
    if price:
        result.price = price
        result.variant_confirmed = False
        log.info("[%s] Playwright regex (ogólna): %s PLN (wariant niepewny)", name, price)
        return result

    result.error = "Playwright: nie znaleziono ceny na stronie"
    log.warning("[%s] %s", name, result.error)
    return result


# ---------------------------------------------------------------------------
# Dispatcher parserów
# ---------------------------------------------------------------------------

PARSERS = {
    "shopify": parse_shopify,
    "intersport": parse_intersport,
    "skiracecenter": parse_skiracecenter,
    "allegro": parse_allegro,
    "ceneo": parse_ceneo,
    "playwright_required": parse_playwright_required,
    "playwright_generic": parse_playwright_generic,
    "generic": parse_generic,
}



def dispatch_parser(site: dict, session: requests.Session) -> ScrapeResult:
    """Wybiera odpowiedni parser na podstawie pola 'parser' w konfiguracji."""
    parser_name = site.get("parser", "generic").lower()
    parser_fn = PARSERS.get(parser_name, parse_generic)
    return parser_fn(site, session)



# ---------------------------------------------------------------------------
# Zarządzanie stanem (state.json)
# ---------------------------------------------------------------------------


def load_state(path: Path = STATE_FILE) -> dict:
    """Wczytuje poprzedni stan z pliku JSON."""
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning("Nie można wczytać state.json: %s", e)
    return {}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    """Zapisuje stan do pliku JSON."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        log.info("Stan zapisany do %s", path)
    except Exception as e:
        log.error("Nie można zapisać state.json: %s", e)


HISTORY_FILE = Path(__file__).parent / "history.json"


def save_history(results: list, sites: list) -> None:
    """
    Dołącza bieżące wyniki do history.json.
    Struktura: { "Nazwa sklepu": [ {timestamp, price, availability}, ... ] }
    Przechowuje max 500 wpisów na sklep.
    """
    name_map = {s["url"]: s.get("name", s["url"]) for s in sites}

    history: dict = {}
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}

    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        shop_name = name_map.get(r.url, r.url)
        if shop_name not in history:
            history[shop_name] = []
        history[shop_name].append({
            "timestamp": now,
            "price": r.price,
            "availability": r.availability,
            "error": r.error,
        })
        history[shop_name] = history[shop_name][-500:]

    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        log.info("Historia zapisana do %s", HISTORY_FILE)
    except Exception as e:
        log.error("Nie można zapisać history.json: %s", e)


# ---------------------------------------------------------------------------
# Wykrywanie zmian i raportowanie
# ---------------------------------------------------------------------------


def _fmt_price(p: float | None) -> str:
    if p is None:
        return "brak"
    return f"{p:,.2f} PLN".replace(",", " ").replace(".", ",")


def _fmt_avail(a: str) -> str:
    return {
        "in_stock": "✅ dostępny",
        "out_of_stock": "❌ niedostępny",
        "unknown": "❓ nieznana",
    }.get(a, a)


def detect_and_report_changes(
    results: list[ScrapeResult],
    old_state: dict,
) -> list[dict]:
    """
    Porównuje nowe wyniki ze starym stanem.
    Zwraca listę słowników opisujących zmiany (do zapisu i wydruku).
    """
    changes = []

    for r in results:
        old = old_state.get(r.url, {})

        old_price = old.get("price")
        old_avail = old.get("availability", "unknown")
        new_price = r.price
        new_avail = r.availability

        price_changed = old_price is not None and new_price is not None and abs(old_price - new_price) > 0.01
        avail_changed = old_avail != new_avail and old_avail != "unknown" and new_avail != "unknown"
        is_new = r.url not in old_state

        if r.error and not r.variant_confirmed and not r.sku_confirmed:
            log.info("[%s] Błąd/nieznany wariant – pomijam alert: %s", r.name, r.error)
            continue

        if is_new:
            changes.append({
                "type": "new",
                "result": r,
                "old_price": None,
                "old_avail": None,
            })
        elif price_changed or avail_changed:
            changes.append({
                "type": "change",
                "result": r,
                "old_price": old_price,
                "old_avail": old_avail,
                "price_changed": price_changed,
                "avail_changed": avail_changed,
            })

    return changes


# ---------------------------------------------------------------------------
# Powiadomienia
# ---------------------------------------------------------------------------


def _notify_macos(title: str, body: str) -> None:
    """Wysyła natywne powiadomienie macOS przez osascript."""
    import subprocess
    try:
        script = f'display notification "{body}" with title "{title}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception as e:
        log.warning("Powiadomienie macOS nie powiodło się: %s", e)


def _notify_email(subject: str, body: str) -> None:
    """
    Wysyła e-mail przez SMTP (Gmail).
    Wymaga zmiennych środowiskowych:
      PRICE_WATCH_EMAIL_TO   – adres odbiorcy
      PRICE_WATCH_EMAIL_FROM – adres nadawcy (Gmail)
      PRICE_WATCH_EMAIL_PASS – hasło aplikacji Gmail
    """
    import os, smtplib
    from email.mime.text import MIMEText

    to_addr   = os.environ.get("PRICE_WATCH_EMAIL_TO")
    from_addr = os.environ.get("PRICE_WATCH_EMAIL_FROM")
    password  = os.environ.get("PRICE_WATCH_EMAIL_PASS")

    if not (to_addr and from_addr and password):
        return  # e-mail nie skonfigurowany – pomijamy cicho

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_addr

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as smtp:
            smtp.login(from_addr, password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
        log.info("E-mail wysłany do %s", to_addr)
    except Exception as e:
        log.warning("Wysyłanie e-maila nie powiodło się: %s", e)


def _notify_ntfy(title: str, body: str) -> None:
    """
    Wysyła powiadomienie push przez ntfy.sh.
    Działa na iPhone, Android i Mac (aplikacja ntfy).
    Wymaga zmiennej środowiskowej:
      PRICE_WATCH_NTFY_TOPIC – nazwa kanału (np. price-watch-66831faf)
    Bez rejestracji – wystarczy zasubskrybować kanał w aplikacji ntfy.
    """
    import os
    topic = os.environ.get("PRICE_WATCH_NTFY_TOPIC")
    if not topic:
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": title.encode("ascii", "ignore").decode(),  # nagłówek HTTP: tylko ASCII
                "Priority": "high",
                "Tags": "ski,moneybag",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                log.info("ntfy push wysłany na kanał: %s", topic)
            else:
                log.warning("ntfy: nieoczekiwany status %d", resp.status)
    except Exception as e:
        log.warning("ntfy push nie powiodło się: %s", e)


def send_notifications(changes: list[dict]) -> None:
    """
    Wysyła powiadomienia o zmianach cen (obniżki i podwyżki).
    Powiadomienie macOS – zawsze.
    ntfy push – gdy skonfigurowany PRICE_WATCH_NTFY_TOPIC.
    E-mail – gdy skonfigurowane zmienne środowiskowe.
    """
    price_changes = [
        ch for ch in changes
        if ch.get("price_changed")
        and ch["result"].price is not None
        and ch.get("old_price") is not None
    ]

    if not price_changes:
        return

    drops = [ch for ch in price_changes if ch["result"].price < ch["old_price"]]
    increases = [ch for ch in price_changes if ch["result"].price > ch["old_price"]]

    lines = []
    for ch in price_changes:
        r: ScrapeResult = ch["result"]
        diff = r.price - ch["old_price"]
        pct = abs(diff) / ch["old_price"] * 100
        arrow = "📉" if diff < 0 else "📈"
        sign = "" if diff < 0 else "+"
        lines.append(
            f"{arrow} {r.name}: {_fmt_price(ch['old_price'])} → {_fmt_price(r.price)}"
            f" ({sign}{diff:.0f} zł, {pct:.1f}%)"
        )

    if drops and not increases:
        title = f"📉 Obniżka ceny! ({len(drops)} sklep{'y' if len(drops) > 1 else ''})"
    elif increases and not drops:
        title = f"📈 Podwyżka ceny! ({len(increases)} sklep{'y' if len(increases) > 1 else ''})"
    else:
        title = f"💰 Zmiany cen! ({len(drops)} ↓ / {len(increases)} ↑)"

    body = "\n".join(lines)

    log.info("Wysyłam powiadomienia o %d zmianie/ach cen", len(price_changes))
    _notify_macos(title, body)
    _notify_ntfy(title, body)
    _notify_email(f"[Price Watch] {title}", body)


def print_report(changes: list[dict], results: list[ScrapeResult]) -> None:
    """Drukuje czytelny raport zmian i podsumowanie."""
    print("\n" + "=" * 60)
    print("  PRICE WATCH – Rossignol ARCADE82 LTD (RROFY08, 176 cm)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    if not changes:
        print("\n✔  Brak zmian cen/dostępności.\n")
    else:
        for ch in changes:
            r: ScrapeResult = ch["result"]
            print(f"\n{'🆕 NOWY WPIS' if ch['type'] == 'new' else '🔔 ZMIANA'}: {r.name}")
            print(f"   URL: {r.url}")

            if ch["type"] == "new":
                print(f"   Cena:        {_fmt_price(r.price)}")
                print(f"   Dostępność:  {_fmt_avail(r.availability)}")
            else:
                if ch.get("price_changed"):
                    old_p = ch["old_price"]
                    new_p = r.price
                    diff_pct = ((new_p - old_p) / old_p * 100) if old_p else 0
                    sign = "+" if diff_pct >= 0 else ""
                    print(
                        f"   Cena:        {_fmt_price(old_p)} → {_fmt_price(new_p)}"
                        f"  ({sign}{diff_pct:.1f}%)"
                    )
                if ch.get("avail_changed"):
                    print(
                        f"   Dostępność:  {_fmt_avail(ch['old_avail'])} → {_fmt_avail(r.availability)}"
                    )

            variant_tag = "✓" if r.variant_confirmed else "?"
            sku_tag = "✓" if r.sku_confirmed else "?"
            print(f"   Wariant 176: [{variant_tag}]  SKU RROFY08: [{sku_tag}]")

    # Podsumowanie wszystkich sklepów
    print("\n" + "-" * 60)
    print("  PODSUMOWANIE WSZYSTKICH SKLEPÓW:")
    print("-" * 60)
    for r in results:
        status_icon = "✅" if r.availability == "in_stock" else ("❌" if r.availability == "out_of_stock" else "❓")
        price_str = _fmt_price(r.price)
        err_str = f" [BŁĄD: {r.error}]" if r.error else ""
        print(f"  {status_icon} {r.name:<25} {price_str:<18}{err_str}")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Testy jednostkowe
# ---------------------------------------------------------------------------


def run_tests() -> None:
    """Proste testy jednostkowe bez zewnętrznych zależności."""
    print("Uruchamiam testy jednostkowe...\n")
    failures = 0

    def assert_eq(desc: str, got, expected):
        nonlocal failures
        if got != expected:
            print(f"  ❌ FAIL [{desc}]: got={got!r}, expected={expected!r}")
            failures += 1
        else:
            print(f"  ✅ OK   [{desc}]")

    # parse_price
    assert_eq("1 749,99 zł", parse_price("1 749,99 zł"), 1749.99)
    assert_eq("1\u00a0749,99\u00a0zł", parse_price("1\u00a0749,99\u00a0zł"), 1749.99)
    assert_eq("2120.00", parse_price("2120.00"), 2120.00)
    assert_eq("2 499,00 PLN", parse_price("2 499,00 PLN"), 2499.00)
    assert_eq("brak ceny", parse_price("brak ceny"), None)
    assert_eq("None input", parse_price(None), None)
    assert_eq("empty string", parse_price(""), None)
    assert_eq("1749,99", parse_price("1749,99"), 1749.99)
    assert_eq("  1 200,00  ", parse_price("  1 200,00  "), 1200.00)

    # Shopify URL builder
    assert_eq(
        "shopify json url",
        _shopify_product_json_url("https://shop.pl/products/my-ski"),
        "https://shop.pl/products/my-ski.json",
    )
    assert_eq(
        "shopify json url trailing slash",
        _shopify_product_json_url("https://shop.pl/products/my-ski/"),
        "https://shop.pl/products/my-ski.json",
    )

    # Wykrywanie zmian
    old_state = {
        "https://example.com": {
            "price": 2499.00,
            "availability": "in_stock",
            "variant_confirmed": True,
            "sku_confirmed": True,
        }
    }
    r_change = ScrapeResult(
        url="https://example.com",
        name="Test Shop",
        price=2120.00,
        availability="in_stock",
        variant_confirmed=True,
        sku_confirmed=True,
    )
    changes = detect_and_report_changes([r_change], old_state)
    assert_eq("zmiana ceny wykryta", len(changes), 1)
    assert_eq("typ zmiany", changes[0]["type"], "change")
    assert_eq("stara cena", changes[0]["old_price"], 2499.00)

    r_no_change = ScrapeResult(
        url="https://example.com",
        name="Test Shop",
        price=2499.00,
        availability="in_stock",
        variant_confirmed=True,
        sku_confirmed=True,
    )
    changes2 = detect_and_report_changes([r_no_change], old_state)
    assert_eq("brak zmiany", len(changes2), 0)

    print(f"\n{'Wszystkie testy OK!' if failures == 0 else f'{failures} testów FAILED!'}\n")
    sys.exit(0 if failures == 0 else 1)


# ---------------------------------------------------------------------------
# Główna pętla
# ---------------------------------------------------------------------------


ITEMS_FILE = Path(__file__).parent / "items.json"


def load_sites(path: Path = SITES_FILE) -> list[dict]:
    """
    Wczytuje konfigurację sklepów.
    Obsługuje items.json (per-produkt) z fallbackiem na sites.json (płaska lista).
    """
    # Spróbuj items.json (nowy format per-produkt)
    items_path = ITEMS_FILE
    if items_path.exists():
        try:
            with open(items_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            # Flatten: zbierz wszystkie sklepy ze wszystkich produktów
            all_sites = []
            for item in items:
                for site in item.get("sites", []):
                    site_copy = dict(site)
                    # Dziedzicz sku_hint z produktu jeśli brak w sklepie
                    if item.get("sku_hint") and not site_copy.get("sku_hint"):
                        site_copy["sku_hint"] = item["sku_hint"]
                    all_sites.append(site_copy)
            if all_sites:
                log.info("Wczytano %d sklepów z items.json (%d produktów)", len(all_sites), len(items))
                return all_sites
        except Exception as e:
            log.warning("Błąd items.json: %s, próbuję sites.json", e)

    # Fallback na sites.json (stary format)
    try:
        with open(path, "r", encoding="utf-8") as f:
            sites = json.load(f)
        log.info("Wczytano %d sklepów z sites.json", len(sites))
        return sites
    except FileNotFoundError:
        log.error("Nie znaleziono pliku %s ani items.json", path)
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error("Błąd parsowania %s: %s", path, e)
        sys.exit(1)


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if "--test" in sys.argv:
        run_tests()

    if dry_run:
        log.info("Tryb DRY-RUN – state.json nie zostanie zapisany")

    sites = load_sites()
    old_state = load_state()

    session = requests.Session()
    results: list[ScrapeResult] = []

    for site in sites:
        log.info("--- Sprawdzam: %s ---", site.get("name", site["url"]))
        result = dispatch_parser(site, session)
        results.append(result)

    changes = detect_and_report_changes(results, old_state)
    send_notifications(changes)
    print_report(changes, results)

    if not dry_run:
        new_state = dict(old_state)
        for r in results:
            new_state[r.url] = r.to_state_dict()
        save_state(new_state)
        save_history(results, sites)


if __name__ == "__main__":
    main()
