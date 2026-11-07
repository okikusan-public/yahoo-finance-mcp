# データパッケージ仕様

## 保存先

```text
.cache/backtests/<package-id>/
├── manifest.json
├── coverage_report.json
├── candidates.json.gz
├── monthly_universes.json.gz
├── daily_prices.json.gz
├── shares.json.gz
├── splits.json.gz
└── fx.json.gz
```

必要なファイルだけを作る。大容量ファイルはgzip JSON、反復分析用ならParquetも使用できる。

## manifest必須項目

```json
{
  "schemaVersion": 1,
  "packageId": "example_YYYYMMDD",
  "createdAt": "ISO-8601",
  "period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "source": {
    "provider": "Yahoo Finance MCP",
    "server": "configured-server-name",
    "tools": ["tool-name"],
    "acquiredAt": "ISO-8601",
    "arguments": [],
    "coverage": {}
  },
  "universe": {"quality": "unverified", "note": ""},
  "coverage": {},
  "joinKeys": {"prices": ["symbol", "date"]},
  "files": [
    {"name": "daily_prices.json.gz", "bytes": 0, "sha256": "", "description": ""}
  ]
}
```

`source.provider` が `Yahoo Finance MCP` でない、または必要範囲のツール呼び出し記録を確認できないパッケージは、このスキル群では利用不可とする。直接の `yfinance`、Web、HTTP API取得をYahoo Finance MCP由来として記録してはならない。

### `catalog_packages.py` が読む項目

- 期間: `period.start`（なければ `priceStart` / `universeStart` / `warmupStart`）から `period.end`（なければ `priceEnd` / `universeEnd` / `testEnd`）。`period.end` はデータの最終日（その日を含む）。終了日を含めない取得は `period.endExclusive` に書いてもよく、一覧には `<日付` と表示される。
- 銘柄数: `coverage.dailyPriceSymbols`（なければ `availableSymbols` / `acquiredSymbols`）。
- 月次母集団数: `coverage.monthlyUniverses`。
- 品質: `universe.quality`。
- これらが欠けても動くが、一覧の該当列が `unknown` / `-` になり、保存済みデータの検索で見つけにくくなる。

### 価格・株式数の基準

分割のある銘柄を扱うパッケージは、`manifest.json` に次を残す。

```json
{
  "adjustment": {
    "priceBasis": "split_adjusted_close",
    "sharesBasis": "as_reported_at_effective_month"
  }
}
```

`coverage_report.json` には、失敗した銘柄（`failures`）、空だった銘柄（`emptySymbols`）、truncatedだった銘柄（`truncatedSymbols`）、分割基準の検査結果（`splitBasisCheck`）を残す。

## 品質ラベル

- `PIT confirmed`: 当時利用可能だった母集団・株式数・価格を確認済み
- `approximate PIT`: 当時資料を使うが補間や対応付けに近似あり
- `current survivors applied historically`: 現在銘柄を過去へ固定適用
- `unverified`: 出典または参照可能時点を確認できず

## 過去時価総額

原則は次の組み合わせを使う。

```text
market_cap(date) = split_adjusted_close(date)
                 × shares_outstanding(known_at_or_before_date)
                 × future_split_factor(date)

future_split_factor(date) = date より後に効力が生じた分割比率の積
```

終値が分割調整済みで株式数が当時の報告値（分割前の基準）のとき、`future_split_factor` を掛けて株式数を終値の基準へそろえる。分割調整済み価格と未調整株式数をそのまま掛けない。遡及して分割調整済みの株式数が返る銘柄では `future_split_factor` を掛けず、二重に補正しない。どちらの基準かは、分割日をまたぐ株式数に分割比率どおりの段差があるかで判定し、判定できなければ `unknown` として時価総額に使わない。株式数を前方保持する場合は近似として記録する。

分割日の前営業日と当日で時価総額が不連続になる（目安: 比が0.8〜1.25の外）場合は、基準の食い違いとして `splitBasisCheck` に残し、時価総額に使う前に解消する。
