#!/usr/bin/env python3
"""List reusable backtest data packages without loading large artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"_error": str(error)}
    return value if isinstance(value, dict) else {"_error": "manifest is not an object"}


def period_text(manifest: dict[str, Any]) -> str:
    period = manifest.get("period")
    if not isinstance(period, dict):
        return "unknown"
    start = (
        period.get("start")
        or period.get("priceStart")
        or period.get("universeStart")
        or period.get("warmupStart")
    )
    end = period.get("end") or period.get("priceEnd") or period.get("universeEnd") or period.get("testEnd")
    if not end and period.get("endExclusive"):
        end = f"<{period['endExclusive']}"  # exclusive upper bound
    if start or end:
        return f"{start or '?'}..{end or '?'}"
    return "unknown"


def quality_text(manifest: dict[str, Any]) -> str:
    universe = manifest.get("universe")
    if isinstance(universe, dict) and universe.get("quality"):
        return str(universe["quality"])
    for key in ("dataQuality", "data_quality", "universeQuality"):
        if manifest.get(key):
            return str(manifest[key])
    caveats = manifest.get("caveats")
    if isinstance(caveats, list):
        caveat_text = " ".join(str(item) for item in caveats)
        if "近似PIT" in caveat_text or "approximate PIT" in caveat_text:
            return "approximate PIT"
    quality_flags = manifest.get("quality_flags") or manifest.get("qualityFlags")
    if isinstance(quality_flags, list):
        flag_text = " ".join(str(item) for item in quality_flags)
        if "近似" in flag_text or "代理" in flag_text or "PIT" in flag_text:
            return "approximate PIT"
    return "unverified"


def coverage_value(coverage: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in coverage:
            return coverage[key]
    return None


def package_row(manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict):
        coverage = {}
    files = manifest.get("files")
    if not isinstance(files, list):
        files = []
    file_names = []
    for item in files:
        if isinstance(item, str):
            file_names.append(item)
        elif isinstance(item, dict) and item.get("name"):
            file_names.append(str(item["name"]))
    return {
        "path": str(manifest_path.parent),
        "packageId": (
            manifest.get("packageId")
            or manifest.get("package_id")
            or manifest_path.parent.name
        ),
        "period": period_text(manifest),
        "quality": quality_text(manifest),
        "dailyPriceSymbols": coverage_value(
            coverage,
            "daily_price_symbols",
            "dailyPriceSymbols",
            "availableSymbols",
            "acquiredSymbols",
        ),
        "monthlyUniverses": coverage_value(
            coverage, "monthly_universes", "monthlyUniverses"
        ),
        "fileCount": len(files),
        "files": file_names,
        "error": manifest.get("_error"),
    }


def discover(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    rows = [package_row(path) for path in sorted(root.rglob("manifest.json"))]
    return sorted(rows, key=lambda row: (row["period"], row["packageId"]))


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No manifest.json files found.")
        return
    headers = ("packageId", "period", "quality", "prices", "months", "files")
    values = []
    for row in rows:
        values.append(
            (
                str(row["packageId"]),
                str(row["period"]),
                str(row["quality"]),
                "-" if row["dailyPriceSymbols"] is None else str(row["dailyPriceSymbols"]),
                "-" if row["monthlyUniverses"] is None else str(row["monthlyUniverses"]),
                str(row["fileCount"]),
            )
        )
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in values))
        for index in range(len(headers))
    ]
    print("  ".join(headers[index].ljust(widths[index]) for index in range(len(headers))))
    print("  ".join("-" * width for width in widths))
    for row in values:
        print("  ".join(row[index].ljust(widths[index]) for index in range(len(headers))))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".cache/backtests"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    rows = discover(args.root)
    if args.as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
