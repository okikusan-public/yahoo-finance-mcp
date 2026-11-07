---
name: stock-backtest-reviewer
description: Review and reconcile stock-backtest results using exact rule conditions, validation evidence, transaction attribution, data quality, and start-date sensitivity. Use when Codex must explain surprising performance, compare runs, identify why a result changed, audit whether comparisons are valid, or summarize lessons without altering the reported numbers.
---

# Stock Backtest Reviewer

## 責務

バックテスト結果を検算証跡と条件差からレビューし、確認できた事実、比較可否、差の要因、データ品質を分けて報告する。結果を改善するために別ルールの数字へ置き換えない。

## ワークフロー

### 1. 実行物を特定する

最低限、`rule.yaml`、`summary.json`、`validation.json`、取引明細、資産曲線を探す。可能なら `$stock-rule-set-manager` でルールセットID、版、SHAを解決する。

呼称だけで実行物を決めない。「高収益ルール」「現行ルール」「前回」と言われた場合は、期間、初期銘柄、税、業種、45%上限などを照合する。

### 2. 検算を先に確認する

`validation.allMatched` がfalse、検算ファイルがない、または会計恒等式が合わない場合は、確定成績としてレビューしない。未検算値は `未検算` と表示する。

### 3. 比較条件を照合する

[review-checklist.md](references/review-checklist.md) の比較表を埋め、比較を次のいずれかに分類する。

- `comparable`: ルール・データ・資金・税・費用が一致し、比較軸だけが違う
- `partially_comparable`: 既知の差があり、限定的な比較だけ可能
- `not_comparable`: 売買結果を左右する条件が異なる

ルールSHAが違う場合は、同一ルールの期間比較と呼ばない。

### 4. 数字をそのまま抽出する

総収益率、年率、最終資産、最大下落と日付、売買回数、平均保有日数、税、配当、費用、現金、ルール固有イベントを記録する。短期間では年率換算より期間収益率を先に示す。

### 5. 取引寄与を分解する

銘柄別実現損益、未実現損益、配当、税、為替、部分売却、損切り、トレンド売却、期末清算へ分解する。上位寄与と下位寄与を合計し、最終資産の恒等式と一致させる。

### 6. 差の原因を説明する

次の順に確認する。

1. ルール差
2. 母集団・分類・PIT品質の差
3. 期間と開始日の差
4. 初期保有の差
5. 税・配当・為替・費用の差
6. データ更新または欠損補完の差

開始時点ごとに現金から再開する1か月、6か月、年初来テストは、それぞれ別の資産経路である。同一資産曲線の部分期間収益と混同しない。

### 7. レビューを報告する

次の順で簡潔に示す。

1. **条件**: 期間、初期資金、ルール版、税、データ品質
2. **確認できた数字**: 生の結果
3. **比較可否**: `comparable` など
4. **差の要因**: 取引・ルール・データの順
5. **検算結果**: 通過項目と未確認事項
6. **見立て**: ユーザーが求めた場合だけ

## ガードレール

- 出力済みの数字を、別実装や別ルールの数字へ置き換えない。
- 推計で欠損を埋めず、確認できない点は `確認できず` とする。
- 好成績のルールを現行ルールとして自動採用しない。
- 期末清算が短期成績へ与える影響を明記する。
- サバイバーシップバイアス、現在分類の過去適用、非PITスナップショットを区別する。
- 改善案や推奨は、ユーザーが求めた場合にのみ結果レビューと分離して書く。
- Notionなどへの保存は、ユーザーの明示承認後だけ行う。
