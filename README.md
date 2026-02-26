# 🎯 Price Watch

A self-hosted price monitoring dashboard for tracking product prices across Polish online stores. Built with Python and vanilla JavaScript — no frameworks, no dependencies beyond the standard library and a few pip packages.

![Dashboard](https://img.shields.io/badge/status-active-brightgreen) ![Python](https://img.shields.io/badge/python-3.9+-blue) ![License](https://img.shields.io/badge/license-MIT-gray)

## Features

- **Multi-store tracking** — monitor the same product across 10+ stores simultaneously
- **Automatic price checks** — built-in scheduler refreshes prices every 30 minutes
- **Price history charts** — visualize price trends over 24h, 3 days, 7 days, 2 weeks, or 1 month
- **Price change alerts** — see at a glance which prices went up ↗ or down ↘
- **Smart search** — find products via DuckDuckGo and add stores with one click
- **Playwright support** — bypass bot protection on stores like x-kom, Media Expert, NEONET
- **Browser extension** — optional Chrome extension for quick access
- **Single-file dashboard** — no build step, just HTML + JS served by the API

## Quick Start

```bash
# 1. Clone / download the project
cd price_watch

# 2. Install dependencies
pip3 install requests beautifulsoup4 playwright
playwright install chromium

# 3. Start the server
python3 api.py
```

Open **http://localhost:8765** in your browser. That's it.

## Project Structure

```
price_watch/
├── api.py            # Backend API server (port 8765)
├── price_watch.py    # Price scraping engine with multiple parsers
├── dashboard.html    # Frontend UI (single-page app)
├── start.sh          # Quick-start script
├── items.json        # Tracked products & their store URLs
├── state.json        # Current prices (auto-generated)
├── history.json      # Price history over time (auto-generated)
├── sites.json        # Flat site list for scraper (auto-generated)
└── extension/        # Optional Chrome browser extension
```

## How It Works

### Architecture

```
Browser ←→ api.py (HTTP server) ←→ price_watch.py (scraper)
               ↕                         ↕
          items.json              state.json + history.json
```

1. **`api.py`** serves the dashboard and provides a REST API for managing products
2. **`price_watch.py`** scrapes prices from store URLs using multiple parser strategies
3. The **dashboard** (`dashboard.html`) is a single-page app that communicates with the API

### Parsers

The scraper supports several parser types, automatically selected based on the store domain:

| Parser | Used for | Method |
|--------|----------|--------|
| `generic` | Most stores | HTTP request + HTML parsing |
| `shopify` | Shopify-based stores | JSON-LD / product.json API |
| `ceneo` | Ceneo.pl | Price comparison scraping |
| `playwright_generic` | Bot-protected stores | Full Chromium browser |

Stores like **x-kom**, **Media Expert**, **NEONET**, and **Allegro** require the `playwright_generic` parser because they block standard HTTP requests with 403 errors.

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/items` | List all products with enriched price data |
| `POST` | `/api/items` | Add a new product |
| `DELETE` | `/api/items/:id` | Delete a product |
| `POST` | `/api/items/:id/sites` | Add stores to a product |
| `DELETE` | `/api/items/:id/sites` | Remove a store |
| `PUT` | `/api/items/:id/sites` | Update a store URL |
| `GET` | `/api/search?q=...` | Search DuckDuckGo for stores |
| `POST` | `/api/check` | Trigger a manual price refresh |
| `GET` | `/api/status` | Check if a price refresh is running |

### Auto-Refresh

The server automatically checks prices every **30 minutes** in a background thread. To disable:

```bash
python3 api.py --no-auto
```

### Dashboard Features

- **Home view** — product tiles sorted by lowest price, with quick stats
- **Detail view** — per-store prices, availability, price change badges, chart, and history log
- **Browser navigation** — back/forward buttons and gestures work via hash routing
- **Search & add** — search for products, select stores from results, add manually via URL

## Configuration

### Adding a Product

1. Use the search bar on the dashboard to find your product
2. Select relevant store links from the results
3. Click "Add" — the product appears on the home screen

### Changing a Store URL

Click **🔗 Zmień link** on any store card in the detail view to update its URL.

### Custom Port

```bash
python3 api.py --port 9000
```

## Browser Extension

An optional Chrome extension gives you quick access to prices from a popup without opening the full dashboard.

### Installation

1. Open Chrome and go to `chrome://extensions`
2. Enable **Developer mode** (toggle in the top-right corner)
3. Click **Load unpacked**
4. Select the `extension/` folder from this project
5. The 🎯 Price Watch icon appears in your toolbar

> **Note:** The extension connects to `http://localhost:8765`, so make sure the server is running.

### Features

- Product switcher dropdown
- Mini stats (lowest price, average, trend)
- Price history chart (last 3 days)
- Ranked shop list with prices and availability
- One-click refresh and link to full dashboard

## Push Notifications (ntfy)

Get push notifications on your phone when prices change — powered by [ntfy.sh](https://ntfy.sh).

### Setup

1. Install the **ntfy** app on your [iPhone](https://apps.apple.com/app/ntfy/id1625396347) or [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)
2. Subscribe to your topic in the app (e.g. `price-watch-66831faf`)
3. Set the topic in `start.sh`:
   ```bash
   export PRICE_WATCH_NTFY_TOPIC="price-watch-66831faf"
   ```
4. Start the server with `./start.sh`

When a price changes during an automatic or manual refresh, you'll receive a push notification like:

> 📉 **Obniżka ceny!**  
> Morele.net: 1 399 zł → 1 369 zł (-30 zł, 2.1%)

## Requirements

- **Python 3.9+**
- **pip packages**: `requests`, `beautifulsoup4`
- **Optional**: `playwright` (for bot-protected stores)

## License

MIT
