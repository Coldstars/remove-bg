#!/usr/bin/env python3
"""Download or verify BiRefNet runtime assets for the current platform."""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import ssl
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

OWNER = "Coldstars"
REPO = "remove-bg"
DEFAULT_RELEASE_TAG = "assets-v1"
MODEL_NAME = "BiRefNet-massive-epoch_240.pth"
RELEASE_PAGE = f"https://github.com/{OWNER}/{REPO}/releases/tag/{DEFAULT_RELEASE_TAG}"
SSL_CONTEXT = ssl.create_default_context()
if sys.platform == "darwin":
    cafile = Path("/etc/ssl/cert.pem")
    if cafile.exists():
        SSL_CONTEXT.load_verify_locations(cafile=str(cafile))


@dataclass(frozen=True)
class Asset:
    name: str
    path: Path
    release_name: str
    md5: str
    executable: bool = False
    alternate_md5s: tuple[str, ...] = ()

    @property
    def accepted_md5s(self) -> tuple[str, ...]:
        return (self.md5, *self.alternate_md5s)


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
        release_name=MODEL_NAME,
        md5="9188db23b38c8c0c4470a700d6bd27e5",
        alternate_md5s=("e730845f56910dd102445853ab227cd3",),
    )
    engines = {
        "darwin-x64": Asset(
            name="BiRefNet-massive-epoch_240",
            path=root / "bin" / "darwin-x64" / "BiRefNet-massive-epoch_240",
            release_name="BiRefNet-massive-epoch_240-darwin-x64",
            md5="1ba4b1cf505e81a344879afda60c1c9f",
            executable=True,
            alternate_md5s=("b21b2bdb4506ae003fa05767f7116bd2",),
        ),
        "darwin-arm64": Asset(
            name="BiRefNet-massive-epoch_240",
            path=root / "bin" / "darwin-arm64" / "BiRefNet-massive-epoch_240",
            release_name="BiRefNet-massive-epoch_240-darwin-arm64",
            md5="68901eb184aca3ab1fbdd9a3361cacdc",
            executable=True,
        ),
        "win32-x64": Asset(
            name="BiRefNet-massive-epoch_240.exe",
            path=root / "bin" / "win32-x64" / "BiRefNet-massive-epoch_240.exe",
            release_name="BiRefNet-massive-epoch_240-win32-x64.exe",
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
    return asset.path.exists() and file_md5(asset.path) in asset.accepted_md5s


def ensure_executable(asset: Asset) -> None:
    if not asset.executable or sys.platform == "win32" or not asset.path.exists():
        return
    mode = asset.path.stat().st_mode
    if mode & stat.S_IXUSR:
        return
    asset.path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def release_asset_url(tag: str, asset: Asset) -> str:
    return f"https://github.com/{OWNER}/{REPO}/releases/download/{tag}/{asset.release_name}"


def download_file(url: str, destination: Path) -> None:
    req = request.Request(url, headers={"User-Agent": f"{REPO}-asset-downloader"})
    try:
        with request.urlopen(req, timeout=60, context=SSL_CONTEXT) as response, destination.open("wb") as file:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
                done += len(chunk)
                if total:
                    percent = done / total * 100
                    print(f"\r  {percent:5.1f}% {done / 1024 / 1024:.1f} MB", end="")
            if total:
                print()
    except error.HTTPError as exc:
        raise SystemExit(f"Download failed {exc.code}: {url}") from exc
    except error.URLError as exc:
        raise SystemExit(f"Download failed: {url}\n{exc}") from exc


def download_asset(asset: Asset, url: str) -> None:
    asset.path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f"{asset.release_name}.",
        suffix=".download",
        dir=str(asset.path.parent),
        delete=False,
    ) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        print(f"download: {asset.release_name}")
        download_file(url, temp_path)
        actual_md5 = file_md5(temp_path)
        if actual_md5 not in asset.accepted_md5s:
            expected = ", ".join(asset.accepted_md5s)
            raise SystemExit(f"MD5 mismatch for {asset.release_name}: expected one of {expected}, got {actual_md5}")
        shutil.move(str(temp_path), str(asset.path))
        ensure_executable(asset)
        print(f"saved: {asset.path}")
    finally:
        temp_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download or verify BiRefNet engine/model assets.")
    parser.add_argument(
        "--platform",
        choices=("auto", "darwin-x64", "darwin-arm64", "win32-x64"),
        default="auto",
        help="Target platform assets to check/download.",
    )
    parser.add_argument("--release-tag", default=DEFAULT_RELEASE_TAG, help="GitHub Release tag to download from.")
    parser.add_argument("--check-only", action="store_true", help="Only verify local files and MD5.")
    parser.add_argument("--force", action="store_true", help="Download even if local files already verify.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = platform_key() if args.platform == "auto" else args.platform
    assets = assets_for(target)
    print(f"target: {target}")
    print(f"release: {OWNER}/{REPO}@{args.release_tag}")

    missing_or_invalid: list[Asset] = []
    for asset in assets:
        valid = is_valid(asset)
        if valid and not args.force:
            ensure_executable(asset)
        print(f"{'ok' if valid else 'missing/invalid'}: {asset.path}")
        if args.force or not valid:
            missing_or_invalid.append(asset)

    if not missing_or_invalid:
        print("all assets verified")
        return 0

    if args.check_only:
        print("some assets are missing or invalid; rerun without --check-only to download them", file=sys.stderr)
        return 1

    failed = False
    for asset in missing_or_invalid:
        try:
            download_asset(asset, release_asset_url(args.release_tag, asset))
        except SystemExit as exc:
            release_page = RELEASE_PAGE if args.release_tag == DEFAULT_RELEASE_TAG else f"https://github.com/{OWNER}/{REPO}/releases/tag/{args.release_tag}"
            print(f"release page: {release_page}", file=sys.stderr)
            print(f"expected asset: {asset.release_name}", file=sys.stderr)
            print(f"expected local path: {asset.path}", file=sys.stderr)
            print(exc, file=sys.stderr)
            failed = True

    if failed:
        return 1

    print("all assets verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
