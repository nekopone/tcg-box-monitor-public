import {
  absoluteUrl,
  cleanText,
  extractPrice,
  extractStock,
  isJunkTitle,
  isLikelyProductUrl,
  isSealedTitle,
  looksLikeSingleCard,
  makeQueryTokens,
  normalizeText,
  normalizeUrlKey,
  parseAttributes,
  textMatchesQuery,
} from './search-common.js';

export function findCandidateProducts(html, baseUrl, query, sealedOnly = true, limit = 2) {
  if (!html) return [];
  const queryNorm = normalizeText(query);
  const tokens = makeQueryTokens(query);
  const anchorRe = /<a\b([^>]*?)href\s*=\s*["']([^"']+)["']([^>]*)>([\s\S]*?)<\/a>/gi;
  const byUrl = new Map();
  let match;
  let inspected = 0;

  while ((match = anchorRe.exec(html)) && inspected < 6000) {
    inspected += 1;
    const href = match[2];
    if (!href || href.startsWith('#') || /^javascript:/i.test(href) || /^mailto:/i.test(href)) continue;
    const url = absoluteUrl(baseUrl, href);
    if (!isLikelyProductUrl(url)) continue;

    const fullAnchor = match[0];
    const attrsText = `${match[1] || ''} ${match[3] || ''}`;
    let title = cleanText(match[4]);
    if (!title || title.length < 2) {
      const img = fullAnchor.match(/<img\b[^>]*>/i);
      if (img) {
        const attrs = parseAttributes(img[0]);
        title = cleanText(attrs.alt || attrs.title || '');
      }
    }
    if (!title) title = cleanText(parseAttributes(`<a ${attrsText}>`).title || '');

    const start = Math.max(0, match.index - 180);
    const end = Math.min(html.length, anchorRe.lastIndex + 1300);
    const context = cleanText(html.slice(start, end));
    const titleNorm = normalizeText(title);
    const contextNorm = normalizeText(context);
    if (!textMatchesQuery(titleNorm, contextNorm, queryNorm, tokens)) continue;

    const sealedTitle = isSealedTitle(title);
    const junkTitle = isJunkTitle(title);
    const singleCard = looksLikeSingleCard(title);
    if (sealedOnly) {
      if (junkTitle) continue;
      if (!sealedTitle && singleCard) continue;
      if (!sealedTitle && !/新品|未開封|ブースター|パック/i.test(context)) continue;
    }

    let score = 0;
    if (queryNorm.length >= 2 && titleNorm.includes(queryNorm)) score += 35;
    if (queryNorm.length >= 2 && contextNorm.includes(queryNorm)) score += 12;
    for (const token of tokens) {
      if (titleNorm.includes(token)) score += 8;
      else if (contextNorm.includes(token)) score += 2;
    }
    if (sealedTitle) score += 25;
    if (/カートン/i.test(title)) score += 4;
    if (/新品|未開封/i.test(title)) score += 6;
    if (singleCard) score -= 30;
    if (junkTitle) score -= 50;

    const afterText = cleanText(html.slice(anchorRe.lastIndex, Math.min(html.length, anchorRe.lastIndex + 900)));
    const price = extractPrice(afterText) ?? extractPrice(context);
    const stockInfo = extractStock(afterText || context);
    if (sealedOnly && price != null && price < 500 && !/パック(?!入り)/.test(title)) score -= 10;

    const candidate = {
      title: title || guessTitleFromContext(context, query),
      url,
      price,
      stock: stockInfo.stock,
      stockQty: stockInfo.qty,
      score,
      detailChecked: false,
    };
    if (!candidate.title) continue;
    const key = normalizeUrlKey(url);
    const current = byUrl.get(key);
    if (!current || current.score < candidate.score) byUrl.set(key, candidate);
  }

  return [...byUrl.values()]
    .sort((a, b) => {
      if (a.score !== b.score) return b.score - a.score;
      if (a.price == null && b.price != null) return 1;
      if (a.price != null && b.price == null) return -1;
      return (a.price || 0) - (b.price || 0);
    })
    .slice(0, limit);
}

function guessTitleFromContext(context, query) {
  const text = String(context || '').replace(/\s+/g, ' ').trim();
  const index = text.toLowerCase().indexOf(String(query || '').toLowerCase());
  if (index >= 0) return text.slice(Math.max(0, index - 60), index + String(query).length + 100).trim();
  return text.slice(0, 140).trim();
}
