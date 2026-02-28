#!/usr/bin/env python3
"""
api.py – Backend API dla Price Watch Dashboard.

Serwer HTTP (stdlib) z REST API do zarządzania śledzonymi produktami,
wyszukiwania sklepów i serwowania dashboardu.

Użycie:
    python3 api.py              # port 8765
    python3 api.py --port 9000  # inny port
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("api")

BASE_DIR = Path(__file__).parent
ITEMS_FILE = BASE_DIR / "items.json"
SITES_FILE = BASE_DIR / "sites.json"
STATE_FILE = BASE_DIR / "state.json"
HISTORY_FILE = BASE_DIR / "history.json"
PRICE_WATCH = BASE_DIR / "price_watch.py"
AUTO_CHECK_INTERVAL = 15  # minutes


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

is_checking = False


def load_json(path: Path) -> list | dict:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return [] if "items" in path.name else {}


def save_json(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_items() -> list[dict]:
    """Load items.json. Falls back to migrating from sites.json."""
    items = load_json(ITEMS_FILE)
    if items:
        return items

    # Migrate from sites.json
    sites = load_json(SITES_FILE)
    if not sites:
        return []

    item = {
        "id": "migrated-product",
        "name": "Zmigrowany produkt",
        "sku_hint": sites[0].get("sku_hint", ""),
        "created": datetime.now(timezone.utc).isoformat(),
        "sites": sites,
    }
    items = [item]
    save_json(ITEMS_FILE, items)
    log.info("Zmigrowano sites.json → items.json")
    return items


def save_items(items: list[dict]) -> None:
    save_json(ITEMS_FILE, items)


def make_id(name: str) -> str:
    """Generate slug ID from product name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
    return slug[:50] or f"item-{int(datetime.now().timestamp())}"


# ---------------------------------------------------------------------------
# DuckDuckGo search
# ---------------------------------------------------------------------------


def search_duckduckgo(query: str, max_results: int = 15) -> list[dict]:
    """
    Search DuckDuckGo for shopping results.
    Returns list of {title, url, snippet}.
    """
    search_query = f"{query} kup cena sklep"
    encoded = urllib.parse.quote_plus(search_query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
    }

    req = urllib.request.Request(url, headers=headers)
    results = []
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Simple regex parsing of DuckDuckGo HTML results
        # Each result has class "result" with link and snippet
        result_blocks = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )

        for href, title, snippet in result_blocks[:max_results]:
            # DuckDuckGo wraps URLs in redirect
            actual_url = href
            if "uddg=" in href:
                match = re.search(r"uddg=([^&]+)", href)
                if match:
                    actual_url = urllib.parse.unquote(match.group(1))
            elif "ad_domain=" in href:
                # Ad redirect: extract domain and build https URL
                match = re.search(r"ad_domain=([^&]+)", href)
                if match:
                    actual_url = f"https://{urllib.parse.unquote(match.group(1))}"

            # Skip if URL is still a duckduckgo tracking link
            if "duckduckgo.com" in actual_url:
                continue

            clean_title = re.sub(r"<[^>]+>", "", title).strip()
            clean_snippet = re.sub(r"<[^>]+>", "", snippet).strip()

            # Skip non-shop results
            if any(skip in actual_url for skip in ["youtube.com", "wikipedia.org", "facebook.com"]):
                continue

            # Deduplicate by domain
            domain = urllib.parse.urlparse(actual_url).netloc
            if any(urllib.parse.urlparse(r["url"]).netloc == domain for r in results):
                continue

            results.append({
                "title": clean_title,
                "url": actual_url,
                "snippet": clean_snippet,
            })

    except Exception as e:
        log.warning("DuckDuckGo search failed: %s", e)

    return results


# ---------------------------------------------------------------------------
# API Handler
# ---------------------------------------------------------------------------


