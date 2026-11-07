---
name: stock-strategy-loop-optimizer
description: Iteratively discover, backtest, and validate candidate stock-trading rules against an explicit profit target while preserving every failed trial and separating in-sample optimization from out-of-sample evidence. Use when Codex is asked to loop until a backtest turns positive, self-improve or optimize a losing strategy, search for rules suggested by failure analysis, or build a reproducible rule-search process without silently adopting an overfit result.
---

# Stock Strategy Loop Optimizer

## 責務

損失原因から候補ルールを生成し、定めた予算内で反復テストする。プラス収益を探索目標にしてよいが、開発期間だけのプラスを再現可能な戦略と呼ばない。

次のスキルと連携する。

- 基準ルールの解決と版管理: `$stock-rule-set-manager`
- データパッケージの取得・更新: `$stock-data-collector`、`$stock-data-updater`
- 各試行の実行と検算: `$stock-backtester`
- 損失寄与と条件差の分析: `$stock-backtest-reviewer`

## Git管理方針

- このスキル定義だけをGit管理する。
- 探索ごとの設定、ランナー、全試行、ログ、結果は `.cache/backtests/<package-id>/searches/<search-id>/` に置く。
- 個別探索のコードや勝ち残ったルールを、共通エンジンや現行ルールへ自動昇格しない。

## ワークフロー

### 1. 探索契約を固定する

[search-contract.md](references/search-contract.md) に従い、次を探索前に固定する。

- 基準ルールセットID・版・SHA
- データパッケージID・品質・母集団
- 初期資金、税、配当、費用、為替、開始方式
- 開発期間、検証期間、ロックする最終確認期間
- 目標指標、目標値、最大下落などの制約
- 変更可能なルール項目と変更禁止項目
- 最大試行数、最大時間、改善停止回数

ユーザーが「何が何でもプラス」と指定した場合も、`総収益率 > 0` だけを成功条件にしない。売買なし、ほぼ全期間現金、1回の偶然の取引、期間の都合のよい切り直しを退化解として除外する。

既に結果を見た期間を最終確認期間に使わない。未使用期間がなければ、最終確認は `確認できず` とし、開発期間内のプラスと明記する。

### 2. 基準結果を再現する

基準ルールを一度そのまま実行し、既存結果との差がゼロか確認する。検算未通過の基準から探索を始めない。

### 3. 失敗トリガーを抽出する

`$stock-backtest-reviewer` で銘柄、業種、買い時点、最大逆行、出口、税、現金、開始日感応度へ分解する。[failure-triggers.md](references/failure-triggers.md) を使い、観測事実と候補ルールを対応付ける。

候補は次の順に並べる。

1. 会計・データ・未来参照の修正
2. 集中・資金・最大損失の制御
3. 買いトリガーと過熱回避
4. 出口と再購入待機
5. サイズと保有数
6. 母集団・業種・ランキング

結果を見て有利だった銘柄を直接指定する候補は作らない。

### 4. 一変数ずつ反復する

各試行で原則一つのルール要素だけを変更する。各試行を実行・検算し、成功・失敗を問わず台帳へ追記する。

- 親試行、仮説、設定差分、ルールSHAを保存する。
- 総収益率だけでなく、最大下落、取引数、投資日数、現金比率、開始日感応度、ルール複雑度を保存する。
- 無効な試行、検算不一致、データ欠損も削除しない。
- 独立に改善した要素だけを次段階で組み合わせる。
- 同値なら単純なルールを優先する。
- 乱数を使う場合はseedを固定する。

開発期間で目標を超えても直ちに停止せず、事前に定めた制約と検証期間を確認する。

### 5. 時系列で昇格判定する

候補の状態を次のいずれかで記録する。

- `in_sample_positive`: 開発期間だけで目標達成
- `validation_positive`: 開発・検証期間で制約を満たす
- `robust_candidate`: ルール凍結後、未使用の最終確認期間でも制約を満たす
- `rejected`: 目標、制約、検算のいずれかを満たさない
- `budget_exhausted`: 探索予算内で候補なし

最終確認期間を見た後にルールを変更した場合、その期間を開発済みへ降格させる。再び同じ期間を最終確認として扱わない。

### 6. 停止する

次のいずれかでループを終了する。

- `robust_candidate` が得られた
- 最大試行数または最大時間へ到達した
- 規定回数連続で検証指標が改善しない
- データ品質または検算不一致で継続不能になった

プラスになるまで期間や母集団を無制限に変更し続けない。候補がなければ、その事実を結果として報告する。

### 7. 報告する

次を分けて示す。

1. 探索契約とデータ品質
2. 基準結果
3. 試行数と変更したルール族
4. 開発期間の最高結果
5. 検証期間の最高結果
6. 最終確認期間の一度限りの結果
7. 最大下落、取引数、投資日数、複雑度
8. 全試行台帳と検算結果
9. 候補状態と未確認事項

「プラスを発見」と「将来もプラスが期待できる」を混同しない。現行ルールへの採用やNotionへの保存は、ユーザーの明示承認後だけ行う。

## ガードレール

- 利益を保証しない。
- 失敗試行を隠さず、最良結果だけを抜き出さない。
- 検証期間や最終確認期間へ合わせて再調整しない。
- 税、費用、配当、為替、期間、母集団を試行間で無断変更しない。
- 現在分類の過去適用、サバイバーシップバイアス、近似PITを明記する。
- 現金維持だけで損失を避けたケースを、十分な投資実績なしに成功扱いしない。
- 候補ルールを現行ルールとして自動採用しない。
