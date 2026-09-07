import { renderHistory, renderRows, renderStatuses, saveHistory } from './ui.js';

const els = {
  form: document.querySelector('#searchForm'),
  query: document.querySelector('#query'),
  searchButton: document.querySelector('#searchButton'),
  sealedOnly: document.querySelector('#sealedOnly'),
  forceRefresh: document.querySelector('#forceRefresh'),
  inStockOnly: document.querySelector('#inStockOnly'),
  history: document.querySelector('#history'),
  progress: document.querySelector('#progress'),
  resultCount: document.querySelector('#resultCount'),
  results: document.querySelector('#results'),
  statuses: document.querySelector('#storeStatuses'),
};

let stores = [];
let storeResults = new Map();
let searching = false;
let currentRun = 0;

init();

async function init() {
  refreshHistory();
  try {
    const response = await fetch('/api/stores', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    stores = data.stores || [];
    els.progress.textContent = `${stores.length}売り場を検索できる。`;
    renderStatuses(els.statuses, stores, storeResults, searching);
  } catch (error) {
    els.progress.textContent = `ショップ一覧の取得に失敗: ${error.message}`;
  }
}

els.form.addEventListener('submit', (event) => {
  event.preventDefault();
  startSearch();
});
els.inStockOnly.addEventListener('change', () => renderRows(els.results, els.resultCount, storeResults, els.inStockOnly.checked));

async function startSearch() {
  if (searching || !stores.length) return;
  const query = els.query.value.trim();
  if (!query) return;

  saveHistory(query);
  refreshHistory();
  storeResults = new Map();
  searching = true;
  const runId = ++currentRun;
  const forceRefresh = els.forceRefresh.checked;
  const cacheBust = forceRefresh ? String(Date.now()) : '';
  els.searchButton.disabled = true;
  els.results.innerHTML = '<div class="empty">検索開始。返ってきた店から表示する。</div>';
  els.resultCount.textContent = '';

  let completed = 0;
  updateProgress(completed);
  renderStatuses(els.statuses, stores, storeResults, searching);

  const queue = [...stores];
  const workers = Array.from({ length: Math.min(6, queue.length) }, async () => {
    while (queue.length && runId === currentRun) {
      const store = queue.shift();
      try {
        const params = new URLSearchParams({
          store: store.id,
          q: query,
          sealed: els.sealedOnly.checked ? '1' : '0',
          refresh: forceRefresh ? '1' : '0',
        });
        if (cacheBust) params.set('_bust', cacheBust);
        const response = await fetch(`/api/search?${params}`, { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        storeResults.set(store.id, data);
      } catch (error) {
        storeResults.set(store.id, {
          query,
          store,
          status: 'error',
          results: [],
          error: error.message || String(error),
          manualSearchUrl: store.home,
        });
      } finally {
        completed += 1;
        updateProgress(completed);
        renderRows(els.results, els.resultCount, storeResults, els.inStockOnly.checked);
        renderStatuses(els.statuses, stores, storeResults, searching);
      }
    }
  });

  await Promise.all(workers);
  if (runId !== currentRun) return;
  searching = false;
  els.searchButton.disabled = false;
  els.forceRefresh.checked = false;
  updateProgress(completed, true);
  renderStatuses(els.statuses, stores, storeResults, searching);
}

function updateProgress(completed, done = false) {
  els.progress.textContent = done ? `${stores.length}売り場の検索完了。` : `検索中 ${completed}/${stores.length}`;
}

function refreshHistory() {
  renderHistory(els.history, (query) => {
    els.query.value = query;
    els.query.focus();
  });
}
