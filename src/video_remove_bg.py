#!/usr/bin/env python3
"""Short video background removal using frame extraction and local BiRefNet."""

from __future__ import annotations

import argparse
import os
import json
import math
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from PIL import Image
except ImportError:
    Image = None

from batch_remove_bg import (
    BackgroundRemoverServer,
    DEFAULT_EDGE_STRENGTH,
    DEFAULT_EDGE_WIDTH,
    default_engine_path,
    default_model_path,
    image_to_data_url,
    platform_key,
    refine_edge_spill,
    validate_output,
)

SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
DEFAULT_FPS = 12
DEFAULT_MAX_DURATION = 10.0
DEFAULT_MAX_SIDE = 720
DEFAULT_ALPHA_SMOOTH = 0.10
DEFAULT_WEBP_QUALITY = 88
DEFAULT_FORMATS = ("apng", "spritesheet")


@dataclass
class VideoResult:
    input_path: Path
    status: str
    outputs: list[Path]
    message: str = ""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_ffmpeg_dir(root: Path) -> Path:
    return root / "bin" / "ffmpeg" / platform_key()


def default_ffmpeg_path(root: Path) -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    return default_ffmpeg_dir(root) / f"ffmpeg{suffix}"


def default_ffprobe_path(root: Path) -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    return default_ffmpeg_dir(root) / f"ffprobe{suffix}"


def run_command(args: list[str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{label} failed with code {result.returncode}: {stderr}")
    return result


def validate_tools(ffmpeg: Path, ffprobe: Path) -> None:
    missing = [path for path in (ffmpeg, ffprobe) if not path.exists()]
    if missing:
        expected_dir = default_ffmpeg_dir(repo_root())
        names = ", ".join(path.name for path in missing)
        raise FileNotFoundError(
            f"Missing {names}. Put ffmpeg and ffprobe in: {expected_dir}"
        )


def probe_duration(ffprobe: Path, video: Path) -> float:
    result = run_command(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video),
        ],
        "ffprobe",
    )
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError(f"Could not read video duration: {video}")
    return duration


def iter_input_videos(input_dir: Path) -> Iterable[Path]:
    for path in sorted(input_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTS:
            yield path


def safe_stem(path: Path) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in path.stem) or "video"


def output_targets(video: Path, output_dir: Path, output_format: str) -> list[Path]:
    stem = safe_stem(video)
    if output_format == "spritesheet":
        return [
            output_dir / f"{stem}_spritesheet.png",
            output_dir / f"{stem}_spritesheet.json",
        ]
    ext = "apng" if output_format == "apng" else output_format
    return [output_dir / f"{stem}.{ext}"]


def all_output_targets(video: Path, output_dir: Path, output_formats: list[str]) -> list[Path]:
    targets: list[Path] = []
    for output_format in output_formats:
        targets.extend(output_targets(video, output_dir, output_format))
    return targets


def extract_frames(
    ffmpeg: Path,
    video: Path,
    fps: int,
    frames_dir: Path,
    overwrite: bool,
    max_side: int,
) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "frame_%06d.png"
    filters = [f"fps={fps}"]
    if max_side > 0:
        filters.append(
            "scale='if(gt(iw,ih),min(iw,%d),-2)':'if(gt(iw,ih),-2,min(ih,%d))'"
            % (max_side, max_side)
        )
    args = [
        str(ffmpeg),
        "-hide_banner",
        "-y" if overwrite else "-n",
        "-i",
        str(video),
        "-vf",
        ",".join(filters),
        str(pattern),
    ]
    run_command(args, "frame extraction")
    frames = sorted(frames_dir.glob("frame_*.png"))
    if not frames:
        raise RuntimeError("No frames were extracted from the video")
    return frames


def remove_frame_backgrounds(
    server: BackgroundRemoverServer,
    frames: list[Path],
    cutout_dir: Path,
    edge_mode: str,
    quality: str,
    edge_strength: int,
    edge_width: int,
) -> list[Path]:
    cutout_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    total = len(frames)
    for index, frame in enumerate(frames, start=1):
        output = cutout_dir / frame.name
        temp = output.with_name(output.stem + ".tmp.png")
        print(f"  [{index}/{total}] {frame.name}")
        try:
            data_url = image_to_data_url(frame)
            png_bytes = server.predict(data_url)
            png_bytes = refine_edge_spill(frame, png_bytes, edge_mode, quality, edge_strength, edge_width)
            temp.write_bytes(png_bytes)
            validate_output(frame, temp)
            temp.replace(output)
            outputs.append(output)
        except Exception:
            if temp.exists():
                temp.unlink(missing_ok=True)
            raise
    return outputs


