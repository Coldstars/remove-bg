#!/usr/bin/env python3
"""Batch background removal using a local BiRefNet Massive engine."""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import io
import json
import os
import platform
import signal
import socket
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib import error as urlerror
from urllib import request as urlrequest

try:
    from PIL import Image
except ImportError:
    Image = None

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_PORT = 54768
MODEL_NAME = "BiRefNet-massive-epoch_240.pth"
ENGINE_NAME = "BiRefNet-massive-epoch_240"


@dataclass
class Result:
    input_path: Path
    output_path: Path | None
    status: str
    message: str = ""


class BackgroundRemoverServer:
    def __init__(self, program: Path, model: Path, port: int, startup_timeout: int) -> None:
        self.program = program
        self.model = model
        self.port = port
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen[str] | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._validate_dependencies()
        self.port = find_available_port(self.port)
        cmd = [
            str(self.program),
            "start",
            "--port",
            str(self.port),
            "--model-dir",
            str(self.model),
            "--pid",
            str(os.getpid()),
        ]
        print(f"Starting BiRefNet server on port {self.port}...")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._wait_until_ready()

    def predict(self, image_data_url: str) -> bytes:
        status, body = http_request(
            "POST",
            f"{self.base_url}/api/v1/predict",
            json_body={"image": image_data_url},
            timeout=600,
        )
        if status < 200 or status >= 300:
            raise RuntimeError(f"Predict request failed with HTTP {status}: {body[:500]!r}")
        return body

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            http_request("POST", f"{self.base_url}/api/v1/shutdown", timeout=2)
        if self.process and self.process.poll() is None:
            with contextlib.suppress(Exception):
                self.process.terminate()
                self.process.wait(timeout=5)
        if self.process and self.process.poll() is None:
            with contextlib.suppress(Exception):
                self.process.kill()

    def _validate_dependencies(self) -> None:
        if not self.program.exists():
            raise FileNotFoundError(f"Missing engine binary: {self.program}")
        if sys.platform != "win32" and not os.access(self.program, os.X_OK):
            raise PermissionError(f"Engine binary is not executable: {self.program}")
        if not self.model.exists():
            raise FileNotFoundError(f"Missing model file: {self.model}")

    def _wait_until_ready(self) -> None:
        deadline = time.time() + self.startup_timeout
        last_error = ""
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                stderr = read_pipe_tail(self.process.stderr)
                stdout = read_pipe_tail(self.process.stdout)
                raise RuntimeError(
                    "BiRefNet server exited during startup.\n"
                    f"stdout:\n{stdout}\n"
                    f"stderr:\n{stderr}"
                )
            try:
                status, _ = http_request("GET", f"{self.base_url}/api/v1/ping", timeout=1)
                if status == 200:
                    print("BiRefNet server is ready.")
                    return
                last_error = f"HTTP {status}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.5)
        raise TimeoutError(f"Server startup timed out after {self.startup_timeout}s: {last_error}")


def read_pipe_tail(pipe, limit: int = 4000) -> str:
    if pipe is None:
        return ""
    with contextlib.suppress(Exception):
        data = pipe.read() or ""
        return data[-limit:]
    return ""


def http_request(method: str, url: str, json_body: dict | None = None, timeout: int | float = 30) -> tuple[int, bytes]:
    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except urlerror.HTTPError as exc:
        return exc.code, exc.read()


