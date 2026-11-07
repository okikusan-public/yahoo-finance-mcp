---
name: stock-data-updater
description: Join Yahoo Finance MCP-sourced market data onto an existing Yahoo Finance MCP-provenance versioned stock-data package without mutating the source package. Use when compliant saved historical data must be extended through a newer date, refreshed with latest prices or corporate actions, or reconciled with a new MCP incremental fragment.
---

# Stock Data Updater

## 責務

保存済みデータと最新差分を安全に結合し、新しい版のパッケージを作る。初回収集は `$stock-data-collector`、結合後の検証実行は `$stock-backtester` に渡す。

## データソース必須条件

- 親パッケージと追加差分の必要範囲が、どちらもYahoo Finance MCP由来だとmanifestで確認できる場合だけ更新する。
- 最新差分は `$stock-data-collector` を使い、Yahoo Finance MCP経由で取得する。
- エージェントが直接 `yfinance`、YahooのHTTP API、Web検索、Webスクレイピング、ブラウザ操作、他の金融APIから差分を取得・補完することを禁止する。
- Yahoo Finance MCPが利用できない、または必要な差分を完全取得できない場合は更新を停止する。別ソースへの自動切り替えは禁止する。
- 出典不明または非MCP行が混在する場合、MCP由来行だけに切り分けられない限り結合しない。

## Git管理方針

- 入力・出力パッケージ、差分データ、結合用の一時スクリプトは `.cache/backtests/` 配下へ保存し、Git管理しない。
- 依頼固有の結合コードをリポジトリ直下の `scripts/` へ追加しない。
- 元パッケージを上書きしない。必ず新しいパッケージIDを作る。

## ワークフロー

### 1. 入力を固定する

次を決める。

- 元パッケージID
- 更新終了日
- 追加対象の銘柄、母集団、データ項目
- 重複期間の競合ポリシー
- 更新時点で利用可能だった情報を保つ必要性

最新差分が未取得なら `$stock-data-collector` で差分パッケージを作る。

親・差分manifestの `source.provider`、MCPサーバー名、ツール名、取得範囲を確認し、いずれかが欠ける場合は開始しない。

### 2. schemaと結合キーを照合する

`manifest.json` を読み、列、型、通貨、価格調整方式、タイムゾーン、銘柄IDを合わせる。標準キーと競合処理は [incremental-join.md](references/incremental-join.md) に従う。

### 3. 新しい版へ結合する

元パッケージを複製せず、必要ファイルを新しいパッケージへ生成する。依頼固有コードが必要なら次へ置く。

```text
.cache/backtests/<new-package-id>/work/
```

- 新しい日付は追加する。
- 同一キー・同一値は重複排除する。
- 同一キー・異なる値は勝手に上書きせず、競合一覧へ出す。ただし価格の差が許容差（既定 相対`1e-4`）以内の丸め差なら、`drift.json` に記録して親の値を保持し、競合に数えない（[incremental-join.md](references/incremental-join.md)）。
- 訂正値を採用する場合は、取得日時、旧値、新値、出典を残す。
- 月次母集団や株式数は `known_at` を保持し、未来情報を過去へ伝播しない。

### 4. 整合性を確認する

期間の連続性、重複キー、欠損、銘柄数、配当・分割の二重計上、株式数の逆行、為替日付を検査する。元期間の値が意図せず変わっていないことをハッシュまたは差分で確認する。

### 5. manifestを更新する

新しいパッケージに `parentPackageId`、追加期間、差分元、競合ポリシー、変更件数（追加・重複排除・丸め差・競合）、許容差、ファイルハッシュを保存する。元パッケージは変更しない。

### 6. 結果を報告する

元と新パッケージのID、追加期間・行数、重複排除数、丸め差の件数、競合件数、欠損、品質ラベルの変化を報告する。バックテストは新パッケージIDを指定して `$stock-backtester` で実行する。
