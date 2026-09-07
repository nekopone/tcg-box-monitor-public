import test from 'node:test';
import assert from 'node:assert/strict';
import { STORES, buildStoreSearchUrl } from '../src/stores.js';
import { extractStock, findCandidateProducts, normalizeText, parseProductDetail } from '../src/search.js';

test('tracks the expected 19 storefronts', () => {
  assert.equal(STORES.length, 19);
  assert.equal(new Set(STORES.map((store) => store.id)).size, 19);
});

test('builds encoded store search URLs from a fixed whitelist', () => {
  const store = STORES.find((item) => item.id === 'tier1_gundam');
  const url = new URL(buildStoreSearchUrl(store, 'GD03 BOX'));
  assert.equal(url.origin, 'https://tier-one.jp');
  assert.equal(url.searchParams.get('search_keyword'), 'GD03 BOX');
});

test('normalizes Japanese/ASCII variants', () => {
  assert.equal(normalizeText('【GD-03】 BOX'), 'gd03box');
});

test('prefers a GD03 BOX over a single-card link', () => {
  const html = `
    <div><a href="/product/100">GD03-001 SR シングルカード</a><span>120円</span><span>在庫あり</span></div>
    <div><a href="/product/200">ガンダムカードゲーム GD03 Steel Requiem BOX</a><span>5,808円</span><span>在庫数：4</span></div>
    <div><a href="/product/300">GD03 オリパ</a><span>1,000円</span></div>
  `;
  const results = findCandidateProducts(html, 'https://example.com/search?q=GD03', 'GD03', true, 2);
  assert.equal(results.length, 1);
  assert.match(results[0].title, /BOX/);
  assert.equal(results[0].price, 5808);
  assert.equal(results[0].stock, 'in_stock');
  assert.equal(results[0].stockQty, 4);
});

test('reads JSON-LD product price and availability', () => {
  const html = `
    <html><head>
      <script type="application/ld+json">{
        "@type":"Product",
        "name":"GD03 Steel Requiem BOX",
        "offers":{"price":"5808","availability":"https://schema.org/InStock"}
      }</script>
    </head><body><h1>GD03</h1></body></html>
  `;
  const result = parseProductDetail(html);
  assert.equal(result.title, 'GD03 Steel Requiem BOX');
  assert.equal(result.price, 5808);
  assert.equal(result.stock, 'in_stock');
});

test('stock parser treats explicit sold-out wording as out of stock', () => {
  assert.deepEqual(extractStock('この商品は現在、品切れです'), { stock: 'out_of_stock', qty: 0 });
});
