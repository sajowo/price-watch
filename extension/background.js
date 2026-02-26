// background.js – Service Worker dla rozszerzenia Price Watch
// Odpowiada za: alarm co 30 min, powiadomienia o zmianach cen, badge z najniższą ceną

const API_URL = "http://localhost:8765/api/items";
const ALARM_NAME = "price-check";
const ALARM_PERIOD_MINUTES = 30;

// ── Inicjalizacja ─────────────────────────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
    chrome.alarms.create(ALARM_NAME, {
        periodInMinutes: ALARM_PERIOD_MINUTES,
        delayInMinutes: 0,
    });
    console.log("[PriceWatch] Zainstalowano. Alarm co", ALARM_PERIOD_MINUTES, "min.");
});

// ── Alarm handler ─────────────────────────────────────────────────────────
chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM_NAME) {
        checkPrices();
    }
});

// ── Fetch + porównaj z poprzednim stanem ──────────────────────────────────
async function checkPrices() {
    let items;
    try {
        const resp = await fetch(`${API_URL}?t=${Date.now()}`, { cache: "no-store" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        items = await resp.json();
    } catch (e) {
        console.warn("[PriceWatch] Nie można pobrać danych:", e.message);
        setBadge("ERR", "#ef4444");
        return;
    }

    if (!items || items.length === 0) {
        setBadge("—", "#64748b");
        return;
    }

    // Get selected item or use first
    const { selectedItemId } = await chrome.storage.local.get("selectedItemId");
    const selectedItem = items.find(i => i.id === selectedItemId) || items[0];

    // Find lowest price from selected item
    const sites = selectedItem.sites || [];
    const prices = sites
        .map(s => s.current_price)
        .filter(p => p != null);
    const minPrice = prices.length ? Math.min(...prices) : null;

    if (minPrice != null) {
        const label = minPrice >= 1000
            ? Math.round(minPrice / 1000 * 10) / 10 + "k"
            : Math.round(minPrice).toString();
        setBadge(label, "#10b981");
    } else {
        setBadge("—", "#64748b");
    }

    // Compare with previous state
    const { prevItems } = await chrome.storage.local.get("prevItems");
    if (prevItems) {
        const changes = detectChanges(prevItems, items);
        if (changes.length > 0) {
            notifyChanges(changes, selectedItem.name);
        }
    }

    await chrome.storage.local.set({
        prevItems: items,
        lastCheck: Date.now(),
        minPrice
    });
}

function setBadge(text, color) {
    chrome.action.setBadgeText({ text });
    chrome.action.setBadgeBackgroundColor({ color });
}

function detectChanges(prevItems, currItems) {
    const changes = [];

    currItems.forEach(item => {
        const prevItem = prevItems.find(p => p.id === item.id);
        if (!prevItem) return;

        const currSites = item.sites || [];
        const prevSites = prevItem.sites || [];

        currSites.forEach(site => {
            const prevSite = prevSites.find(p => p.url === site.url);
            if (!prevSite) return;

            const currPrice = site.current_price;
            const prevPrice = prevSite.current_price;

            // Price change
            if (prevPrice != null && currPrice != null && Math.abs(currPrice - prevPrice) > 0.01) {
                changes.push({
                    itemName: item.name,
                    shopName: site.name || new URL(site.url).hostname.replace("www.", ""),
                    oldPrice: prevPrice,
                    newPrice: currPrice,
                    type: 'price'
                });
            }

            // Availability change to in_stock
            if (prevSite.availability !== 'in_stock' && site.availability === 'in_stock') {
                changes.push({
                    itemName: item.name,
                    shopName: site.name || new URL(site.url).hostname.replace("www.", ""),
                    type: 'availability'
                });
            }
        });
    });

    return changes;
}

function notifyChanges(changes, selectedItemName) {
    // Group changes by item
    const byItem = {};
    changes.forEach(c => {
        if (!byItem[c.itemName]) byItem[c.itemName] = [];
        byItem[c.itemName].push(c);
    });

    // Create notification for each item with changes
    Object.entries(byItem).forEach(([itemName, itemChanges]) => {
        const lines = itemChanges.map(c => {
            if (c.type === 'availability') {
                return `${c.shopName}: DOSTĘPNY!`;
            }
            const diff = c.newPrice - c.oldPrice;
            const sign = diff > 0 ? "+" : "";
            return `${c.shopName}: ${sign}${diff.toFixed(0)} zł → ${c.newPrice.toFixed(0)} zł`;
        });

        chrome.notifications.create({
            type: "basic",
            iconUrl: "icons/icon128.png",
            title: `🎯 ${itemName} – zmiana ceny!`,
            message: lines.join("\n"),
            priority: 2,
        });
    });
}

// Uruchom od razu przy starcie service workera
checkPrices();
