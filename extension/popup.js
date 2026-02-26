// popup.js – logika popupu rozszerzenia Price Watch

const BASE_URL = "http://localhost:8765";
const PALETTE = [
    '#3b82f6', '#10b981', '#f59e0b', '#ec4899', '#6366f1',
    '#14b8a6', '#f97316', '#8b5cf6', '#06b6d4', '#84cc16',
];

let chartInstance = null;
let allItems = [];
let selectedItemId = null;

// ── Helpers ──────────────────────────────────────────────────────────────
function fmtPrice(p, short = false) {
    if (p == null) return null;
    if (short) return Math.round(p).toLocaleString('pl-PL') + ' zł';
    return new Intl.NumberFormat('pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(p) + ' PLN';
}
function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit' })
        + ' ' + d.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' });
}
function hostname(url) {
    try { return new URL(url).hostname.replace('www.', ''); } catch { return url; }
}

// ── Show/hide states ──────────────────────────────────────────────────────
function showLoading() {
    document.getElementById('loadingState').style.display = '';
    document.getElementById('errorState').style.display = 'none';
    document.getElementById('stats').style.display = 'none';
    document.getElementById('chartSection').style.display = 'none';
    document.getElementById('shopsSection').style.display = 'none';
    document.getElementById('footer').style.display = 'none';
}
function showError() {
    document.getElementById('loadingState').style.display = 'none';
    document.getElementById('errorState').style.display = '';
    document.getElementById('stats').style.display = 'none';
    document.getElementById('chartSection').style.display = 'none';
    document.getElementById('shopsSection').style.display = 'none';
    document.getElementById('footer').style.display = 'none';
}
function showData() {
    document.getElementById('loadingState').style.display = 'none';
    document.getElementById('errorState').style.display = 'none';
    document.getElementById('stats').style.display = '';
    document.getElementById('chartSection').style.display = '';
    document.getElementById('shopsSection').style.display = '';
    document.getElementById('footer').style.display = '';
}

