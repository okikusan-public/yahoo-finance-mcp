from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path(".cache/backtests/rulesets")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?$"
)
CONTENT_NORMALIZATION = (
    "v1: drop blank lines and full-line '#' comments, strip trailing whitespace"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_text(text: str) -> str:
    """Rule text without blank lines, full-line comments, or trailing spaces.

    Inline comments and key order are not normalized. A '#' line inside a YAML
    multi-line string is also dropped.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            continue
        lines.append(stripped)
    return "\n".join(lines) + "\n"


def content_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return hashlib.sha256(normalized_text(text).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_name(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not pattern.fullmatch(value):
        raise SystemExit(f"Invalid {label}: {value}")


def validate_timestamp(value: str | None, label: str) -> None:
    if value is None:
        return
    if not TIMESTAMP_PATTERN.fullmatch(value):
        raise SystemExit(
            f"Invalid {label}: {value} (expected YYYY-MM-DD or ISO 8601 datetime)"
        )
    try:
        datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"Invalid {label}: {value} (not a calendar date)") from None


def resolve_rule(path: Path) -> Path:
    candidate = path / "rule.yaml" if path.is_dir() else path
    if not candidate.is_file():
        raise SystemExit(f"Rule file not found: {candidate}")
    return candidate


def integrity(target: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Compare the stored rule file against the SHA-256 recorded at registration."""
    rule_path = target / manifest.get("ruleFile", "rule.yaml")
    if not rule_path.is_file():
        return {"status": "missing-file"}
    actual = sha256(rule_path)
    expected = manifest.get("ruleSha256")
    result: dict[str, Any] = {
        "status": "ok" if actual == expected else "MISMATCH",
        "expectedRuleSha256": expected,
        "actualRuleSha256": actual,
    }
    stored_content = manifest.get("contentSha256")
    if stored_content is not None:
        result["contentSha256Matches"] = content_sha256(rule_path) == stored_content
    return result


def find_duplicate_content(strategy_dir: Path, digest: str) -> str | None:
    for path in sorted(strategy_dir.glob("*/manifest.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rule_path = path.parent / value.get("ruleFile", "rule.yaml")
        stored = value.get("contentSha256")
        if stored is None and rule_path.is_file():
            stored = content_sha256(rule_path)  # versions registered before contentSha256 existed
        if stored == digest:
            return path.parent.name
    return None


def register(args: argparse.Namespace) -> None:
    validate_name(args.ruleset_id, ID_PATTERN, "ruleset id")
    validate_name(args.version, VERSION_PATTERN, "version")
    validate_timestamp(args.known_at, "known-at")
    validate_timestamp(args.effective_from, "effective-from")
    source = resolve_rule(Path(args.source))
    root = Path(args.root)
    strategy_dir = root / args.ruleset_id
    target = strategy_dir / args.version
    if target.exists():
        raise SystemExit(f"Ruleset version already exists: {target}")
    digest = content_sha256(source)
    duplicate = find_duplicate_content(strategy_dir, digest)
    if duplicate is not None and not args.allow_duplicate_content:
        raise SystemExit(
            f"Rule content is identical to existing version {duplicate} "
            "(only comments or blank lines differ). Reuse that version, or pass "
            "--allow-duplicate-content to register anyway (e.g. to change its status)."
        )
    strategy_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{args.version}.", dir=strategy_dir))
    try:
        rule_path = temporary / "rule.yaml"
        shutil.copy2(source, rule_path)
        manifest = {
            "schemaVersion": 1,
            "rulesetId": args.ruleset_id,
            "version": args.version,
            "status": args.status,
            "knownAt": args.known_at,
            "effectiveFrom": args.effective_from,
            "sourceOfTruth": args.source_of_truth,
            "parentVersion": args.parent_version,
            "note": args.note,
            "registeredAt": datetime.now(timezone.utc).isoformat(),
            "ruleFile": "rule.yaml",
            "ruleSha256": sha256(rule_path),
            "contentSha256": digest,
            "contentNormalization": CONTENT_NORMALIZATION,
        }
        write_json(temporary / "manifest.json", manifest)
        temporary.rename(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(json.dumps(manifest | {"path": str(target)}, ensure_ascii=False, indent=2))


def catalog(args: argparse.Namespace) -> None:
    root = Path(args.root)
    rows = []
    if root.exists():
        for path in sorted(root.glob("*/*/manifest.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            rows.append(
                value
                | {
                    "path": str(path.parent),
                    "integrity": integrity(path.parent, value)["status"],
                }
            )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def show(args: argparse.Namespace) -> None:
    validate_name(args.ruleset_id, ID_PATTERN, "ruleset id")
    validate_name(args.version, VERSION_PATTERN, "version")
    target = Path(args.root) / args.ruleset_id / args.version
    manifest_path = target / "manifest.json"
    rule_path = target / "rule.yaml"
    if not manifest_path.is_file() or not rule_path.is_file():
        raise SystemExit(f"Ruleset version not found: {target}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    check = integrity(target, manifest)
    value = {
        "manifest": manifest,
        "integrity": check,
        "rule": rule_path.read_text(encoding="utf-8"),
    }
    if check["status"] != "ok":
        print(
            f"WARNING: stored rule does not match the registered SHA-256 ({check['status']})",
            file=sys.stderr,
        )
    print(json.dumps(value, ensure_ascii=False, indent=2))


def verify(args: argparse.Namespace) -> None:
    root = Path(args.root)
    if args.ruleset_id or args.version:
        if not (args.ruleset_id and args.version):
            raise SystemExit("Pass both --ruleset-id and --version, or neither to verify all.")
        validate_name(args.ruleset_id, ID_PATTERN, "ruleset id")
        validate_name(args.version, VERSION_PATTERN, "version")
        manifests = [root / args.ruleset_id / args.version / "manifest.json"]
    else:
        manifests = sorted(root.glob("*/*/manifest.json")) if root.exists() else []
    rows = []
    for path in manifests:
        if not path.is_file():
            raise SystemExit(f"Ruleset version not found: {path.parent}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "rulesetId": manifest.get("rulesetId"),
                "version": manifest.get("version"),
                **integrity(path.parent, manifest),
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    if any(row["status"] != "ok" for row in rows):
        raise SystemExit(1)


def diff(args: argparse.Namespace) -> None:
    left = resolve_rule(Path(args.left))
    right = resolve_rule(Path(args.right))
    left_lines = left.read_text(encoding="utf-8").splitlines(keepends=True)
    right_lines = right.read_text(encoding="utf-8").splitlines(keepends=True)
    header = {
        "left": str(left),
        "leftSha256": sha256(left),
        "leftContentSha256": content_sha256(left),
        "right": str(right),
        "rightSha256": sha256(right),
        "rightContentSha256": content_sha256(right),
        "identical": left_lines == right_lines,
        "contentIdentical": content_sha256(left) == content_sha256(right),
    }
    print(json.dumps(header, ensure_ascii=False, indent=2))
    print(
        "".join(
            difflib.unified_diff(
                left_lines,
                right_lines,
                fromfile=str(left),
                tofile=str(right),
            )
        ),
        end="",
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    subparsers = value.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser("register")
    register_parser.add_argument("--source", required=True)
    register_parser.add_argument("--root", default=str(DEFAULT_ROOT))
    register_parser.add_argument("--ruleset-id", required=True)
    register_parser.add_argument("--version", required=True)
    register_parser.add_argument(
        "--status",
        choices=("draft", "confirmed", "superseded"),
        required=True,
    )
    register_parser.add_argument("--known-at", required=True)
    register_parser.add_argument("--source-of-truth", required=True)
    register_parser.add_argument("--effective-from")
    register_parser.add_argument("--parent-version")
    register_parser.add_argument("--note")
    register_parser.add_argument("--allow-duplicate-content", action="store_true")
    register_parser.set_defaults(handler=register)

    catalog_parser = subparsers.add_parser("catalog")
    catalog_parser.add_argument("--root", default=str(DEFAULT_ROOT))
    catalog_parser.set_defaults(handler=catalog)

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--root", default=str(DEFAULT_ROOT))
    show_parser.add_argument("--ruleset-id", required=True)
    show_parser.add_argument("--version", required=True)
    show_parser.set_defaults(handler=show)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--root", default=str(DEFAULT_ROOT))
    verify_parser.add_argument("--ruleset-id")
    verify_parser.add_argument("--version")
    verify_parser.set_defaults(handler=verify)

    diff_parser = subparsers.add_parser("diff")
    diff_parser.add_argument("--left", required=True)
    diff_parser.add_argument("--right", required=True)
    diff_parser.set_defaults(handler=diff)
    return value


def main() -> None:
    args = parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
