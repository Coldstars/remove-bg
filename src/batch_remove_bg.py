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
import statistics
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
    from PIL import Image, ImageFilter
except ImportError:
    Image = None
    ImageFilter = None

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_PORT = 54768
DEFAULT_EDGE_STRENGTH = 60
DEFAULT_EDGE_WIDTH = 2
MODEL_NAME = "BiRefNet-massive-epoch_240.pth"
ENGINE_NAME = "BiRefNet-massive-epoch_240"


@dataclass
class Result:
    input_path: Path
    output_path: Path | None
    status: str
    message: str = ""


class BackgroundRemoverServer:
    def __init__(
        self,
        program: Path,
        model: Path,
        port: int,
        startup_timeout: int,
        monitor_parent: bool = True,
    ) -> None:
        self.program = program
        self.model = model
        self.port = port
        self.startup_timeout = startup_timeout
        self.monitor_parent = monitor_parent
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
        ]
        if self.monitor_parent:
            cmd.extend(["--pid", str(os.getpid())])
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


def require_pillow() -> None:
    if Image is None or ImageFilter is None:
        raise RuntimeError(
            "Pillow is required for image processing and edge refinement. "
            "Run: python -m pip install -r requirements.txt"
        )


def image_to_data_url(path: Path) -> str:
    # Convert every source image to PNG before sending it to the model.
    require_pillow()
    assert Image is not None
    with Image.open(path) as image:
        image = image.convert("RGBA")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
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
    require_pillow()
    assert Image is not None
    with Image.open(path) as image:
        return image.size


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