def find_available_port(start_port: int) -> int:
    for port in range(start_port, start_port + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No available port found from {start_port} to {start_port + 99}")


def platform_key() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "darwin-arm64"
        return "darwin-x64"
    if sys.platform == "win32":
        return "win32-x64"
    raise RuntimeError(f"Unsupported platform: {sys.platform} {platform.machine()}")


def default_engine_path(root: Path) -> Path:
    key = platform_key()
    filename = ENGINE_NAME + (".exe" if key == "win32-x64" else "")
    return root / "bin" / key / filename


def default_model_path(root: Path) -> Path:
    return root / "models" / MODEL_NAME


def image_to_data_url(path: Path) -> str:
    # Convert every source image to PNG before sending it to the model.
    # Pillow keeps this path portable across macOS and Windows. macOS can
    # still fall back to sips when Pillow has not been installed yet.
    if Image is not None:
        with Image.open(path) as image:
            image = image.convert("RGBA")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            data = buffer.getvalue()
    elif sys.platform == "darwin":
        data = image_to_png_with_sips(path)
    else:
        raise RuntimeError("Pillow is required on this platform. Run: python -m pip install -r requirements.txt")
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def image_to_png_with_sips(path: Path) -> bytes:
    if path.suffix.lower() == ".png":
        return path.read_bytes()

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        run_sips(["-s", "format", "png", str(path), "--out", str(tmp_path)])
        return tmp_path.read_bytes()
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()


def run_sips(args: list[str]) -> str:
    result = subprocess.run(["sips", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "sips failed")
    return result.stdout


def image_size(path: Path) -> tuple[int, int]:
    if Image is not None:
        with Image.open(path) as image:
            return image.size
    if sys.platform == "darwin":
        output = run_sips(["-g", "pixelWidth", "-g", "pixelHeight", str(path)])
        width = height = None
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("pixelWidth:"):
                width = int(line.split(":", 1)[1].strip())
            elif line.startswith("pixelHeight:"):
                height = int(line.split(":", 1)[1].strip())
        if width is None or height is None:
            raise ValueError(f"Could not read image size: {path}")
        return width, height
    raise RuntimeError("Pillow is required on this platform. Run: python -m pip install -r requirements.txt")


def png_has_alpha(path: Path) -> bool:
    data = path.read_bytes()
    if len(data) < 26 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    color_type = data[25]
    return color_type in (4, 6)


def paeth_predictor(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= up_distance and left_distance <= upper_left_distance:
        return left
    if up_distance <= upper_left_distance:
        return up
    return upper_left


def decode_png_rgba(png_bytes: bytes) -> tuple[int, int, list[bytearray]]:
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG file")

    pos = 8
    width = height = bit_depth = color_type = None
    compressed = bytearray()

    while pos < len(png_bytes):
        chunk_len = int.from_bytes(png_bytes[pos : pos + 4], "big")
        pos += 4
        chunk_type = png_bytes[pos : pos + 4]
        pos += 4
        chunk_data = png_bytes[pos : pos + chunk_len]
        pos += chunk_len + 4

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])[:4]
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None:
        raise ValueError("PNG is missing IHDR")
    if bit_depth != 8 or color_type != 6:
        raise ValueError(f"Unsupported PNG format: bit_depth={bit_depth}, color_type={color_type}")

    raw = zlib.decompress(bytes(compressed))
    bytes_per_pixel = 4
    stride = width * bytes_per_pixel
    rows: list[bytearray] = []
    prev = bytearray(stride)
    index = 0

    for _ in range(height):
        filter_type = raw[index]
        index += 1
        row = bytearray(raw[index : index + stride])
        index += stride

        for x in range(stride):
            left = row[x - bytes_per_pixel] if x >= bytes_per_pixel else 0
            up = prev[x]
            upper_left = prev[x - bytes_per_pixel] if x >= bytes_per_pixel else 0

            if filter_type == 1:
                row[x] = (row[x] + left) & 255
            elif filter_type == 2:
                row[x] = (row[x] + up) & 255
            elif filter_type == 3:
                row[x] = (row[x] + ((left + up) // 2)) & 255
            elif filter_type == 4:
                row[x] = (row[x] + paeth_predictor(left, up, upper_left)) & 255
            elif filter_type != 0:
                raise ValueError(f"Unsupported PNG filter: {filter_type}")

        rows.append(row)
        prev = row

    return width, height, rows


def encode_png_rgba(width: int, height: int, rows: list[bytearray]) -> bytes:
    def chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
        crc = binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        return (
            len(chunk_data).to_bytes(4, "big")
            + chunk_type
            + chunk_data
            + crc.to_bytes(4, "big")
        )

    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw.extend(row)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), level=6))
        + chunk(b"IEND", b"")
    )


