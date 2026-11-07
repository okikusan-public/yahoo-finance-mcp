# ルールセット契約

## 必須メタデータ

- `ruleset_id`: 戦略を表す安定ID
- `version`: 内容が変わるたびに更新する版
- `status`: `draft`、`confirmed`、`superseded`
- `known_at`: この版を知り得た日時
- `effective_from`: 実運用へ適用する場合の開始日
- `source_of_truth`: Notion、ユーザー確認、既存実行物など
- `parent_version`: 派生元
- `confirmed`: 確認済み条件
- `assumptions`: 明示的な仮定
- `unknowns`: 未確認事項

## 売買条件

- 母集団、地域、時価総額順位、業種制約、分類基準日、PIT品質
- 候補条件、順位式、参照窓、同点処理
- シグナル日時、約定日時、価格、休場日の扱い
- 初期資金、最大保有数、購入額、売買単位、現金不足
- 全売却、部分売却、45%上限、損切り、買い直し禁止
- 入れ替え条件、1日上限、同日イベント順序
- 税率、課税単位、損益通算、配当税、税金不足
- 手数料、為替換算、為替コスト、配当、分割
- 期間末清算、ベンチマーク、必要指標

## 推奨YAML

```yaml
ruleset_id: example-strategy
version: v1
status: confirmed
known_at: 2026-11-03T12:00:00Z
effective_from: null
source_of_truth: user-confirmed chat
parent_version: null

universe:
  selection: monthly market-cap universe
  regions: {us: 100, jp: 100, sg: 40}
  industries: []
  quality: approximate PIT

signal:
  candidate_filter: []
  ranking: unknown
  lookbacks: []

execution:
  entry: next_symbol_trading_day_open
  exit: next_symbol_trading_day_open
  same_day_priority: unknown

portfolio:
  initial_cash_jpy: 1200000
  max_positions: 3
  sizing: total_assets_at_signal_close / 3
  maximum_position_weight: null
  replacement_when_full: false
  borrowing: false

exit:
  full_exit: []
  partial_exit: []
  cooldown_business_days: 0

tax_and_income:
  capital_gains_tax: unknown
  dividends: unknown
  fees: 0
  fx_cost: 0

evaluation:
  liquidation_at_end: true
  metrics: []

confirmed: []
assumptions: []
unknowns: []
```

## 版の判定

次のいずれかが変われば新しい版にする。

- 対象銘柄が変わる条件
- 売買タイミングまたは価格
- 購入数量または保有上限
- 売却、損切り、45%調整、入れ替え
- 税、配当、費用、為替
- 同日処理順序または期間末清算

説明文、表示名、誤字だけの修正（行頭コメント・空行・行末空白）は `contentSha256` を変えないため、新しい版にしない。`ruleSha256`（ファイルそのもの）は変わるので、改ざん検出にだけ使う。インラインコメントの追加やキー順の入れ替えは正規化されず、別内容として扱われる。

## 比較可能性

2結果を「同一ルール比較」と呼べるのは、ルールセットID、版、`contentSha256` が一致する場合だけとする。期間だけを変えた比較では、各開始時点で現金から再開した独立シミュレーションか、同一資産曲線の部分期間かを明記する。
