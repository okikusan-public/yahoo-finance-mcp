---
name: stock-data-collector
description: Collect historical or current stock prices, dividends, splits, shares, FX, and point-in-time universe data exclusively through Yahoo Finance MCP into reusable versioned packages. Use when a backtest needs a new dataset, a missing period or symbol must be acquired, or saved market-data coverage and Yahoo Finance MCP provenance must be inventoried.
---

# Stock Data Collector

## 責務

必要な株式データを収集し、再利用可能な版付きパッケージとして保存する。既存パッケージへの最新差分の結合は `$stock-data-updater`、売買ルールの実行は `$stock-backtester` に渡す。

## データソース必須条件

- 市場データの新規取得は **Yahoo Finance MCP経由だけ**で行う。
- エージェントが直接 `yfinance`、YahooのHTTP API、Web検索、Webスクレイピング、ブラウザ操作、他の金融APIを使って代替取得することを禁止する。
- Yahoo Finance MCPの内部実装が何であっても、エージェントは必ずMCPツール面を呼び出す。
- 作業開始前にYahoo Finance MCPのツールが呼び出し可能か確認する。ツールが見つからない、接続・認証に失敗する、必要項目を返せない場合は処理を停止し、`Yahoo Finance MCP利用不可` と報告する。
- MCPの空応答、部分応答、truncated、取得上限超過を別ソースや推計で補完しない。バッチ縮小または再試行でも解消しなければ未取得として停止する。
- 保存済みデータは、必要範囲の全行がYahoo Finance MCP由来だとmanifestで確認できる場合だけ再利用する。出典不明、`yfinance`直接取得、Web取得を含む範囲は利用しない。
- manifestにはMCPサーバー名、ツール名、引数、取得日時、取得範囲を保存する。

## Git管理方針

- このスキル定義と汎用ヘルパーだけを `skills/` 配下でGit管理する。
- 取得データ、途中ファイル、依頼固有の取得スクリプトは `.cache/backtests/<package-id>/` 配下へ保存し、Git管理しない。
- 依頼固有コードをリポジトリ直下の `scripts/` やプロダクトコードへ追加しない。
- 汎用化したい処理が生じても、自動で昇格させず、再利用性とテストを確認してから別変更として扱う。

## ワークフロー

### 1. データ契約を決める

次を明示する。

- 対象市場、銘柄または母集団、期間、基準通貨
- OHLC、調整後価格、配当、分割、株式数、為替などの必要項目
- 日次・月次などの粒度
- 過去時点で利用可能だった情報が必要か
- 保存期間と再利用予定

未確認項目を推計で埋めない。結果へ影響する場合は `unknown` として確認する。

### 2. 保存済みデータを探す

最初に実行する。

```bash
python skills/stock-data-collector/scripts/catalog_packages.py --root .cache/backtests
```

期間、銘柄、列、品質が足りる部分は再利用し、不足部分だけを取得する。

### 3. 不足データを収集する

- Yahoo Finance MCPの上限に合わせてバッチ化する。
- バッチごとにチェックポイントを保存し、再開可能にする。
- 失敗、空データ、truncatedを成功扱いしない。ツールが成功を返しても行数が0なら `empty` として扱い、呼び出し記録の `status` は `success` / `empty` / `truncated` / `failed` を区別する。空の銘柄は `coverage_report.json` の欠損として載せ、上場前か上場廃止かは推定せず `unknown` とする。
- 取得日時、ツール、引数、銘柄、失敗内容を記録する。
- 上場廃止、ティッカー変更、複数クラス、取引通貨を区別する。

過去時価総額や過去母集団を作る場合は、後から判明した情報を混ぜない。現在銘柄固定を過去へ適用した場合はPITと呼ばない。

### 4. 品質を検査する

銘柄数、期間、行数、欠損、重複、最短日・最長日、通貨、株式数カバレッジを確認する。価格調整、分割、配当、為替の整合も確認する。

Yahoo Finance MCPの価格は `auto_adjust=false` でも終値が分割調整済みで返り、株式数は各時点の報告値（分割前の基準）で返る。時価総額を作る場合は、分割のある銘柄で次を確認し、結果を `coverage_report.json` の `splitBasisCheck` に記録する。基準の食い違いを放置したまま時価総額を計算しない。

- 分割日の前営業日と当日で、[package-format.md](references/package-format.md) の式による時価総額の比が目安として0.8〜1.25に収まること。
- 外れる場合は、株式数が分割後の値へ更新される前の古い値を使っている、または二重に補正している可能性があるため、要確認として記録する。推計で補正しない。

### 5. 版付きパッケージを保存する

[package-format.md](references/package-format.md) に従い、`manifest.json`、coverage、データ、ハッシュを `.cache/backtests/<package-id>/` へ保存する。既存パッケージを上書きしない。

### 6. 引き渡す

パッケージID、期間、銘柄数、品質ラベル、欠損、保存場所を報告する。最新差分を結合する場合は `$stock-data-updater`、バックテストする場合は `$stock-backtester` を使う。
