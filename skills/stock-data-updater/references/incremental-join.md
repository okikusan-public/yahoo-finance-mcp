# 最新差分の結合仕様

## 出典ゲート

- 親と差分の必要範囲は、manifestでYahoo Finance MCP由来と確認できなければならない。
- 直接の `yfinance`、Web、HTTP API、他の金融API由来データは結合対象外とする。
- 行単位の出典が混在し、MCP由来範囲を分離できない場合は結合を拒否する。

## 標準結合キー

| データ | キー |
|---|---|
| 日次価格 | `symbol + date` |
| 配当 | `symbol + ex_date + amount + currency` |
| 分割 | `symbol + date + ratio` |
| 株式数 | `symbol + effective_date + known_at` |
| 為替 | `pair + date` |
| 月次母集団 | `month + region + symbol` |

ティッカー変更がある場合は、表示ティッカーではなく安定IDを優先し、対応表を保存する。

## 競合ポリシー

1. 同一キー・同一値は重複排除する。
2. 同一キー・異なる値は `conflicts.json` へ保存する。
3. 明示された訂正ソースだけを優先できる。
4. 採用値、旧値、両方の取得日時と出典を残す。
5. 調整後価格の再計算で過去値が変わる場合は、旧系列と混在させず系列全体を新しい版にする。
6. 丸め差（下記）は競合に数えず、`drift.json` に記録して親の値を保持する。

## 調整後価格の丸め差

取得範囲が違うと、調整後価格の重なり行が小数第4位程度だけ変わることがある（テストでは終値で0.0001〜0.0003）。これを毎回競合にしないため、次の規則で分ける。

- 対象は価格列（始値・高値・安値・終値・調整後終値）だけ。出来高、配当額、分割比率、株式数は完全一致を求め、許容差を使わない。
- 同一キーの相対差が `driftTolerance`（既定 `1e-4`）以内なら丸め差とする。`drift.json` にキー、親の値、差分の値、相対差を残し、親の値を保持する。
- 許容差を超える差は競合として `conflicts.json` へ出す。
- 1銘柄の重なり行の多くが同じ方向に一定比率ずれている場合は、配当の遡及調整などによる系列全体の再計算とみなし、規則5に従って系列全体を新しい版にする。丸め差として扱わない。
- 許容差は変えてよいが、変えた値と理由をmanifestの `update.driftTolerance` に残す。

## 新manifestの追加項目

```json
{
  "parentPackageId": "base-package",
  "update": {
    "through": "YYYY-MM-DD",
    "incrementPackageId": "increment-package",
    "addedRows": 0,
    "deduplicatedRows": 0,
    "conflictRows": 0,
    "driftRows": 0,
    "driftTolerance": 1e-4,
    "conflictPolicy": "report-only",
    "sourceProvider": "Yahoo Finance MCP",
    "sourceTools": ["tool-name"]
  }
}
```

元期間が変わっていないことを確認できない場合は、更新成功として扱わない。