def remove_frame_backgrounds_with_cli(
    program: Path,
    model: Path,
    frames: list[Path],
    cutout_dir: Path,
    edge_mode: str,
    quality: str,
    edge_strength: int,
    edge_width: int,
    workers: int,
) -> list[Path]:
    cutout_dir.mkdir(parents=True, exist_ok=True)
    total = len(frames)
    workers = max(1, workers)

    def process_frame(frame: Path) -> Path:
        output = cutout_dir / frame.name
        temp = output.with_name(output.stem + ".tmp.png")
        try:
            run_command(
                [
                    str(program),
                    "run",
                    "-i",
                    str(frame),
                    "-o",
                    str(temp),
                    "-m",
                    str(model),
                ],
                "BiRefNet frame processing",
            )
            png_bytes = refine_edge_spill(frame, temp.read_bytes(), edge_mode, quality, edge_strength, edge_width)
            temp.write_bytes(png_bytes)
            validate_output(frame, temp)
            temp.replace(output)
            return output
        except Exception:
            if temp.exists():
                temp.unlink(missing_ok=True)
            raise

    outputs_by_frame: dict[Path, Path] = {}
    if workers == 1:
        for index, frame in enumerate(frames, start=1):
            print(f"  [{index}/{total}] {frame.name}")
            outputs_by_frame[frame] = process_frame(frame)
    else:
        completed = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_frame = {executor.submit(process_frame, frame): frame for frame in frames}
            for future in as_completed(future_to_frame):
                frame = future_to_frame[future]
                outputs_by_frame[frame] = future.result()
                completed += 1
                print(f"  [{completed}/{total}] {frame.name}")
    outputs = [outputs_by_frame[frame] for frame in frames]
    return outputs


def smooth_alpha_frames(frames: list[Path], strength: float) -> None:
    if strength <= 0:
        return
    if Image is None:
        raise RuntimeError("Pillow is required for alpha smoothing")
    strength = max(0.0, min(0.85, strength))
    previous_alpha = None
    for frame in frames:
        with Image.open(frame) as image:
            image = image.convert("RGBA")
            red, green, blue, alpha = image.split()
            if previous_alpha is not None and previous_alpha.size == alpha.size:
                alpha = Image.blend(alpha, previous_alpha, strength)
                image.putalpha(alpha)
                image.save(frame)
            previous_alpha = alpha.copy()


