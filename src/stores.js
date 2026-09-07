/**
 * Search targets for the personal TCG cross-search app.
 *
 * Important:
 * - No credentials, cookies, account IDs, purchase history, or other private data live here.
 * - Each store is searched only through a public search endpoint.
 * - If a store rejects automated requests, the Worker reports that and the UI falls back to a manual link.
 */
export const STORES = [
  {
    id: 'dayya',
    name: 'Day屋 蒲田',
    home: 'https://www.tcg-dayya.com/',
    action: 'https://www.tcg-dayya.com/products/list',
    field: 'name',
    note: '新品・カートン予約。カートンは伝票直貼り商品あり',
  },
  {
    id: 'hatti_xross',
    name: 'はっち XrossStars',
    home: 'https://www.hatti-xrossstars.jp/',
    action: 'https://www.hatti-xrossstars.jp/product-list',
    field: 'keyword',
    note: 'BOX予約。12BOX出荷条件は商品ごとに確認',
  },
  {
    id: 'cardwiz',
    name: 'カードショップWiZ',
    home: 'https://www.cardwiz.jp/',
    action: 'https://www.cardwiz.jp/product-list',
    field: 'keyword',
    note: '新品・予約を優先。旧弾未開封は仕入経路を別確認',
  },
  {
    id: 'torezamu',
    name: 'トレカ侍通販',
    home: 'https://www.torezamunet.com/',
    action: 'https://www.torezamunet.com/product-list',
    field: 'keyword',
    note: '新品・予約欄を優先。オリパ/謎袋は除外',
  },
  {
    id: '193',
    name: '193net',
    home: 'https://193tcg.com/',
    action: 'https://193tcg.com/products/list',
    field: 'name',
    note: '新品BOX用。カートンは納品書封入のため開封される商品あり',
  },
  {
    id: 'torecolo',
    name: 'CBトレコロ',
    home: 'https://www.torecolo.jp/shop/',
    action: 'https://www.torecolo.jp/shop/goods/search.aspx',
    field: 'keyword',
    note: '新品カートン予約の補助候補',
  },
  {
    id: 'tcgacademy',
    name: 'マスターズスクウェア1号店',
    home: 'https://www.tcgacademy.com/',
    action: 'https://www.tcgacademy.com/product-list',
    field: 'keyword',
    note: 'ポケカ/デュエマ等。新品BOX・カートン',
  },
  {
    id: 'square3rd',
    name: 'マスターズスクウェア3号店',
    home: 'https://www.square-3rd.jp/',
    action: 'https://www.square-3rd.jp/product-list',
    field: 'keyword',
    note: 'ロルカナ/MTG等。新品に限定して見る',
  },
  {
    id: 'square_bushiroad',
    name: 'マスターズスクウェア ブシロード店',
    home: 'https://www.square-bushiroad.com/',
    action: 'https://www.square-bushiroad.com/product-list',
    field: 'keyword',
    note: 'ヴァイス/ホロライブ/ヴァンガード等の新品・予約',
  },
  {
    id: 'advantage2',
    name: 'アドバンテージ2号店',
    home: 'https://www.advantagetcg2nd.jp/',
    action: 'https://www.advantagetcg2nd.jp/product-list',
    field: 'keyword',
    note: 'ホロカ/ウルトラマン等の新品・予約',
  },
  {
    id: 'tier1_op',
    name: 'ティアワン ワンピース',
    home: 'https://tier-one-onepiece.jp/',
    action: 'https://tier-one-onepiece.jp/view/search',
    field: 'search_keyword',
    note: 'ワンピース新品・カートン。梱包条件は商品ごとに確認',
  },
  {
    id: 'masters_gundam',
    name: 'マスターズスクウェア ガンダム',
    home: 'https://www.masters-square.com/',
    action: 'https://www.masters-square.com/product-list',
    field: 'keyword',
    note: 'ガンダム新品。BOX/カートン販売実績あり',
  },
  {
    id: 'papy',
    name: 'パピー弥富店',
    home: 'https://papysougouten.ocnk.net/',
    action: 'https://papysougouten.ocnk.net/product-list',
    field: 'keyword',
    note: '新品BOX。カートン外箱への伝票直貼りに注意',
  },
  {
    id: 'tier1_gundam',
    name: 'ティアワン ガンダム',
    home: 'https://tier-one.jp/',
    action: 'https://tier-one.jp/view/search',
    field: 'search_keyword',
    note: 'ガンダム新品商品',
  },
  {
    id: 'advantage1',
    name: 'TCG通販アドバンテージ',
    home: 'https://www.advantagetcg.jp/',
    action: 'https://www.advantagetcg.jp/product-list',
    field: 'keyword',
    note: '新品・予約。外装開封時の動画推奨ルールあり',
  },
  {
    id: 'futaki',
    name: 'ディスカウント二木',
    home: 'https://www.target.co.jp/',
    action: 'https://www.target.co.jp/view/search',
    field: 'search_keyword',
    note: '独自通販・新品BOX。通販用在庫を持つ運用',
  },
  {
    id: 'cardmax',
    name: 'CARDMAX',
    home: 'https://www.cardmax.jp/',
    action: 'https://www.cardmax.jp/shop/shopbrand.html',
    field: 'search',
    note: '新品BOX。店頭受取専用商品は除外して見る',
  },
  {
    id: 'mediaworld',
    name: 'メディアワールド本店',
    home: 'https://mediaworld.co.jp/',
    action: 'https://mediaworld.co.jp/search',
    field: 'q',
    fixed: { type: 'product' },
    note: '新品と「未使用中古」を明確に区別。新品だけを見る',
  },
  {
    id: 'suns',
    name: 'サンズオンラインストア',
    home: 'https://store.shopping.yahoo.co.jp/suns-online-store/',
    action: 'https://store.shopping.yahoo.co.jp/suns-online-store/search.html',
    field: 'p',
    note: '条件付き候補。正規卸仕入れは販売店側の表明',
  },
];

export const STORE_MAP = new Map(STORES.map((store) => [store.id, store]));

export function buildStoreSearchUrl(store, query) {
  const url = new URL(store.action);
  for (const [key, value] of Object.entries(store.fixed || {})) {
    url.searchParams.set(key, String(value));
  }
  url.searchParams.set(store.field, query);
  return url.toString();
}
