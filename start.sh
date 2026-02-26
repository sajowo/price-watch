#!/bin/bash
# Skrypt startowy dla Price Watch Dashboard
# Uruchamia serwer API z pełną funkcjonalnością

# ntfy push notifications – topic for the ntfy app
export PRICE_WATCH_NTFY_TOPIC="price-watch-66831faf"

echo "🚀 Uruchamianie Price Watch API..."
echo "📊 Dashboard będzie dostępny na: http://localhost:8765"
echo "🔔 ntfy powiadomienia → topic: $PRICE_WATCH_NTFY_TOPIC"
echo ""

python3 api.py
