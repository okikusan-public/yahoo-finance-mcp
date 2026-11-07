# 探索契約

探索前に次の形式で `search.yaml` を作る。未指定値を推測で埋める場合は `assumption` と記録する。

```yaml
search_id: example-search-v1
base_ruleset:
  id: cap45-example
  version: v1
  sha256: null
data:
  package_id: null
  quality: unknown
capital:
  initial_jpy: 1200000
  startup_mode: signal_only
objective:
  tax_case: taxed
  metric: total_return
  target: 0.0
  constraints:
    maximum_drawdown_floor: -0.30
    minimum_completed_sales: 3
    minimum_invested_day_ratio: 0.20
    validation_must_be_positive: true
periods:
  development: {start: null, end: null}
  validation: {start: null, end: null}
  holdout: {start: null, end: null, locked: true}
  already_inspected: []
search_space:
  mutable: []
  immutable:
    - data_package
    - tax_model
    - execution_timing
budget:
  max_trials: 30
  max_minutes: 90
  stop_after_non_improving_trials: 5
selection:
  primary: validation_total_return
  tie_breakers:
    - smaller_maximum_drawdown
    - fewer_rule_changes
    - lower_turnover
```

## 退化解の防止

目標値を超えても、次に該当する試行を成功扱いしない。

- 買いがない、または売却完了数が最低条件未満
- 投資日数比率が最低条件未満
- 期間や終了日を結果確認後に変更した
- 母集団を結果確認後の勝ち銘柄へ限定した
- 税、費用、配当、為替条件を基準から緩めた
- 検算が通っていない

最低取引数や投資日数比率は戦略特性に合わせて探索前に固定する。長期保有戦略へ短期戦略と同じ閾値を強制しない。

## 出力構造

```text
.cache/backtests/<package-id>/searches/<search-id>/
├── search.yaml
├── trials.jsonl
├── leaderboard.csv
├── candidates/
│   └── <trial-id>/
│       ├── rule.yaml
│       ├── summary.json
│       └── validation.json
├── holdout/
│   └── frozen-candidate/
├── search-summary.json
└── validation.json
```

`trials.jsonl` へ全試行を追記し、上書きしない。各行に最低限次を含める。

```json
{
  "trialId": "t001",
  "parentTrialId": "baseline",
  "hypothesis": "limit one holding per business driver",
  "ruleDiff": {},
  "ruleSha256": null,
  "development": {},
  "validation": {},
  "holdout": null,
  "validationPassed": true,
  "status": "rejected"
}
```

## 期間の扱い

- 時系列順に開発、検証、最終確認を分ける。
- 既に人間またはエージェントが詳細を見た期間を `already_inspected` に入れる。
- 最終確認期間は候補を凍結してから一度だけ使う。
- 十分な未使用期間がなければ、ローリング検証を使い、`robust_candidate` ではなく `validation_positive` までに留める。

## 順位付け

まず検算とハード制約で失格試行を除く。残りを検証期間の主指標で並べ、最大下落、変更数、売買回転率を同点処理に使う。最終確認結果を探索順位へ逆流させない。
