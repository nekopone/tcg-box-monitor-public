import { cleanText, decodeHtmlEntities, extractPrice, extractStock, parseMoney } from './search-common.js';

export function parseProductDetail(html) {
  const out = { title: '', price: null, stock: 'unknown', stockQty: null };
  if (!html) return out;

  const scripts = html.match(/<script\b[^>]*type=["']application\/ld\+json["'][^>]*>[\s\S]*?<\/script>/gi) || [];
  for (const script of scripts) {
    const bodyMatch = script.match(/>([\s\S]*?)<\/script>/i);
    if (!bodyMatch) continue;
    try {
      const product = findProductJsonLd(JSON.parse(decodeHtmlEntities(bodyMatch[1]).trim()));
      if (!product) continue;
      if (product.name) out.title = cleanText(String(product.name));
      const offers = Array.isArray(product.offers) ? product.offers[0] : product.offers;
      if (offers) {
        const price = Number(offers.price || offers.lowPrice || offers.highPrice);
        if (Number.isFinite(price) && price > 0) out.price = Math.round(price);
        const availability = String(offers.availability || '').toLowerCase();
        if (/instock|limitedavailability|preorder/.test(availability)) out.stock = 'in_stock';
        if (/outofstock|soldout|discontinued/.test(availability)) out.stock = 'out_of_stock';
      }
      break;
    } catch {}
  }

  if (out.price == null) {
    const patterns = [
      /<meta\b[^>]*(?:property|itemprop)=["'](?:product:price:amount|price)["'][^>]*content=["']([0-9,.]+)["'][^>]*>/i,
      /<meta\b[^>]*content=["']([0-9,.]+)["'][^>]*(?:property|itemprop)=["'](?:product:price:amount|price)["'][^>]*>/i,
    ];
    for (const pattern of patterns) {
      const match = html.match(pattern);
      if (!match) continue;
      const price = parseMoney(match[1]);
      if (price != null) { out.price = price; break; }
    }
  }

  const mainText = cleanText(extractMainProductRegion(html));
  if (!out.title) {
    const h1 = html.match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/i);
    if (h1) out.title = cleanText(h1[1]);
  }
  if (!out.title) {
    const title = html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i);
    if (title) out.title = cleanText(title[1]).replace(/\s*[|｜].*$/, '').trim();
  }
  if (out.price == null) out.price = extractPrice(mainText);
  if (out.stock === 'unknown') {
    const stock = extractStock(mainText);
    if (stock.stock !== 'unknown') { out.stock = stock.stock; out.stockQty = stock.qty; }
  }
  return out;
}

function extractMainProductRegion(html) {
  const source = String(html || '');
  const h1Index = source.search(/<h1\b/i);
  if (h1Index >= 0) return source.slice(Math.max(0, h1Index - 8000), Math.min(source.length, h1Index + 45000));
  return source.slice(0, Math.min(source.length, 60000));
}

function findProductJsonLd(obj) {
  if (!obj) return null;
  if (Array.isArray(obj)) {
    for (const item of obj) { const result = findProductJsonLd(item); if (result) return result; }
    return null;
  }
  if (typeof obj !== 'object') return null;
  const type = obj['@type'];
  if (type === 'Product' || (Array.isArray(type) && type.includes('Product'))) return obj;
  if (obj['@graph']) return findProductJsonLd(obj['@graph']);
  for (const value of Object.values(obj)) {
    if (value && typeof value === 'object') { const result = findProductJsonLd(value); if (result) return result; }
  }
  return null;
}
