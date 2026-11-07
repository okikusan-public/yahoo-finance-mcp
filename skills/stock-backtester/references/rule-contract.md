# 売買ルール契約

## 必須項目

- 戦略ID、版、正本、基準ケース
- データパッケージID、対象期間、母集団品質
- 初期現金、初期保有、開始方式、ウォームアップ
- 適格条件、買い発火条件、順位式、同点処理、参照窓
- 条件不成立時の現金待機、複数発火時の購入数
- シグナル日時、約定日時、価格
- 初期資金、最大保有数、サイズ方式、売買単位
- 現金不足、借入、売却代金再利用の扱い
- 全売却、一部売却、買い直し、入れ替え
- 税、配当、手数料、為替コスト
- 同日イベントの優先順位
- 比較指標、清算の有無
- ベンチマーク、通貨換算、配当の扱い
- 業種分類ソース、分類基準日、分類粒度、PIT品質
- 同一分類上限、最低分類数、未分類、配分、リバランスの扱い

## 仕様テンプレート

```yaml
strategy_id: example
version: v1
source_of_truth: user-confirmed
data_package_id: unknown

universe:
  quality: unverified
  selection: unknown

signal:
  eligibility_filter: unknown
  entry_trigger:
    operator: all
    conditions: []
  ranking:
    expression: unknown
    role: priority_only
  no_match_action: hold_cash
  multiple_match_policy: rank_then_fill_available_slots
  lookbacks: []
  signal_price: raw_close

execution:
  entry: next_symbol_trading_day_open
  exit: next_symbol_trading_day_open
  same_day_priority: unknown

portfolio:
  initial_cash_jpy: 1200000
  initial_positions: []
  startup_mode: signal_only
  warmup_policy: indicators_only
  max_positions: null
  sizing: unknown
  cash_shortfall: reduce_quantity
  borrowing: false
  diversification:
    classification_source: unknown
    classification_as_of: null
    classification_quality: unverified
    level: sector
    max_positions_per_category: null
    minimum_categories: null
    unclassified_policy: unknown
    allocation: equal_position
    rebalance: none

exit:
  full_exit: []
  partial_exit: []
  cooldown_business_days: 0

tax_and_income:
  capital_gains_tax: unknown
  dividends: unknown
  fees: unknown
  fx_cost: unknown

evaluation:
  periods: []
  metrics: [total_return, cagr, max_drawdown, trade_count, average_holding_days]
  benchmarks:
    - symbol: ^N225
      currency: JPY
      return_type: price
    - symbol: ^GSPC
      currency: USD
      convert_to: JPY
      return_type: price

unknowns: []
```

## 開始方式

- `signal_only`: 現金・保有なしで開始し、期間内に `entry_trigger` が成立した銘柄だけを購入する。既定値とする。
- `seed_on_start`: 開始時点の順位から保有枠を埋める比較用方式。ユーザーが明示した場合だけ使う。
- `carry_in_positions`: 期間開始前から続く実運用経路を再生する方式。取得価格、数量、現金、税状態を明示する。

`eligibility_filter` は母集団を絞るだけで、買いを発火させない。`ranking` は複数の発火候補を並べるだけで、単独では買い条件にならない。発火候補が0件なら取引せず、現金を維持する。

`>` と `>=`、営業日と暦日、未調整値と調整値を区別する。比較のための仮定を `confirmed` にしない。
