#!/usr/bin/env python3
"""Verify BiRefNet runtime assets for the current platform.

The runtime model and platform engines are intentionally kept outside Git.
Put the files in the expected paths, then run this script to verify them.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

MODEL_NAME = "BiRefNet-massive-epoch_240.pth"


@dataclass(frozen=True)
class Asset:
    name: str
    path: Path
    md5: str
    executable: bool = False
    alternate_md5s: tuple[str, ...] = ()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def platform_key() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return "darwin-arm64" if machine in {"arm64", "aarch64"} else "darwin-x64"
    if sys.platform == "win32":
        return "win32-x64"
    raise SystemExit(f"Unsupported platform: {sys.platform} {platform.machine()}")


def assets_for(target: str) -> list[Asset]:
    root = repo_root()
    model = Asset(
        name=MODEL_NAME,
        path=root / "models" / MODEL_NAME,
        md5="9188db23b38c8c0c4470a700d6bd27e5",
        alternate_md5s=("e730845f56910dd102445853ab227cd3",),
    )
    engines = {
        "darwin-x64": Asset(
            name="BiRefNet-massive-epoch_240",
            path=root / "bin" / "darwin-x64" / "BiRefNet-massive-epoch_240",
            md5="1ba4b1cf505e81a344879afda60c1c9f",
            executable=True,
            alternate_md5s=("b21b2bdb4506ae003fa05767f7116bd2",),
        ),
        "darwin-arm64": Asset(
            name="BiRefNet-massive-epoch_240",
            path=root / "bin" / "darwin-arm64" / "BiRefNet-massive-epoch_240",
            md5="68901eb184aca3ab1fbdd9a3361cacdc",
            executable=True,
        ),
        "win32-x64": Asset(
            name="BiRefNet-massive-epoch_240.exe",
            path=root / "bin" / "win32-x64" / "BiRefNet-massive-epoch_240.exe",
            md5="cb29fa18fc256473133e565ee5c8ee68",
            executable=True,
        ),
    }
    return [model, engines[target]]


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_valid(asset: Asset) -> bool:
    if not asset.path.exists():
        return False
    return file_md5(asset.path) in (asset.md5, *asset.alternate_md5s)


def ensure_executable(asset: Asset) -> None:
    if not asset.executable or sys.platform == "win32" or not asset.path.exists():
        return
    mode = asset.path.stat().st_mode
    asset.path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def print_asset_instructions(target: str, assets: list[Asset]) -> None:
    print()
    print("Runtime assets are not stored in Git.")
    print("Place the current platform engine and shared model at these paths:")
    for asset in assets:
        print(f"  - {asset.path}")
    print()
    print("Recommended distribution path:")
    print("  1. Keep source code in GitHub.")
    print("  2. Upload model/engine files to your own release package or private storage.")
    print("  3. After copying them into this folder, run:")
    print(f"     python scripts/download_assets.py --platform {target} --check-only")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify BiRefNet engine/model assets.")
    parser.add_argument(
        "--platform",
        choices=("auto", "darwin-x64", "darwin-arm64", "win32-x64"),
        default="auto",
        help="Target platform assets to check.",
    )
    parser.add_argument("--check-only", action="store_true", help="Only verify files and MD5.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Compatibility flag. Assets are no longer downloaded by this script.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = platform_key() if args.platform == "auto" else args.platform
    assets = assets_for(target)
    print(f"target: {target}")

    missing_or_invalid: list[Asset] = []
    for asset in assets:
        valid = is_valid(asset)
        print(f"{'ok' if valid else 'missing/invalid'}: {asset.path}")
        if not valid:
            missing_or_invalid.append(asset)

    if args.force:
        print("--force is ignored because this script only verifies local assets.")

    for asset in assets:
        if is_valid(asset):
            ensure_executable(asset)

    if missing_or_invalid:
        print_asset_instructions(target, missing_or_invalid)
        return 1

    print("all assets verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