def green_dominance(red: int, green: int, blue: int) -> int:
    return green - max(red, blue)


def should_remove_green_spill(rows: list[bytearray], width: int, edge_mode: str) -> bool:
    if edge_mode == "green":
        return True
    if edge_mode == "off":
        return False

    semi_alpha = 0
    greenish = 0
    dominance_total = 0

    for row in rows:
        for x in range(width):
            idx = x * 4
            red, green, blue, alpha = row[idx : idx + 4]
            if 8 < alpha < 245:
                semi_alpha += 1
                dominance = green_dominance(red, green, blue)
                if dominance > 18 and green > 70:
                    greenish += 1
                    dominance_total += dominance

    if semi_alpha == 0 or greenish == 0:
        return False

    greenish_ratio = greenish / semi_alpha
    avg_dominance = dominance_total / greenish
    return greenish_ratio >= 0.25 and avg_dominance >= 24


def remove_green_spill(png_bytes: bytes, edge_mode: str, quality: str) -> bytes:
    if edge_mode == "off":
        return png_bytes

    try:
        width, height, rows = decode_png_rgba(png_bytes)
    except Exception:
        return png_bytes

    if not should_remove_green_spill(rows, width, edge_mode):
        return png_bytes

    clean = quality == "clean"
    dominance_threshold = 12 if clean else 20
    clear_alpha = 82 if clean else 30
    shrink_alpha = 190 if clean else 85

    for row in rows:
        for x in range(width):
            idx = x * 4
            red, green, blue, alpha = row[idx : idx + 4]
            if alpha == 0:
                row[idx : idx + 3] = b"\x00\x00\x00"
                continue

            dominance = green_dominance(red, green, blue)
            if dominance <= dominance_threshold or green < 55:
                continue

            edge_weight = max(0.0, min(1.0, (255 - alpha) / 255))
            if alpha <= clear_alpha and dominance > dominance_threshold:
                row[idx : idx + 3] = b"\x00\x00\x00"
                row[idx + 3] = 0
                continue

            target_green = max(red, blue) + (2 if clean else 14)
            strength = (0.55 + 0.40 * edge_weight) if clean else (0.18 + 0.42 * edge_weight)
            row[idx + 1] = max(0, min(255, int(green - (green - target_green) * strength)))

            if clean and alpha < shrink_alpha:
                alpha_strength = min(0.48, 0.18 + (dominance / 140) * 0.26)
                row[idx + 3] = max(0, min(255, int(alpha * (1 - alpha_strength))))

    return encode_png_rgba(width, height, rows)


def validate_output(input_path: Path, output_path: Path) -> None:
    if image_size(input_path) != image_size(output_path):
        raise ValueError(f"Output size mismatch: expected {image_size(input_path)}, got {image_size(output_path)}")
    if not png_has_alpha(output_path):
        raise ValueError("Output PNG does not contain an alpha channel")


def iter_input_files(input_dir: Path, patterns: list[str]) -> Iterable[Path]:
    seen: set[Path] = set()
    output_dir_names = {"output", "output_birefnet"}
    for pattern in patterns:
        for path in input_dir.rglob(pattern):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTS:
                continue
            if any(part in output_dir_names for part in path.relative_to(input_dir).parts[:-1]):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def output_path_for(input_path: Path, input_dir: Path, output_dir: Path) -> Path:
    relative = input_path.relative_to(input_dir)
    return (output_dir / relative).with_suffix(".png")


