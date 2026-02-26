# Funkcje rozszerzenia Price Watch 2.0

## Główne zmiany

### 1. Dropdown wyboru produktu

```
┌─────────────────────────────────────┐
│ 🎯 Price Watch                      │
│ ┌─────────────────────────────────┐ │
│ │ Rossignol ARCADE82 LTD 176cm  ▼ │ │ ← NOWE!
│ └─────────────────────────────────┘ │
│                          ⟳ Odśwież  │
└─────────────────────────────────────┘
```

Kliknij dropdown aby wybrać produkt z listy:
- Rossignol ARCADE82 LTD 176cm
- rtx 5060
- iphone 17 pro pl
- ... (wszystkie produkty z dashboard)

### 2. Automatyczne zapisywanie wyboru

Rozszerzenie pamięta ostatnio wybrany produkt. Po ponownym otwarciu popup zobaczysz ten sam produkt.

### 3. Statystyki dla wybranego produktu

```
┌─────────────┬─────────────┬─────────────┐
│ Najtaniej   │ Średnia     │ Trend       │
│ 1505 zł     │ 1834 zł     │ ↓ 50 zł     │
│ skiwebshop  │ ze sklepów  │ ostatnia    │
└─────────────┴─────────────┴─────────────┘
```

### 4. Wykres historii cen

Wykres pokazuje historię cen dla wszystkich sklepów wybranego produktu.

```
Historia cen (PLN)
┌─────────────────────────────────────┐
│     2000 ┤                           │
│     1800 ┤  ╭─────╮                  │
│     1600 ┤  │     ╰─╮                │
│     1400 ┤──╯       ╰────            │
└─────────────────────────────────────┘
```

### 5. Lista sklepów z rankingiem

```
Aktualne ceny
┌─────────────────────────────────────┐
│ 🥇 SkiWebShop.pl        1505 PLN    │
│    ✓ Wariant ✓ SKU                  │
├─────────────────────────────────────┤
│ #2 SkiRaceCenter.pl     1790 PLN    │
│    ✓ SKU                            │
├─────────────────────────────────────┤
│ #3 Intersport.pl        1750 PLN    │
│    ✓ Wariant ✓ SKU                  │
└─────────────────────────────────────┘
```

### 6. Badge z najniższą ceną

Ikona rozszerzenia pokazuje najniższą cenę wybranego produktu:

```
┌────┐
│🎯  │
│1.5k│  ← Najniższa cena: 1505 PLN
└────┘
```

### 7. Powiadomienia o zmianach

Gdy cena się zmieni, dostaniesz powiadomienie:

```
┌─────────────────────────────────────┐
│ 🎯 Rossignol ARCADE82 – zmiana ceny!│
│                                     │
│ SkiWebShop.pl: -50 zł → 1455 zł    │
│ Intersport.pl: +20 zł → 1770 zł    │
└─────────────────────────────────────┘
```

## Porównanie z wersją 1.0

| Funkcja          | v1.0                    | v2.0                  |
| ---------------- | ----------------------- | --------------------- |
| Liczba produktów | 1 (hardcoded)           | ∞ (dynamiczne)        |
| Wybór produktu   | Brak                    | Dropdown              |
| Źródło danych    | state.json              | API /api/items        |
| Powiadomienia    | Wszystkie razem         | Per produkt           |
| Badge            | Najniższa ze wszystkich | Najniższa z wybranego |
| Ikona            | 🎿                       | 🎯                     |

## Jak używać

1. **Dodaj produkty** przez dashboard: `http://localhost:8765`
2. **Otwórz rozszerzenie** - kliknij ikonę 🎯 na pasku
3. **Wybierz produkt** z dropdown u góry
4. **Przeglądaj dane** - statystyki, wykres, sklepy
5. **Kliknij sklep** aby otworzyć stronę produktu
6. **Odśwież** przyciskiem ⟳ aby pobrać najnowsze dane

## Automatyczne sprawdzanie

Rozszerzenie automatycznie sprawdza ceny co 30 minut i:
- Aktualizuje badge z najniższą ceną
- Wysyła powiadomienia o zmianach
- Zapisuje historię do wykresu

## Wymagania

- Serwer API musi działać: `python3 api.py`
- Produkty muszą być dodane przez dashboard
- Chrome/Edge/Brave z Manifest V3