def synthesize_with_ffmpeg(
    ffmpeg: Path,
    frames_dir: Path,
    fps: int,
    output: Path,
    output_format: str,
    webp_quality: int,
    webp_lossless: bool,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "frame_%06d.png"
    base_args = [str(ffmpeg), "-hide_banner", "-y", "-framerate", str(fps), "-i", str(pattern)]

    if output_format == "webp":
        args = [
            *base_args,
            "-loop",
            "0",
            "-c:v",
            "libwebp_anim",
            "-q:v",
            str(webp_quality),
            str(output),
        ]
        if webp_lossless:
            args.insert(-1, "1")
            args.insert(-1, "-lossless")
    elif output_format == "apng":
        args = [*base_args, "-plays", "0", "-f", "apng", str(output)]
    elif output_format == "gif":
        args = [*base_args, str(output)]
    elif output_format == "webm":
        args = [
            *base_args,
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-auto-alt-ref",
            "0",
            str(output),
        ]
    else:
        raise ValueError(f"Unsupported FFmpeg output format: {output_format}")

    run_command(args, f"{output_format} synthesis")


def synthesize_spritesheet(frames: list[Path], outputs: list[Path], fps: int) -> None:
    if Image is None:
        raise RuntimeError("Pillow is required for spritesheet output")
    sheet_path, metadata_path = outputs
    with Image.open(frames[0]) as first:
        width, height = first.size

    columns = math.ceil(math.sqrt(len(frames)))
    rows = math.ceil(len(frames) / columns)
    sheet = Image.new("RGBA", (columns * width, rows * height), (0, 0, 0, 0))

    for index, frame in enumerate(frames):
        with Image.open(frame) as image:
            image = image.convert("RGBA")
            x = (index % columns) * width
            y = (index // columns) * height
            sheet.paste(image, (x, y))

    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(sheet_path)
    metadata = {
        "fps": fps,
        "frame_count": len(frames),
        "frame_width": width,
        "frame_height": height,
        "columns": columns,
        "rows": rows,
        "image": sheet_path.name,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def process_video(
    server: BackgroundRemoverServer | None,
    ffmpeg: Path,
    ffprobe: Path,
    video: Path,
    args: argparse.Namespace,
) -> VideoResult:
    targets = all_output_targets(video, args.output, args.formats)
    if any(path.exists() for path in targets) and not args.overwrite:
        return VideoResult(video, "skipped", targets, "output exists")

    duration = probe_duration(ffprobe, video)
    if duration > args.max_duration:
        return VideoResult(
            video,
            "failed",
            targets,
            f"duration {duration:.2f}s exceeds limit {args.max_duration:.2f}s",
        )

    run_name = f"{safe_stem(video)}_{int(time.time())}"
    run_dir = args.temp / run_name
    frames_dir = run_dir / "frames"
    cutout_dir = run_dir / "cutout"

    try:
        print(f"Extracting frames at {args.fps} FPS...")
        extract_frames(ffmpeg, video, args.fps, frames_dir, True, args.max_side)
        frames = sorted(frames_dir.glob("frame_*.png"))
        print(f"Removing backgrounds for {len(frames)} frames...")
        if server is None:
            cutout_frames = remove_frame_backgrounds_with_cli(
                args.bin,
                args.model,
                frames,
                cutout_dir,
                args.edge_mode,
                args.quality,
                args.edge_strength,
                args.edge_width,
                args.workers,
            )
        else:
            cutout_frames = remove_frame_backgrounds(
                server,
                frames,
                cutout_dir,
                args.edge_mode,
                args.quality,
                args.edge_strength,
                args.edge_width,
            )
        if args.alpha_smooth > 0:
            print(f"Smoothing alpha between frames ({args.alpha_smooth:.2f})...")
            smooth_alpha_frames(cutout_frames, args.alpha_smooth)
        print(f"Writing {', '.join(args.formats)} output...")
        written_outputs: list[Path] = []
        for output_format in args.formats:
            format_targets = output_targets(video, args.output, output_format)
            if output_format == "spritesheet":
                synthesize_spritesheet(cutout_frames, format_targets, args.fps)
            else:
                synthesize_with_ffmpeg(
                    ffmpeg,
                    cutout_dir,
                    args.fps,
                    format_targets[0],
                    output_format,
                    args.webp_quality,
                    args.webp_lossless,
                )
            written_outputs.extend(format_targets)
        return VideoResult(video, "ok", written_outputs)
    except Exception as exc:
        return VideoResult(video, "failed", targets, str(exc))
    finally:
        if not args.keep_frames:
            shutil.rmtree(run_dir, ignore_errors=True)
        else:
            print(f"Kept intermediate frames: {run_dir}")


def parse_args() -> argparse.Namespace:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Remove backgrounds from short videos.")
    parser.add_argument("videos", nargs="*", type=Path, help="Video files to process")
    parser.add_argument("--input", type=Path, default=root / "input-video", help="Input video folder")
    parser.add_argument("--output", type=Path, default=root / "output-video", help="Output folder")
    parser.add_argument("--temp", type=Path, default=root / "temp", help="Temporary working folder")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help="Frame extraction FPS")
    parser.add_argument("--max-side", type=int, default=DEFAULT_MAX_SIDE, help="Resize extracted frames so the longest side is at most this value; 0 keeps original size")
    parser.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION, help="Maximum video duration in seconds")
    parser.add_argument(
        "--format",
        action="append",
        choices=("webp", "apng", "gif", "webm", "spritesheet"),
        default=None,
        help="Output format. Can be repeated. Defaults to apng and spritesheet.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    parser.add_argument("--keep-frames", action="store_true", help="Keep intermediate extracted/cutout frames")
    parser.add_argument("--workers", type=int, default=min(2, max(1, os.cpu_count() or 1)), help="Parallel workers for single-image run mode")
    parser.add_argument("--alpha-smooth", type=float, default=DEFAULT_ALPHA_SMOOTH, help="Blend alpha masks with the previous frame; use 0 to disable")
    parser.add_argument("--webp-quality", type=int, default=DEFAULT_WEBP_QUALITY, help="Animated WebP quality, 1-100")
    parser.add_argument("--webp-lossless", action="store_true", help="Write lossless animated WebP, slower and larger")
    parser.add_argument("--ffmpeg", type=Path, default=default_ffmpeg_path(root), help="FFmpeg executable path")
    parser.add_argument("--ffprobe", type=Path, default=default_ffprobe_path(root), help="FFprobe executable path")
    parser.add_argument("--port", type=int, default=54768, help="Preferred local BiRefNet server port")
    parser.add_argument("--startup-timeout", type=int, default=120, help="Server startup timeout in seconds")
    parser.add_argument("--bin", type=Path, default=default_engine_path(root), help="BiRefNet engine binary path")
    parser.add_argument("--model", type=Path, default=default_model_path(root), help="BiRefNet model file path")
    parser.add_argument("--engine-mode", choices=("auto", "server", "run"), default="auto", help="BiRefNet execution mode")
    parser.add_argument("--edge-mode", choices=("auto", "color", "green", "off"), default="auto", help="Edge cleanup mode")
    parser.add_argument("--quality", choices=("clean", "detail"), default="clean", help="Edge cleanup strength")
    parser.add_argument("--edge-strength", type=int, default=DEFAULT_EDGE_STRENGTH, help="Edge refinement intensity, 0-100")
    parser.add_argument("--edge-width", type=int, default=DEFAULT_EDGE_WIDTH, help="Boundary refinement width, 0-4 pixels")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.input = args.input.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.temp = args.temp.expanduser().resolve()
    args.ffmpeg = args.ffmpeg.expanduser().resolve()
    args.ffprobe = args.ffprobe.expanduser().resolve()
    args.bin = args.bin.expanduser().resolve()
    args.model = args.model.expanduser().resolve()
    args.formats = args.format or list(DEFAULT_FORMATS)

    if args.fps <= 0:
        print("--fps must be a positive integer", file=sys.stderr)
        return 2
    if args.max_side < 0:
        print("--max-side must be 0 or a positive integer", file=sys.stderr)
        return 2
    if args.workers <= 0:
        print("--workers must be a positive integer", file=sys.stderr)
        return 2
    if not 0 <= args.alpha_smooth <= 0.85:
        print("--alpha-smooth must be between 0 and 0.85", file=sys.stderr)
        return 2
    if not 1 <= args.webp_quality <= 100:
        print("--webp-quality must be between 1 and 100", file=sys.stderr)
        return 2
    if not 0 <= args.edge_strength <= 100:
        print("--edge-strength must be between 0 and 100", file=sys.stderr)
        return 2
    if not 0 <= args.edge_width <= 4:
        print("--edge-width must be between 0 and 4", file=sys.stderr)
        return 2

    try:
        validate_tools(args.ffmpeg, args.ffprobe)
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 2

    videos = [path.expanduser().resolve() for path in args.videos]
    if not videos:
        if not args.input.exists():
            print(f"Input folder does not exist: {args.input}", file=sys.stderr)
            return 2
        videos = [path.resolve() for path in iter_input_videos(args.input)]

    if not videos:
        print(f"No supported videos found in: {args.input}")
        print("Put mp4/mov/m4v/webm/avi/mkv files into input-video/ and run again.")
        return 0

    for video in videos:
        if not video.exists():
            print(f"Video does not exist: {video}", file=sys.stderr)
            return 2
        if video.suffix.lower() not in SUPPORTED_VIDEO_EXTS:
            print(f"Unsupported video extension: {video}", file=sys.stderr)
            return 2

    args.output.mkdir(parents=True, exist_ok=True)
    args.temp.mkdir(parents=True, exist_ok=True)

    results: list[VideoResult] = []
    processable_videos: list[Path] = []
    for video in videos:
        targets = all_output_targets(video, args.output, args.formats)
        if any(path.exists() for path in targets) and not args.overwrite:
            results.append(VideoResult(video, "skipped", targets, "output exists"))
            continue
        try:
            duration = probe_duration(args.ffprobe, video)
        except Exception as exc:
            results.append(VideoResult(video, "failed", targets, str(exc)))
            continue
        if duration > args.max_duration:
            results.append(
                VideoResult(
                    video,
                    "failed",
                    targets,
                    f"duration {duration:.2f}s exceeds limit {args.max_duration:.2f}s",
                )
            )
            continue
        processable_videos.append(video)

    if processable_videos:
        server: BackgroundRemoverServer | None = None
        try:
            if args.engine_mode != "run":
                server = BackgroundRemoverServer(
                    args.bin,
                    args.model,
                    args.port,
                    args.startup_timeout,
                    monitor_parent=False,
                )
                try:
                    server.start()
                except Exception:
                    server.stop()
                    if args.engine_mode == "server":
                        raise
                    print("BiRefNet server mode failed; falling back to single-image run mode.")
                    server = None
            for index, video in enumerate(processable_videos, start=1):
                print(f"\n[{index}/{len(processable_videos)}] {video.name}")
                result = process_video(server, args.ffmpeg, args.ffprobe, video, args)
                results.append(result)
                if result.status == "ok":
                    for output in result.outputs:
                        print(f"  ok -> {output}")
                elif result.status == "skipped":
                    print(f"  skipped ({result.message})")
                else:
                    print(f"  failed: {result.message}", file=sys.stderr)
        finally:
            if server is not None:
                server.stop()

    ok = sum(1 for result in results if result.status == "ok")
    skipped = sum(1 for result in results if result.status == "skipped")
    failed = [result for result in results if result.status == "failed"]

    print("\nSummary")
    print(f"  ok: {ok}")
    print(f"  skipped: {skipped}")
    print(f"  failed: {len(failed)}")
    if failed:
        print("\nFailed videos:", file=sys.stderr)
        for result in failed:
            print(f"  {result.input_path}: {result.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