def process_one(
    server: BackgroundRemoverServer,
    input_path: Path,
    input_dir: Path,
    output_dir: Path,
    overwrite: bool,
    edge_mode: str,
    quality: str,
) -> Result:
    output_path = output_path_for(input_path, input_dir, output_dir)
    if output_path.exists() and not overwrite:
        return Result(input_path, output_path, "skipped", "output exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        data_url = image_to_data_url(input_path)
        png_bytes = server.predict(data_url)
        png_bytes = remove_green_spill(png_bytes, edge_mode, quality)
        temp_path.write_bytes(png_bytes)
        validate_output(input_path, temp_path)
        temp_path.replace(output_path)
        return Result(input_path, output_path, "ok")
    except Exception as exc:
        if temp_path.exists():
            with contextlib.suppress(Exception):
                temp_path.unlink()
        return Result(input_path, output_path, "failed", str(exc))


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Batch remove image backgrounds with BiRefNet Massive.")
    parser.add_argument("--input", type=Path, default=root / "input", help="Input folder")
    parser.add_argument("--output", type=Path, default=root / "output", help="Output folder")
    parser.add_argument(
        "--glob",
        action="append",
        dest="patterns",
        default=None,
        help="Input glob pattern. Can be repeated. Defaults to common image types.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Preferred local server port")
    parser.add_argument("--startup-timeout", type=int, default=120, help="Server startup timeout in seconds")
    parser.add_argument("--bin", type=Path, default=default_engine_path(root), help="Engine binary path")
    parser.add_argument("--model", type=Path, default=default_model_path(root), help="Model file path")
    parser.add_argument(
        "--edge-mode",
        choices=("auto", "green", "off"),
        default="auto",
        help="Edge cleanup mode. auto removes green spill only when detected.",
    )
    parser.add_argument(
        "--quality",
        choices=("clean", "detail"),
        default="clean",
        help="Edge cleanup strength. clean removes more green fringe; detail is more conservative.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    patterns = args.patterns or ["*.png", "*.jpg", "*.jpeg", "*.webp"]

    if not input_dir.exists():
        print(f"Input folder does not exist: {input_dir}", file=sys.stderr)
        return 2

    files = sorted(iter_input_files(input_dir, patterns))
    if not files:
        print(f"No supported images found in: {input_dir}")
        print("Put png/jpg/jpeg/webp files into input/ and run again.")
        return 0

    results: list[Result] = []
    pending_files: list[Path] = []
    for input_path in files:
        planned_output = output_path_for(input_path, input_dir, output_dir)
        if planned_output.exists() and not args.overwrite:
            results.append(Result(input_path, planned_output, "skipped", "output exists"))
        else:
            pending_files.append(input_path)

    if not pending_files:
        total = len(files)
        for index, result in enumerate(results, start=1):
            print(f"[{index}/{total}] {result.input_path.name}")
            print(f"  skipped ({result.message})")
    else:
        server = BackgroundRemoverServer(
            args.bin.expanduser().resolve(),
            args.model.expanduser().resolve(),
            args.port,
            args.startup_timeout,
        )
        try:
            server.start()
            total = len(files)
            for index, input_path in enumerate(files, start=1):
                print(f"[{index}/{total}] {input_path.name}")
                planned_output = output_path_for(input_path, input_dir, output_dir)
                if planned_output.exists() and not args.overwrite:
                    result = Result(input_path, planned_output, "skipped", "output exists")
                else:
                    result = process_one(
                        server,
                        input_path,
                        input_dir,
                        output_dir,
                        args.overwrite,
                        args.edge_mode,
                        args.quality,
                    )
                results.append(result)
                if result.status == "ok":
                    print(f"  ok -> {result.output_path}")
                elif result.status == "skipped":
                    print(f"  skipped ({result.message})")
                else:
                    print(f"  failed: {result.message}", file=sys.stderr)
        finally:
            server.stop()

    ok = sum(1 for r in results if r.status == "ok")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = [r for r in results if r.status == "failed"]

    print("\nSummary")
    print(f"  ok: {ok}")
    print(f"  skipped: {skipped}")
    print(f"  failed: {len(failed)}")
    if failed:
        print("\nFailed files:", file=sys.stderr)
        for result in failed:
            print(f"  {result.input_path}: {result.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    raise SystemExit(main())
