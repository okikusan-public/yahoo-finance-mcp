---
name: stock-rule-set-manager
description: Create, version, compare, freeze, and resolve exact stock-trading rule sets before backtests. Use when Codex must save a strategy definition, identify the current or authoritative rule, compare two rule versions, prevent rules from being mixed across runs, or hand an immutable rule snapshot to the stock backtester.
---

# Stock Rule Set Manager

## 責務

売買ルールを再実行可能な版付きスナップショットとして管理し、バックテストへ渡す条件を一意にする。データ収集やバックテスト実行は行わず、それぞれ `$stock-data-collector`、`$stock-data-updater`、`$stock-backtester` に渡す。

## 保存方針

- スキル定義と汎用スクリプトだけをGit管理する。
- 個別ルールは原則 `.cache/backtests/rulesets/<ruleset-id>/<version>/` に保存する。
- 登録済み版を上書きしない。変更時は必ず新しい版を作る。
- Notionなど外部サービスへの書き込みは、ユーザーの明示承認後だけ行う。
- ユーザーが正本を指定した場合はそれを優先し、競合内容を黙って統合しない。

## ワークフロー

### 1. 正本と目的を固定する

正本、対象戦略、基準版、用途、適用開始日を確認する。「現行」「前回」「高収益ルール」などの呼称だけで版を決めない。候補が複数ある場合は、期間、初期銘柄、税、業種制約などから照合し、確認できない場合は `unknown` とする。

### 2. ルールを正規化する

[ruleset-contract.md](references/ruleset-contract.md) に従い、次を分離する。

- `confirmed`: 正本またはユーザーが明示した条件
- `assumption`: 比較・実行用の明示的な仮定
- `unknown`: 結果を変え得る未確認事項

不等号、参照日、約定日、税計算、45%上限、業種制約、損切り、入れ替えの有無を省略しない。

### 3. 版を発行する

`ruleset_id`、`version`、`status`、`known_at`、`source_of_truth`、親版を決める。`confirmed` 版は内容を固定し、1項目でも変わる場合は新しい版にする。

```bash
python skills/stock-rule-set-manager/scripts/ruleset_registry.py register \
  --source /path/to/rule.yaml \
  --ruleset-id cap45-six-industries \
  --version v1 \
  --status confirmed \
  --known-at 2026-11-03T12:00:00Z \
  --source-of-truth "user-confirmed chat"
```

登録時に `rule.yaml`、`manifest.json`、2種類のSHA-256を保存する。

- `ruleSha256`: ファイルそのもののSHA-256。登録後の書き換え検出に使う。
- `contentSha256`: 空行・行頭コメント・行末空白を除いた内容のSHA-256。ルールの同一性判定に使う（インラインコメントやキー順は正規化しない）。

既存版への登録は失敗させる。内容が既存版と同一（コメントや空行だけの差）の場合も失敗する。状態の変更などでどうしても登録するときだけ `--allow-duplicate-content` を付ける。`--known-at` と `--effective-from` は `YYYY-MM-DD` またはISO 8601日時で指定する。

### 4. 実行前に版を解決する

まず登録済みファイルが書き換えられていないことを確認する。

```bash
python skills/stock-rule-set-manager/scripts/ruleset_registry.py verify \
  --ruleset-id cap45-six-industries --version v1
```

`MISMATCH` または `missing-file` の版は使わない（終了コード1）。引数を省略すると全版を確認する。`show` と `catalog` も同じ結果を `integrity` に表示する。

バックテスト前に次を1行で固定する。

```text
ruleset_id + version + rule_sha256 + content_sha256 + source_of_truth + status
```

`status` が `draft`、または `unknown` が残る場合は、その旨を実行条件へ残す。別版を暗黙に代用しない。

### 5. 差分を確認する

```bash
python skills/stock-rule-set-manager/scripts/ruleset_registry.py catalog
python skills/stock-rule-set-manager/scripts/ruleset_registry.py diff \
  --left .cache/backtests/rulesets/cap45-six-industries/v1 \
  --right .cache/backtests/rulesets/pullback-momentum/v1
```

差分は、少なくとも母集団、買い条件、順位式、売却条件、サイズ、最大保有数、45%上限、税、費用、期間末清算に分類する。`diff` の出力にある `contentIdentical` が `true` なら、コメントや空行だけの差でありルールは同じ。

### 6. バックテストへ渡す

`$stock-backtester` の `rule.yaml` にルールセットID、版、`ruleSha256` と `contentSha256` を記録する。実行後に要約値だけでルールを推定し直さない。

## ガードレール

- 同じ期間・同じデータでも、`contentSha256` が違えば「同一ルール再実行」と扱わない。コメントだけの差で `ruleSha256` だけが違う場合は、ルールとしては同一だが、実行物には使った版の `ruleSha256` を記録する。
- `verify` が通らない版を、バックテストやレビューの根拠にしない。
- ルール名が同じでも、業種制約や損切りなどが違えば新しい版にする。
- 結果が良い版を「現行」と自動認定しない。
- 過去結果を再現するときは、その実行物のルールSHAを優先する。
- ルールに存在しない条件を会話履歴から補完しない。
- 個別ランナーや結果はGit管理せず、`.cache/backtests/<package-id>/runs/` に置く。