class APIHandler(SimpleHTTPRequestHandler):
    """HTTP handler with REST API endpoints + static file serving."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _route(self, method: str):
        """Route request to appropriate handler."""
        path = self.path.split("?")[0].rstrip("/")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        # API routes
        if path == "/api/items" and method == "GET":
            self._api_get_items()
            return True
        if path == "/api/items" and method == "POST":
            self._api_add_item()
            return True
        if re.match(r"^/api/items/[^/]+$", path) and method == "DELETE":
            item_id = path.split("/")[-1]
            self._api_delete_item(item_id)
            return True
        if re.match(r"^/api/items/[^/]+/sites$", path) and method == "POST":
            item_id = path.split("/")[-2]
            self._api_add_sites(item_id)
            return True
        if re.match(r"^/api/items/[^/]+/sites$", path) and method == "DELETE":
            item_id = path.split("/")[-2]
            self._api_delete_site(item_id)
            return True
        if re.match(r"^/api/items/[^/]+/sites$", path) and method == "PUT":
            item_id = path.split("/")[-2]
            self._api_update_site(item_id)
            return True
        if path == "/api/search" and method == "GET":
            q = query.get("q", [""])[0]
            self._api_search(q)
            return True
        if path == "/api/check" and method == "POST":
            self._api_check()
            return True
        if path == "/api/status" and method == "GET":
            self._api_get_status()
            return True

        return None  # not an API route

    def do_GET(self):
        result = self._route("GET")
        if result is not None:
            return  # API route handled, don't continue to file serving
        # Serve / as dashboard.html
        if self.path in ("/", "/index.html"):
            self.path = "/dashboard.html"
        super().do_GET()

    def do_POST(self):
        result = self._route("POST")
        if result is None:
            self.send_error(404)

    def do_DELETE(self):
        result = self._route("DELETE")
        if result is None:
            self.send_error(404)

    def do_PUT(self):
        result = self._route("PUT")
        if result is None:
            self.send_error(404)

    # ── API handlers ──────────────────────────────────────────────────

    def _api_get_status(self):
        global is_checking
        self._send_json({"checking": is_checking})

    def _api_get_items(self):
        items = load_items()
        state = load_json(STATE_FILE)
        history = load_json(HISTORY_FILE)

        # Enrich items with current prices
        enriched = []
        for item in items:
            item_data = dict(item)
            sites_enriched = []
            for site in item.get("sites", []):
                site_data = dict(site)
                url = site["url"]
                if url in state:
                    site_data["current_price"] = state[url].get("price")
                    site_data["availability"] = state[url].get("availability", "unknown")
                    site_data["last_checked"] = state[url].get("timestamp")
                    site_data["error"] = state[url].get("error")
                    site_data["variant_confirmed"] = state[url].get("variant_confirmed", False)
                    site_data["sku_confirmed"] = state[url].get("sku_confirmed", False)
                # Find price history for this shop
                shop_name = site.get("name", url)
                if shop_name in history:
                    records = history[shop_name]
                    site_data["history"] = records[-50:]  # last 50
                    # Compute price_change for this site
                    price_records = [r for r in records if r.get("price") is not None]
                    if len(price_records) >= 2:
                        site_data["price_change"] = price_records[-1]["price"] - price_records[-2]["price"]
                    else:
                        site_data["price_change"] = None
                else:
                    site_data["price_change"] = None
                sites_enriched.append(site_data)
            item_data["sites"] = sites_enriched

            # Compute stats
            prices = [s["current_price"] for s in sites_enriched if s.get("current_price")]
            item_data["min_price"] = min(prices) if prices else None
            item_data["max_price"] = max(prices) if prices else None
            item_data["shop_count"] = len(sites_enriched)

            # Compute product-level price_change (change in min price)
            # Gather all history records with prices from all sites
            all_min_by_date = {}
            for s in sites_enriched:
                for r in s.get("history", []):
                    if r.get("price") is not None:
                        ts = r["timestamp"]
                        if ts not in all_min_by_date or r["price"] < all_min_by_date[ts]:
                            all_min_by_date[ts] = r["price"]
            if len(all_min_by_date) >= 2:
                sorted_mins = sorted(all_min_by_date.items(), key=lambda x: x[0])
                item_data["price_change"] = sorted_mins[-1][1] - sorted_mins[-2][1]
            else:
                item_data["price_change"] = None

            # Sort sites by price from cheapest to most expensive
            # Sites with no price data go to the end
            item_data["sites"] = sorted(
                sites_enriched,
                key=lambda s: s.get("current_price") if s.get("current_price") is not None else float('inf'),
                reverse=False
            )

            enriched.append(item_data)

        # Sort items by min_price from cheapest to most expensive
        # Items with no price data go to the end
        enriched.sort(
            key=lambda i: i.get("min_price") if i.get("min_price") is not None else float('inf'),
            reverse=False
        )

        self._send_json(enriched)

    def _api_add_item(self):
        body = self._read_body()
        name = body.get("name", "").strip()
        sites = body.get("sites", [])

        if not name:
            self._send_json({"error": "Nazwa produktu jest wymagana"}, 400)
            return

        items = load_items()
        item_id = make_id(name)

        # Ensure unique ID
        existing_ids = {i["id"] for i in items}
        if item_id in existing_ids:
            item_id = f"{item_id}-{int(datetime.now().timestamp()) % 10000}"

        # Auto-detect parser and name for each site
        for site in sites:
            if not site.get("parser"):
                site["parser"] = _guess_parser(site["url"])
            if not site.get("name"):
                site["name"] = _extract_domain(site["url"])

        new_item = {
            "id": item_id,
            "name": name,
            "sku_hint": body.get("sku_hint", ""),
            "created": datetime.now(timezone.utc).isoformat(),
            "sites": sites,
        }

        items.append(new_item)
        save_items(items)
        log.info("Dodano produkt: %s (%d sklepów)", name, len(sites))
        self._send_json(new_item, 201)

    def _api_delete_item(self, item_id: str):
        items = load_items()
        original_len = len(items)
        items = [i for i in items if i["id"] != item_id]

        if len(items) == original_len:
            self._send_json({"error": "Nie znaleziono produktu"}, 404)
            return

        save_items(items)
        log.info("Usunięto produkt: %s", item_id)
        self._send_json({"ok": True})

    def _api_add_sites(self, item_id: str):
        body = self._read_body()
        new_sites = body.get("sites", [])

        if not new_sites:
            self._send_json({"error": "Brak sklepów do dodania"}, 400)
            return

        items = load_items()
        for item in items:
            if item["id"] == item_id:
                existing_urls = {s["url"] for s in item.get("sites", [])}
                for site in new_sites:
                    if site.get("url") and site["url"] not in existing_urls:
                        # Auto-detect parser
                        if not site.get("parser"):
                            site["parser"] = _guess_parser(site["url"])
                        if not site.get("name"):
                            site["name"] = _extract_domain(site["url"])
                        item["sites"].append(site)
                save_items(items)
                log.info("Dodano %d sklepów do %s", len(new_sites), item_id)
                self._send_json({"ok": True, "total_sites": len(item["sites"])})
                return

        self._send_json({"error": "Nie znaleziono produktu"}, 404)

    def _api_delete_site(self, item_id: str):
        body = self._read_body()
        url_to_remove = body.get("url", "")

        items = load_items()
        for item in items:
            if item["id"] == item_id:
                original = len(item["sites"])
                item["sites"] = [s for s in item["sites"] if s["url"] != url_to_remove]
                if len(item["sites"]) == original:
                    self._send_json({"error": "Nie znaleziono sklepu"}, 404)
                    return
                save_items(items)
                self._send_json({"ok": True})
                return

        self._send_json({"error": "Nie znaleziono produktu"}, 404)

    def _api_update_site(self, item_id: str):
        """Update a site's URL and/or name."""
        body = self._read_body()
        old_url = body.get("old_url", "")
        new_url = body.get("new_url", "").strip()
        new_name = body.get("new_name", "").strip()

        if not old_url or not new_url:
            self._send_json({"error": "old_url i new_url są wymagane"}, 400)
            return

        items = load_items()
        for item in items:
            if item["id"] == item_id:
                for site in item["sites"]:
                    if site["url"] == old_url:
                        site["url"] = new_url
                        if new_name:
                            site["name"] = new_name
                        if not site.get("parser") or site["parser"] == "generic":
                            site["parser"] = _guess_parser(new_url)
                        save_items(items)
                        log.info("Zaktualizowano URL sklepu: %s → %s", old_url, new_url)
                        self._send_json({"ok": True})
                        return
                self._send_json({"error": "Nie znaleziono sklepu"}, 404)
                return

        self._send_json({"error": "Nie znaleziono produktu"}, 404)

    def _api_search(self, query: str):
        if not query or len(query) < 2:
            self._send_json({"error": "Zapytanie za krótkie"}, 400)
            return

        results = search_duckduckgo(query)
        self._send_json({"query": query, "results": results})

    def _api_check(self):
        """Run price_watch.py in background."""
        global is_checking
        if is_checking:
            self._send_json({"error": "Sprawdzanie już trwa"}, 400)
            return

        is_checking = True
        thread = threading.Thread(target=_run_price_check, daemon=True)
        thread.start()
        self._send_json({"ok": True, "message": "Sprawdzanie cen uruchomione"})

    def log_message(self, format, *args):
        """Log only API calls, skip static files."""
        path = args[0] if args else ""
        if "/api/" in str(path):
            log.info(format, *args)


