# TCG BOX抽選・発売日モニター

ポケモンカードゲーム、ONE PIECEカードゲーム、ドラゴンボールスーパーカードゲーム フュージョンワールド、遊戯王OCG、ディズニー・ロルカナ、ガンダムカードゲームのBOX抽選受付開始と発売日を監視し、DiscordとGoogle Calendarへ重複なく通知するPython 3.12製ツールです。

## 運用構成

- 共通ルールと全監視先は`sites.yaml`で一元管理します。
- 監視状態は、このリポジトリ自身の`monitor-state`ブランチへ`monitor_state.json`として保存します。別リポジトリには依存しません。
- 通知先などの認証情報だけをGitHub Actions Secretsへ保存します。
- 設定・状態とも通常のテキストなので、復号作業なしで確認、修正、テストできます。

## 監視方針

- 汎用の店舗・通販・抽選サイトは、6作品すべてを監視対象として設定します。
- メーカー公式、作品公式ショップ、ポケモンセンターなど作品が限定されるサイトだけは、扱う作品へ限定します。
- 全監視先の確認間隔は120分に統一しています。
- 定期実行は毎日06:04、11:04、16:04、18:04、20:04、22:04（日本時間）の6回です。
- BOXだけを対象にし、構築済みデッキ、スターター、単品パック、周辺用品、当選発表だけの告知は除外します。
- 公式情報を優先し、二次情報は補完経路として扱います。
- 発売日・発売月の表示と通知は、メーカー公式で確認できた商品だけが対象です。
- 公式発表が発売月のみの場合は、Discordへ「日にち未確定」と一度通知します。仮の日付では予定を作らず、正式な発売日が出た時に改めて通知し、Google Calendarへ登録します。
- CAPTCHA、Cloudflare、人間確認、ログイン画面は突破せず、監視異常として扱います。

作品ごとのON/OFFは`GAME_MONITOR_MODES.txt`で切り替えます。OFFの作品しか扱わない監視先は、ページ取得前に除外します。

`EXPEDITION_MODE.txt`は、当選時に1回来店すればよい遠征先を地域別に切り替えるファイルです。`EXPEDITION_SENDAI`、`EXPEDITION_TOKYO_ROUTE`、`EXPEDITION_TOKYO`をそれぞれ`ON`または`OFF`にできます。`OFF`の地域は、対象ページへの通信、画像読み取り、監視状態処理を行いません。

- `EXPEDITION_SENDAI`: 従来の仙台遠征5店
- `EXPEDITION_TOKYO_ROUTE`: 福島・郡山・小山・大宮の駅近店
- `EXPEDITION_TOKYO`: 秋葉原・池袋・渋谷・新宿の駅近店

TCバトロコ盛岡大通・仙台駅東口とトレーディングカードピット仙台駅東口店は通常監視のため、地域スイッチに関係なく動きます。リポスト応募、店頭掲示QRだけの応募、当選後に店頭予約と受取の2回来店が必要な回は通知対象から除外します。

## GitHub Actions Secrets

`Settings` → `Secrets and variables` → `Actions`で次を登録します。値をIssue、Pull Request、Actionsログ、READMEへ貼らないでください。

| Secret | 内容 |
|---|---|
| `DISCORD_WEBHOOK_URL` | 通知先DiscordチャンネルのWebhook URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | GoogleサービスアカウントJSON全文 |
| `GOOGLE_CALENDAR_ID` | 監視専用Google Calendar ID |

状態ブランチへの読み書きには、このリポジトリの`GITHUB_TOKEN`を使います。追加の状態保存用トークンは不要です。

## GitHub Actionsの使い方

1. `test` workflowで設定検証、静的解析、全テストを確認します。
2. 初回だけ`monitor` workflowを`baseline`で実行します。
3. `dry-run`で通知候補を確認します。
4. `arm`を実行し、定期通知を有効化します。
5. 以後は定期実行と、監視コード・設定を`main`へ反映した直後の実行に任せます。

既存の`monitor_state.json`を移行済みなら、`baseline`と`arm`のやり直しは不要です。

## ローカル実行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
tcg-monitor validate-config
pytest
```

監視状態を指定して外部送信なしで確認する場合は次を使います。

```bash
tcg-monitor --state monitor_state.json dry-run
tcg-monitor --state monitor_state.json summary
```

## コマンド

- `tcg-monitor validate-config`: 設定検証
- `tcg-monitor baseline [--include-future-releases]`: 現在見える案件を既知化
- `tcg-monitor dry-run`: 外部送信せず候補を確認
- `tcg-monitor run`: armed状態の時だけ通知
- `tcg-monitor arm`: 定期通知を有効化
- `tcg-monitor status`: 状態JSONを表示
- `tcg-monitor summary`: Actions用の監視結果を生成

共通オプションとして`--source SOURCE_ID`、`--game GAME_ID`、`--fixture-dir tests/fixtures`、`--config sites.yaml`、`--state monitor_state.json`、`--game-switch GAME_MONITOR_MODES.txt`、`--expedition-switch EXPEDITION_MODE.txt`が使えます。
