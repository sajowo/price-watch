# Price Watch - Rozszerzenie Chrome

Rozszerzenie Chrome do monitorowania cen produktów w polskich sklepach internetowych.

## Funkcje

- **Wybór produktu**: Dropdown do wyboru śledzonego produktu
- **Statystyki**: Najniższa cena, średnia, trend
- **Wykres**: Historia cen dla wszystkich sklepów
- **Lista sklepów**: Ranking z cenami i dostępnością
- **Powiadomienia**: Automatyczne alerty o zmianach cen (co 30 min)
- **Badge**: Najniższa cena wyświetlana na ikonie rozszerzenia

## Instalacja

1. Upewnij się, że serwer API działa:
   ```bash
   cd ..
   python3 api.py
   ```

2. Otwórz Chrome i przejdź do `chrome://extensions/`

3. Włącz "Tryb dewelopera" (prawy górny róg)

4. Kliknij "Załaduj rozpakowane" i wybierz folder `extension`

5. Rozszerzenie pojawi się na pasku narzędzi

## Użycie

1. Kliknij ikonę rozszerzenia na pasku narzędzi
2. Wybierz produkt z listy rozwijanej u góry
3. Przeglądaj statystyki, wykres i ceny w sklepach
4. Kliknij na sklep aby otworzyć stronę produktu
5. Użyj przycisku "Pełny dashboard ↗" aby otworzyć pełną wersję

## Powiadomienia

Rozszerzenie automatycznie sprawdza ceny co 30 minut i wysyła powiadomienia gdy:
- Cena produktu się zmienia
- Produkt staje się dostępny

## Wymagania

- Chrome/Edge/Brave (Manifest V3)
- Lokalny serwer API na `http://localhost:8765`
- Produkty dodane przez dashboard lub API

## Struktura plików

```
extension/
├── manifest.json       # Konfiguracja rozszerzenia
├── popup.html          # Interfejs popup
├── popup.js            # Logika popup
├── background.js       # Service worker (alarmy, powiadomienia)
├── chart.min.js        # Chart.js (lokalnie)
└── icons/              # Ikony rozszerzenia
    ├── icon16.png
    ├── icon48.png
    └── icon128.png
```

## Rozwój

Po zmianach w kodzie:
1. Przejdź do `chrome://extensions/`
2. Kliknij ikonę odświeżania przy rozszerzeniu
3. Otwórz popup ponownie

## Troubleshooting

### Rozszerzenie pokazuje "Brak danych z serwera"
- Sprawdź czy serwer API działa: `curl http://localhost:8765/api/items`
- Upewnij się że używasz `python3 api.py` (nie `serve.py`)

### Brak produktów w dropdown
- Dodaj produkty przez dashboard: `http://localhost:8765`
- Sprawdź `items.json` - powinien zawierać listę produktów

### Powiadomienia nie działają
- Sprawdź uprawnienia rozszerzenia w Chrome
- Upewnij się że powiadomienia są włączone w systemie
- Service worker musi być aktywny (sprawdź w `chrome://extensions/`)
