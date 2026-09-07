# TCG横断在庫検索（Cloudflare Workers版）

個人用の「TCG版 価格.com」。商品名、JAN、型番（例: `GD03`）を入れると、登録済みのTCG通販売り場をオンデマンドで横断検索し、価格・在庫・商品URLをまとめます。

## 設計

- **常時監視しない**: 検索ボタンを押した時だけ通信。
- **1ショップ = 1 Worker API呼び出し**: ブラウザから最大6店ずつ並列実行し、遅い店や壊れた店が全体を止めない。
- 各Worker呼び出しは、原則 **検索ページ1回 + 上位候補の商品詳細最大2回**だけ取得。
- 同じ `ショップ + 検索語` は **10分キャッシュ**。
- CAPTCHA / 403 / 429 を回避するコードは入れない。拒否された店は「手動確認」へ落とす。
- 検索先URLは固定ホワイトリスト。ユーザー入力から任意URLをfetchしないので、SSRF用途にならない。
- APIキー、Cookie、アカウントID、個人情報などの秘密情報はリポジトリに置かない。

## 現在の対象

19売り場。ガンダム、ポケカ、ワンピース、ヴァイス、ホロライブ、ロルカナ等を扱う、これまで信用面を確認した通販を中心に登録しています。

## Cloudflareへの初回デプロイ（ここだけ手作業）

このアプリは価格・在庫検索専用の公開リポジトリです。抽選監視・Discord通知・カレンダー登録は、別の `nekoromme/tcg-box-monitor-public` で動きます。

1. Cloudflare Dashboard → **Workers & Pages** → **Create application** → **Import a repository**。
2. GitHubを接続し、`nekoromme/tcg-cross-search` を選択。
3. Worker名を **`tcg-cross-search`** にする（`wrangler.jsonc` の `name` と一致させる）。
4. **Root directory** は空欄（リポジトリ直下）にする。`cross-search-worker` は入力しない。
5. Build command は `npm test`、Deploy command は `npx wrangler deploy` を推奨。
6. Save and Deploy。
7. 発行された `workers.dev` のURLを開く。以前の `cross-search-worker/*` という Build watch paths が設定されていれば削除する。専用リポジトリなのでパスによる絞り込みは不要。

以後はこのリポジトリの `main` が更新されるとCloudflare側が自動デプロイします。抽選監視側の更新には反応しません。

## ローカル確認

```bash
npm ci
npm test
npm run dev
```

## 変更しやすい場所

- 店の追加・検索URL変更: `src/stores.js`
- 検索結果から候補商品を拾う: `src/search-results.js`
- 商品詳細の価格・在庫判定: `src/product-detail.js`
- 共通の文字列・価格・在庫判定: `src/search-common.js`
- HTTP・キャッシュ: `src/index.js`
- 画面: `public/`

サイト側HTMLが変わって検索が壊れた場合、基本的には該当店の設定か解析ロジックだけ直せば復旧できます。