# ---------------------------------------------------------------------------
# Price check runner (shared by manual & auto)
# ---------------------------------------------------------------------------


def _run_price_check():
    """Run price_watch.py. Used by both manual /api/check and auto-scheduler."""
    global is_checking
    try:
        items = load_items()
        all_sites = []
        for item in items:
            for site in item.get("sites", []):
                site_copy = dict(site)
                if item.get("sku_hint") and not site_copy.get("sku_hint"):
                    site_copy["sku_hint"] = item["sku_hint"]
                all_sites.append(site_copy)

        save_json(SITES_FILE, all_sites)

        subprocess.run(
            [sys.executable, str(PRICE_WATCH)],
            cwd=str(BASE_DIR),
            timeout=300,
        )
        log.info("Sprawdzenie cen zakończone")
    except Exception as e:
        log.error("Błąd sprawdzania cen: %s", e)
    finally:
        is_checking = False


def _auto_scheduler():
    """Background thread that runs price check every AUTO_CHECK_INTERVAL minutes."""
    global is_checking
    log.info("🕐 Auto-scheduler uruchomiony (co %d min)", AUTO_CHECK_INTERVAL)
    # First check after 60s so user can verify it works
    first_run = True
    while True:
        wait = 60 if first_run else AUTO_CHECK_INTERVAL * 60
        first_run = False
        time.sleep(wait)
        try:
            if is_checking:
                log.info("Auto-scheduler: sprawdzanie już trwa, pomijam")
                continue
            log.info("Auto-scheduler: uruchamiam sprawdzanie cen")
            is_checking = True
            _run_price_check()
        except Exception as e:
            log.error("Auto-scheduler: błąd: %s", e)
            is_checking = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _guess_parser(url: str) -> str:
    """Guess parser type from URL."""
    domain = urllib.parse.urlparse(url).netloc.lower()
    if "ceneo" in domain:
        return "ceneo"
    PLAYWRIGHT_DOMAINS = [
        "x-kom", "mediaexpert", "neonet", "intersport", "8a.pl", "allegro",
    ]
    if any(shop in domain for shop in PLAYWRIGHT_DOMAINS):
        return "playwright_generic"
    if any(shop in domain for shop in ["shopify", "myshopify"]):
        return "shopify"
    return "generic"


def _extract_domain(url: str) -> str:
    """Extract readable domain name from URL."""
    netloc = urllib.parse.urlparse(url).netloc
    return netloc.replace("www.", "")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Price Watch API Server")
    parser.add_argument("--port", type=int, default=8765, help="Port (domyślnie: 8765)")
    parser.add_argument("--no-auto", action="store_true", help="Wyłącz auto-scheduler")
    args = parser.parse_args()

    # Ensure items.json exists
    load_items()

    # Start auto-scheduler thread
    if not args.no_auto:
        sched_thread = threading.Thread(target=_auto_scheduler, daemon=True)
        sched_thread.start()

    server = HTTPServer(("", args.port), APIHandler)
    log.info("🎿 Price Watch API → http://localhost:%d", args.port)
    log.info("🔁 Auto-odświeżanie co %d min %s",
             AUTO_CHECK_INTERVAL,
             "(wyłączone)" if args.no_auto else "(włączone)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Zatrzymano.")
        server.server_close()


if __name__ == "__main__":
    main()