// ── Load products list ────────────────────────────────────────────────────
async function loadProducts() {
    try {
        const resp = await fetch(`${BASE_URL}/api/items?t=${Date.now()}`, { cache: 'no-store' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        allItems = await resp.json();

        const select = document.getElementById('productSelect');
        select.innerHTML = '';

        if (allItems.length === 0) {
            select.innerHTML = '<option value="">Brak produktów</option>';
            return;
        }

        allItems.forEach(item => {
            const option = document.createElement('option');
            option.value = item.id;
            option.textContent = item.name;
            select.appendChild(option);
        });

        // Load saved selection or use first item
        const saved = await chrome.storage.local.get('selectedItemId');
        selectedItemId = saved.selectedItemId || allItems[0].id;
        select.value = selectedItemId;

        // Load data for selected item
        loadData();
    } catch (e) {
        console.error('Failed to load products:', e);
        document.getElementById('productSelect').innerHTML = '<option value="">Błąd ładowania</option>';
        showError();
    }
}

// ── Product selection change ──────────────────────────────────────────────
document.getElementById('productSelect').addEventListener('change', async (e) => {
    selectedItemId = e.target.value;
    await chrome.storage.local.set({ selectedItemId });
    loadData();
});

// ── Render stats ──────────────────────────────────────────────────────────
function renderStats(item) {
    const sites = item.sites || [];
    const withPrice = sites.filter(s => s.current_price != null);
    const best = withPrice.reduce((a, b) => (a.current_price < b.current_price ? a : b), withPrice[0] || null);
    const avg = withPrice.length ? withPrice.reduce((s, e) => s + e.current_price, 0) / withPrice.length : null;

    document.getElementById('sBest').textContent = best ? fmtPrice(best.current_price, true) : '—';
    document.getElementById('sBestShop').textContent = best ? (best.name || hostname(best.url)) : '—';
    document.getElementById('sAvg').textContent = avg ? fmtPrice(avg, true) : '—';

    // Trend - calculate from history
    let latestChange = null;
    sites.forEach(site => {
        const history = site.history || [];
        for (let i = 1; i < history.length; i++) {
            const r = history[i], prev = history[i - 1];
            if (r.price != null && prev.price != null && r.price !== prev.price) {
                latestChange = r.price - prev.price;
            }
        }
    });

    const tEl = document.getElementById('sTrend');
    const tSub = document.getElementById('sTrendSub');
    if (latestChange == null) {
        tEl.textContent = '→ Stabilne'; tEl.style.color = '#64748b';
        tSub.textContent = 'brak zmian';
    } else if (latestChange < 0) {
        tEl.textContent = `↓ ${Math.abs(latestChange).toFixed(0)} zł`;
        tEl.style.color = '#10b981';
        tSub.textContent = 'ostatnia zmiana';
    } else {
        tEl.textContent = `↑ ${latestChange.toFixed(0)} zł`;
        tEl.style.color = '#ef4444';
        tSub.textContent = 'ostatnia zmiana';
    }
}

// ── Render mini chart ─────────────────────────────────────────────────────
function renderChart(item) {
    const container = document.getElementById('chartContainer');
    const sites = item.sites || [];

    // Build history from sites, deduplicating consecutive same-price records
    const shopsWithData = sites
        .filter(site => {
            const history = site.history || [];
            return history.filter(r => r.price != null).length >= 1;
        })
        .map(site => {
            const name = site.name || hostname(site.url);
            const raw = (site.history || []).filter(r => r.price != null);
            // Keep only records where price changed from previous
            const deduped = [];
            for (let i = 0; i < raw.length; i++) {
                if (i === 0 || raw[i].price !== raw[i - 1].price || i === raw.length - 1) {
                    deduped.push(raw[i]);
                }
            }
            return [name, deduped];
        })
        .filter(([, recs]) => recs.length >= 1);

    if (shopsWithData.length === 0) {
        container.innerHTML = '<div class="no-chart">Brak historii – uruchom skrypt kilka razy</div>';
        return;
    }

    const allTs = new Set();
    shopsWithData.forEach(([, recs]) => recs.forEach(r => allTs.add(r.timestamp)));

    // Limit to last 3 days
    const threeDaysAgo = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
    const sortedTs = [...allTs].sort().filter(ts => ts >= threeDaysAgo);
    const labels = sortedTs.map(ts => {
        const d = new Date(ts);
        return d.toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit' })
            + ' ' + d.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' });
    });

    const datasets = shopsWithData.map(([name, recs], i) => {
        const dataMap = {};
        recs.forEach(r => { if (r.price != null) dataMap[r.timestamp] = r.price; });
        return {
            label: name,
            data: sortedTs.map(ts => dataMap[ts] ?? null),
            borderColor: PALETTE[i % PALETTE.length],
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            pointRadius: 0,
            pointHitRadius: 6,
            tension: 0.3,
            spanGaps: true,
        };
    });

    // Replace container with canvas
    container.innerHTML = '<canvas id="miniChart"></canvas>';
    const canvas = document.getElementById('miniChart');

    if (chartInstance) chartInstance.destroy();
    chartInstance = new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        color: '#94a3b8',
                        font: { size: 9 },
                        boxWidth: 10,
                        boxHeight: 2,
                        padding: 6,
                        usePointStyle: false,
                    }
                },
                tooltip: {
                    backgroundColor: '#1a2235',
                    borderColor: '#1f2d45',
                    borderWidth: 1,
                    titleColor: '#e2e8f0',
                    bodyColor: '#94a3b8',
                    padding: 8,
                    callbacks: {
                        label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? Math.round(ctx.parsed.y).toLocaleString('pl-PL') + ' zł' : '—'}`
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: '#1f2d45' },
                    ticks: { color: '#64748b', font: { size: 9 }, maxTicksLimit: 5 }
                },
                y: {
                    beginAtZero: false,
                    grid: { color: '#1f2d45' },
                    ticks: {
                        color: '#64748b', font: { size: 9 },
                        callback: v => Math.round(v).toLocaleString('pl-PL') + ' zł'
                    }
                }
            }
        }
    });
}

// ── Render shop list ──────────────────────────────────────────────────────
function renderShops(item) {
    const sites = item.sites || [];
    const sorted = [...sites].sort((a, b) => {
        if (a.current_price == null && b.current_price == null) return 0;
        if (a.current_price == null) return 1;
        if (b.current_price == null) return -1;
        return a.current_price - b.current_price;
    });

    const withPrice = sorted.filter(s => s.current_price != null);
    const minPrice = withPrice.length ? withPrice[0].current_price : null;

    const list = document.getElementById('shopList');
    list.innerHTML = '';

    sorted.forEach((site, i) => {
        const a = document.createElement('a');
        a.className = 'shop-row';
        a.href = site.url;
        a.target = '_blank';

        const rank = i + 1;
        const isGold = site.current_price != null && site.current_price === minPrice;
        const rankLabel = isGold ? '🥇' : (rank <= 3 && site.current_price != null ? `#${rank}` : (site.current_price != null ? `#${rank}` : '—'));

        const tags = [];
        if (site.variant_confirmed) tags.push('<span class="tag var">✓ Wariant</span>');
        if (site.sku_confirmed) tags.push('<span class="tag sku">✓ SKU</span>');
        if (site.error) tags.push('<span class="tag err">Błąd</span>');

        const availText = { in_stock: '✅ Dostępny', out_of_stock: '❌ Niedostępny', unknown: '' }[site.availability] || '';
        const availClass = site.availability === 'in_stock' ? 'green' : site.availability === 'out_of_stock' ? 'red' : '';

        a.innerHTML = `
      <div class="shop-rank ${isGold ? 'gold' : ''}">${rankLabel}</div>
      <div class="shop-info">
        <div class="shop-name">${site.name || hostname(site.url)}</div>
        <div class="shop-tags">${tags.join('')}</div>
      </div>
      <div class="shop-price">
        ${site.current_price != null
                ? `<div class="price-val">${Math.round(site.current_price).toLocaleString('pl-PL')} <span style="font-size:10px;font-weight:500;color:var(--muted)">PLN</span></div>`
                : `<div class="price-val no-price">Brak ceny</div>`
            }
        ${availText ? `<div class="price-avail ${availClass}">${availText}</div>` : ''}
      </div>
    `;
        list.appendChild(a);
    });
}