def color_distance_sq(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return sum((left - right) ** 2 for left, right in zip(a, b))


def clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


def local_background_color(
    original_pixels,
    alpha_pixels,
    x: int,
    y: int,
    width: int,
    height: int,
    radius: int,
) -> tuple[int, int, int] | None:
    samples: list[tuple[int, int, int]] = []
    offsets = sorted(
        (
            (dx * dx + dy * dy, dx, dy)
            for dy in range(-radius, radius + 1)
            for dx in range(-radius, radius + 1)
            if dx or dy
        ),
        key=lambda item: item[0],
    )
    for _, dx, dy in offsets:
        px, py = x + dx, y + dy
        if 0 <= px < width and 0 <= py < height and alpha_pixels[px, py] <= 10:
            red, green, blue, _ = original_pixels[px, py]
            samples.append((red, green, blue))
            if len(samples) >= 16:
                break
    if len(samples) < 3:
        return None
    return tuple(int(statistics.median(channel)) for channel in zip(*samples))


def local_texture_factor(original_pixels, x: int, y: int, width: int, height: int) -> float:
    colors: list[tuple[int, int, int]] = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            px, py = x + dx, y + dy
            if 0 <= px < width and 0 <= py < height:
                colors.append(original_pixels[px, py][:3])
    if len(colors) < 2:
        return 1.0
    ranges = [max(channel) - min(channel) for channel in zip(*colors)]
    variation = max(ranges)
    if variation >= 95:
        return 0.42
    if variation >= 55:
        return 0.68
    return 1.0


def local_foreground_color(
    original_pixels,
    alpha_pixels,
    candidate_pixels,
    background: tuple[int, int, int],
    x: int,
    y: int,
    width: int,
    height: int,
    radius: int,
) -> tuple[int, int, int] | None:
    samples: list[tuple[int, int, int]] = []
    offsets = sorted(
        (
            (dx * dx + dy * dy, dx, dy)
            for dy in range(-radius, radius + 1)
            for dx in range(-radius, radius + 1)
            if dx or dy
        ),
        key=lambda item: item[0],
    )
    for _, dx, dy in offsets:
        px, py = x + dx, y + dy
        if not (0 <= px < width and 0 <= py < height):
            continue
        color = original_pixels[px, py][:3]
        if alpha_pixels[px, py] >= 235 and not candidate_pixels[px, py] and color_distance_sq(color, background) > 68 ** 2:
            samples.append(color)
            if len(samples) >= 12:
                break
    if len(samples) < 3:
        return None
    return tuple(int(statistics.median(channel)) for channel in zip(*samples))


def estimate_background_mix(
    source: tuple[int, int, int],
    foreground: tuple[int, int, int],
    background: tuple[int, int, int],
) -> tuple[float, float]:
    direction = tuple(bg - fg for fg, bg in zip(foreground, background))
    denominator = sum(value * value for value in direction)
    if denominator < 32 ** 2:
        return 0.0, float("inf")
    mixture = sum((channel - fg) * value for channel, fg, value in zip(source, foreground, direction)) / denominator
    mixture = max(0.0, min(1.0, mixture))
    projected = tuple(fg + value * mixture for fg, value in zip(foreground, direction))
    residual = sum((channel - projected_channel) ** 2 for channel, projected_channel in zip(source, projected)) ** 0.5
    return mixture, residual


def refine_edge_spill_numpy(
    original,
    cutout,
    edge_mode: str,
    quality: str,
    edge_strength: int,
    edge_width: int,
) -> bytes | None:
    """Accelerate local edge cleanup with array operations when NumPy is installed."""
    try:
        import numpy as np
    except ImportError:
        return None

    source = np.asarray(original, dtype=np.float32)
    result = np.asarray(cutout, dtype=np.uint8).copy()
    alpha = result[:, :, 3].astype(np.float32)
    outside = alpha <= 10
    band_radius = min(7, edge_width + 3)
    band = np.asarray(
        Image.fromarray((outside * 255).astype("uint8"), "L").filter(ImageFilter.MaxFilter(band_radius * 2 + 1))
    ) > 0
    candidate = band & (alpha > 0)
    if not candidate.any():
        buffer = io.BytesIO()
        Image.fromarray(result, "RGBA").save(buffer, format="PNG")
        return buffer.getvalue()

    def local_average(mask, radius):
        weight = np.asarray(
            Image.fromarray((mask * 255).astype("uint8"), "L").filter(ImageFilter.BoxBlur(radius)),
            dtype=np.float32,
        ) / 255.0
        channels = []
        for channel in range(3):
            weighted = (source[:, :, channel] * mask).astype("uint8")
            channels.append(
                np.asarray(Image.fromarray(weighted, "L").filter(ImageFilter.BoxBlur(radius)), dtype=np.float32)
                / np.maximum(weight, 1e-4)
            )
        return np.stack(channels, axis=2), weight > 0.015

    background, has_background = local_average(outside, edge_width + 5)
    opaque = (alpha >= 235) & ~candidate
    foreground, has_foreground = local_average(opaque, edge_width + 11)
    if edge_mode == "green":
        background[~has_background] = (45, 170, 70)
        has_background[:] = True

    valid = candidate & has_background
    source_rgb = source[:, :, :3]
    working_rgb = result[:, :, :3].astype(np.float32)
    distance = ((source_rgb - background) ** 2).sum(axis=2)
    clean = quality == "clean"
    strength = edge_strength / 100.0
    close_limit = (118 if edge_mode == "color" else 96) ** 2
    spill_limit = (185 if clean else 160) ** 2
    similarity = np.clip(1.0 - distance / spill_limit, 0.0, 1.0)
    alpha_factor = np.maximum(0.12, 1.0 - alpha / 255.0)
    correction = strength * similarity * (0.55 + alpha_factor * 0.60)

    direction = background - foreground
    denominator = (direction**2).sum(axis=2)
    mixture = np.clip(((source_rgb - foreground) * direction).sum(axis=2) / np.maximum(denominator, 1.0), 0.0, 1.0)
    projected = foreground + direction * mixture[:, :, None]
    residual = np.sqrt(((source_rgb - projected) ** 2).sum(axis=2))
    mixed = valid & has_foreground & (denominator >= 32**2) & (residual <= (56 if clean else 42)) & (mixture >= 0.05)
    mix_strength = strength * (0.92 if clean else 0.62)
    target_alpha = 255.0 * (1.0 - mixture)
    alpha[mixed] += (np.minimum(alpha, target_alpha) - alpha)[mixed] * mix_strength
    rgb_strength = np.minimum(0.96, mix_strength * (0.48 + mixture * 0.62))
    working_rgb[mixed] += (foreground - working_rgb)[mixed] * rgb_strength[mixed, None]

    rim = valid & ~mixed & (alpha >= 210) & (distance <= close_limit)
    rim_reduction = strength * similarity * (0.62 if clean else 0.42)
    alpha[rim] *= 1.0 - rim_reduction[rim]
    strong_rim = rim & (distance <= 56**2) & (edge_strength >= 55)
    alpha[strong_rim] = np.minimum(alpha[strong_rim], result[:, :, 3][strong_rim] * (0.16 if clean else 0.38))

    remaining = valid & ~mixed & ~rim & (correction > 0.025) & (alpha > 0)
    alpha_fraction = np.maximum(alpha / 255.0, 0.10 if clean else 0.16)
    unmixed = (source_rgb - background * (1.0 - alpha_fraction[:, :, None])) / alpha_fraction[:, :, None]
    unmixed = np.clip(unmixed, 0.0, 255.0)
    working_rgb[remaining] += (unmixed - working_rgb)[remaining] * correction[remaining, None]
    if clean:
        soften = remaining & (alpha < 205)
        alpha[soften] *= 1.0 - correction[soften] * 0.20

    clear = valid & (alpha <= (82 if clean else 44)) & (similarity >= 0.52)
    alpha[clear] = 0
    result[:, :, :3] = np.clip(working_rgb, 0, 255).astype("uint8")
    result[:, :, 3] = np.clip(alpha, 0, 255).astype("uint8")
    result[result[:, :, 3] == 0, :3] = 0
    buffer = io.BytesIO()
    Image.fromarray(result, "RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


def refine_edge_spill(
    original_path: Path,
    png_bytes: bytes,
    edge_mode: str,
    quality: str,
    edge_strength: int = DEFAULT_EDGE_STRENGTH,
    edge_width: int = DEFAULT_EDGE_WIDTH,
) -> bytes:
    if edge_mode == "off":
        return png_bytes

    require_pillow()
    assert Image is not None and ImageFilter is not None
    edge_strength = max(0, min(100, edge_strength))
    edge_width = max(0, min(4, edge_width))
    if edge_strength == 0 or edge_width == 0:
        return png_bytes

    with Image.open(original_path) as original_image, Image.open(io.BytesIO(png_bytes)) as cutout_image:
        original = original_image.convert("RGBA")
        cutout = cutout_image.convert("RGBA")
    if original.size != cutout.size:
        return png_bytes
    accelerated = refine_edge_spill_numpy(original, cutout, edge_mode, quality, edge_strength, edge_width)
    if accelerated is not None:
        return accelerated

    width, height = cutout.size
    alpha = cutout.getchannel("A")
    alpha_pixels = alpha.load()
    original_pixels = original.load()
    result_pixels = cutout.load()
    outside = alpha.point(lambda value: 255 if value <= 10 else 0)
    band_radius = min(7, edge_width + 3)
    candidates = outside.filter(ImageFilter.MaxFilter(band_radius * 2 + 1))
    candidate_pixels = candidates.load()
    clean = quality == "clean"
    strength = edge_strength / 100.0
    sample_radius = edge_width + 5
    foreground_radius = edge_width + 11
    close_limit = (118 if edge_mode == "color" else 96) ** 2
    spill_limit = (185 if clean else 160) ** 2

    for y in range(height):
        for x in range(width):
            rgba = result_pixels[x, y]
            red, green, blue, current_alpha = rgba
            if current_alpha == 0:
                result_pixels[x, y] = (0, 0, 0, 0)
                continue
            if not candidate_pixels[x, y]:
                continue

            background = local_background_color(
                original_pixels,
                alpha_pixels,
                x,
                y,
                width,
                height,
                sample_radius,
            )
            if background is None:
                if edge_mode != "green":
                    continue
                background = (45, 170, 70)

            source_color = original_pixels[x, y][:3]
            distance = color_distance_sq(source_color, background)
            similarity = max(0.0, 1.0 - distance / spill_limit)

            texture_factor = local_texture_factor(original_pixels, x, y, width, height)
            alpha_factor = max(0.12, 1.0 - current_alpha / 255.0)
            correction = strength * similarity * (0.55 + alpha_factor * 0.60) * texture_factor
            foreground = local_foreground_color(
                original_pixels,
                alpha_pixels,
                candidate_pixels,
                background,
                x,
                y,
                width,
                height,
                foreground_radius,
            )
            mixture = residual = 0.0
            if foreground is not None:
                mixture, residual = estimate_background_mix(source_color, foreground, background)
                if residual <= (56 if clean else 42) and mixture >= 0.05:
                    mix_strength = strength * (0.92 if clean else 0.62)
                    target_alpha = clamp_byte(255 * (1.0 - mixture))
                    if target_alpha < current_alpha:
                        current_alpha = clamp_byte(current_alpha + (target_alpha - current_alpha) * mix_strength)
                    rgb_strength = min(0.96, mix_strength * (0.48 + mixture * 0.62))
                    red, green, blue = (
                        clamp_byte(channel + (target - channel) * rgb_strength)
                        for channel, target in zip((red, green, blue), foreground)
                    )
                    result_pixels[x, y] = (red, green, blue, current_alpha)
                    if current_alpha <= (82 if clean else 44) and mixture >= 0.38:
                        result_pixels[x, y] = (0, 0, 0, 0)
                    continue

            if edge_mode == "auto" and similarity < 0.10 and mixture < 0.08 and current_alpha >= 210:
                continue

            # A nearly solid pixel matching nearby removed background is a retained rim.
            if current_alpha >= 210 and distance <= close_limit:
                rim_texture_factor = 1.0 if distance <= (56 ** 2) else texture_factor
                rim_reduction = strength * similarity * (0.62 if clean else 0.42) * rim_texture_factor
                new_alpha = clamp_byte(current_alpha * (1.0 - rim_reduction))
                if distance <= (56 ** 2) and edge_strength >= 55:
                    new_alpha = min(new_alpha, clamp_byte(current_alpha * (0.16 if clean else 0.38)))
                current_alpha = new_alpha
                result_pixels[x, y] = (red, green, blue, current_alpha)

            if current_alpha <= (82 if clean else 44) and similarity >= 0.52:
                result_pixels[x, y] = (0, 0, 0, 0)
                continue
            if correction <= 0.025 or current_alpha == 0:
                continue

            alpha_fraction = max(current_alpha / 255.0, 0.10 if clean else 0.16)
            corrected: list[int] = []
            for channel, bg_channel in zip(source_color, background):
                foreground = (channel - bg_channel * (1.0 - alpha_fraction)) / alpha_fraction
                corrected.append(clamp_byte(channel + (clamp_byte(foreground) - channel) * correction))
            if clean and current_alpha < 205:
                current_alpha = clamp_byte(current_alpha * (1.0 - correction * 0.20))
            result_pixels[x, y] = (*corrected, current_alpha)

    buffer = io.BytesIO()
    cutout.save(buffer, format="PNG")
    return buffer.getvalue()


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
    edge_strength: int,
    edge_width: int,
) -> Result:
    output_path = output_path_for(input_path, input_dir, output_dir)
    if output_path.exists() and not overwrite:
        return Result(input_path, output_path, "skipped", "output exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        data_url = image_to_data_url(input_path)
        png_bytes = server.predict(data_url)
        png_bytes = refine_edge_spill(input_path, png_bytes, edge_mode, quality, edge_strength, edge_width)
        temp_path.write_bytes(png_bytes)
        validate_output(input_path, temp_path)
        temp_path.replace(output_path)
        return Result(input_path, output_path, "ok")
    except Exception as exc:
        if temp_path.exists():
            with contextlib.suppress(Exception):
                temp_path.unlink()
        return Result(input_path, output_path, "failed", str(exc))


def process_one_with_cli(
    program: Path,
    model: Path,
    input_path: Path,
    input_dir: Path,
    output_dir: Path,
    overwrite: bool,
    edge_mode: str,
    quality: str,
    edge_strength: int,
    edge_width: int,
) -> Result:
    output_path = output_path_for(input_path, input_dir, output_dir)
    if output_path.exists() and not overwrite:
        return Result(input_path, output_path, "skipped", "output exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model_output = output_path.with_name(output_path.stem + ".engine.tmp.png")
    final_output = output_path.with_name(output_path.stem + ".tmp.png")
    try:
        result = subprocess.run(
            [str(program), "run", "-i", str(input_path), "-o", str(model_output), "-m", str(model)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(message or f"BiRefNet run failed with code {result.returncode}")
        png_bytes = refine_edge_spill(
            input_path,
            model_output.read_bytes(),
            edge_mode,
            quality,
            edge_strength,
            edge_width,
        )
        final_output.write_bytes(png_bytes)
        validate_output(input_path, final_output)
        final_output.replace(output_path)
        return Result(input_path, output_path, "ok")
    except Exception as exc:
        return Result(input_path, output_path, "failed", str(exc))
    finally:
        model_output.unlink(missing_ok=True)
        final_output.unlink(missing_ok=True)


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
        choices=("auto", "color", "green", "off"),
        default="auto",
        help="Edge cleanup mode. auto detects background color spill; color forces generic cleanup.",
    )
    parser.add_argument(
        "--quality",
        choices=("clean", "detail"),
        default="clean",
        help="Edge cleanup strength. clean removes more dirty edge; detail is more conservative.",
    )
    parser.add_argument(
        "--edge-strength",
        type=int,
        default=DEFAULT_EDGE_STRENGTH,
        help="Edge refinement intensity from 0 to 100. Defaults to 60.",
    )
    parser.add_argument(
        "--edge-width",
        type=int,
        default=DEFAULT_EDGE_WIDTH,
        help="Boundary refinement width from 0 to 4 pixels. Defaults to 2.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    patterns = args.patterns or ["*.png", "*.jpg", "*.jpeg", "*.webp"]
    if not 0 <= args.edge_strength <= 100:
        print("--edge-strength must be between 0 and 100", file=sys.stderr)
        return 2
    if not 0 <= args.edge_width <= 4:
        print("--edge-width must be between 0 and 4", file=sys.stderr)
        return 2

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
        server: BackgroundRemoverServer | None = BackgroundRemoverServer(
            args.bin.expanduser().resolve(),
            args.model.expanduser().resolve(),
            args.port,
            args.startup_timeout,
            monitor_parent=False,
        )
        try:
            try:
                server.start()
            except Exception:
                server.stop()
                server = None
                print("BiRefNet server mode failed; falling back to single-image run mode.")
            total = len(files)
            for index, input_path in enumerate(files, start=1):
                print(f"[{index}/{total}] {input_path.name}")
                planned_output = output_path_for(input_path, input_dir, output_dir)
                if planned_output.exists() and not args.overwrite:
                    result = Result(input_path, planned_output, "skipped", "output exists")
                elif server is not None:
                    result = process_one(
                        server,
                        input_path,
                        input_dir,
                        output_dir,
                        args.overwrite,
                        args.edge_mode,
                        args.quality,
                        args.edge_strength,
                        args.edge_width,
                    )
                else:
                    result = process_one_with_cli(
                        args.bin.expanduser().resolve(),
                        args.model.expanduser().resolve(),
                        input_path,
                        input_dir,
                        output_dir,
                        args.overwrite,
                        args.edge_mode,
                        args.quality,
                        args.edge_strength,
                        args.edge_width,
                    )
                results.append(result)
                if result.status == "ok":
                    print(f"  ok -> {result.output_path}")
                elif result.status == "skipped":
                    print(f"  skipped ({result.message})")
                else:
                    print(f"  failed: {result.message}", file=sys.stderr)
        finally:
            if server is not None:
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
