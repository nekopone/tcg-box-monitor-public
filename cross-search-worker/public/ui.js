export function flattenRows(storeResults) {
  const rows = [];
  for (const result of storeResults.values()) {
    for (const item of result.results || []) {
      rows.push({
        ...item,
        storeId: result.store.id,
        storeName: result.store.name,
        storeNote: result.store.note || '',
      });
    }
  }
  rows.sort((a, b) => {
    const stockOrder = { in_stock: 0, unknown: 1, out_of_stock: 2 };
    const sa = stockOrder[a.stock] ?? 1;
    const sb = stockOrder[b.stock] ?? 1;
    if (sa !== sb) return sa - sb;
    if (a.price == null && b.price != null) return 1;
    if (a.price != null && b.price == null) return -1;
    if (a.price != null && b.price != null && a.price !== b.price) return a.price - b.price;
    return (b.score || 0) - (a.score || 0);
  });
  return rows;
}

export function renderRows(container, countEl, storeResults, inStockOnly) {
  let rows = flattenRows(storeResults);
  if (inStockOnly) rows = rows.filter((row) => row.stock === 'in_stock');
  countEl.textContent = `${rows.length}件`;
  if (!rows.length) {
    const hasCompleted = storeResults.size > 0;
    container.innerHTML = `<div class="empty">${hasCompleted ? '現在表示できる候補なし。検索中か、各店で該当商品が見つかっていない。' : 'まだ検索していない。'}</div>`;
    return;
  }
  container.innerHTML = rows.map((row) => `
    <article class="result">
      <div>
        <div class="store">${escapeHtml(row.storeName)}</div>
        <div class="storeNote">${escapeHtml(row.storeNote)}</div>
      </div>
      <div class="title">
        <a href="${escapeAttr(row.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.title)}</a>
        <div class="checked">${row.detailChecked ? '商品詳細まで確認' : '検索結果ページから判定'}</div>
      </div>
      <div class="price">${formatPrice(row.price)}</div>
      <div>${stockBadge(row.stock, row.stockQty)}</div>
      <a class="open" href="${escapeAttr(row.url)}" target="_blank" rel="noopener noreferrer">商品ページ ↗</a>
    </article>
  `).join('');
}

export function renderStatuses(container, stores, storeResults, searching) {
  if (!stores.length) {
    container.innerHTML = '<div>読み込み中…</div>';
    return;
  }
  container.innerHTML = stores.map((store) => {
    const result = storeResults.get(store.id);
    if (!result) return statusRow(store, searching ? '待機' : '未検索', '', store.home);
    const labels = { ok: '候補あり', no_hit: '該当なし', blocked: '取得拒否', error: 'エラー' };
    const label = labels[result.status] || result.status || '不明';
    const message = result.error || (result.results?.length ? `${result.results.length}件` : '');
    return statusRow(store, label, message, result.manualSearchUrl || store.home);
  }).join('');
}

function statusRow(store, label, message, manualUrl) {
  return `
    <div class="statusRow">
      <strong>${escapeHtml(store.name)}</strong>
      <span class="statusBadge">${escapeHtml(label)}</span>
      <span class="message">${escapeHtml(message || '')}</span>
      <a class="manual" href="${escapeAttr(manualUrl)}" target="_blank" rel="noopener noreferrer">手動確認 ↗</a>
    </div>
  `;
}

function stockBadge(stock, qty) {
  if (stock === 'in_stock') return `<span class="stock in_stock">在庫あり${qty != null ? ` ${qty}` : ''}</span>`;
  if (stock === 'out_of_stock') return '<span class="stock out_of_stock">在庫なし</span>';
  return '<span class="stock unknown">在庫不明</span>';
}

function formatPrice(price) {
  return price == null ? '価格不明' : `${Number(price).toLocaleString('ja-JP')}円`;
}

export function loadHistory() {
  try {
    const value = JSON.parse(localStorage.getItem('tcg-search-history') || '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

export function saveHistory(query) {
  const previous = loadHistory().filter((item) => item !== query);
  previous.unshift(query);
  localStorage.setItem('tcg-search-history', JSON.stringify(previous.slice(0, 10)));
}

export function renderHistory(container, onSelect) {
  const history = loadHistory();
  container.innerHTML = history.map((query) => `<button class="chip" type="button" data-query="${escapeAttr(query)}">${escapeHtml(query)}</button>`).join('');
  for (const chip of container.querySelectorAll('.chip')) {
    chip.addEventListener('click', () => onSelect(chip.dataset.query || ''));
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function escapeAttr(value) {
  return escapeHtml(value);
}