// ── Main load ─────────────────────────────────────────────────────────────
async function loadData() {
    if (!selectedItemId) return;

    const btn = document.getElementById('refreshBtn');
    btn.disabled = true;
    btn.textContent = '⟳ Ładowanie…';
    showLoading();

    try {
        const resp = await fetch(`${BASE_URL}/api/items?t=${Date.now()}`, { cache: 'no-store' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        allItems = await resp.json();

        const item = allItems.find(i => i.id === selectedItemId);
        if (!item) {
            showError();
            btn.disabled = false;
            btn.textContent = '⟳ Odśwież';
            return;
        }

        renderStats(item);
        renderChart(item);
        renderShops(item);

        // Footer - find latest timestamp
        const sites = item.sites || [];
        const timestamps = sites.map(s => s.last_checked).filter(Boolean).sort();
        const lastTs = timestamps[timestamps.length - 1];
        document.getElementById('footerTime').textContent =
            lastTs ? `Aktualizacja: ${fmtDate(lastTs)}` : 'Brak danych o czasie';

        showData();
    } catch (e) {
        console.error('Failed to load data:', e);
        showError();
    } finally {
        btn.disabled = false;
        btn.textContent = '⟳ Odśwież';
    }
}

// ── Open dashboard ────────────────────────────────────────────────────────
document.getElementById('openDash').addEventListener('click', () => {
    chrome.tabs.create({ url: `${BASE_URL}/dashboard.html` });
});

// ── Refresh button ────────────────────────────────────────────────────────
document.getElementById('refreshBtn').addEventListener('click', () => {
    loadData();
});

// ── Init ──────────────────────────────────────────────────────────────────
loadProducts();
