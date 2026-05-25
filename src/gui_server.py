#!/usr/bin/env python3
"""Local Web GUI for image and short-video background removal."""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from batch_remove_bg import (
    BackgroundRemoverServer,
    DEFAULT_EDGE_STRENGTH,
    DEFAULT_EDGE_WIDTH,
    default_engine_path,
    default_model_path,
    image_to_data_url,
    refine_edge_spill,
    validate_output,
)
from video_remove_bg import (
    DEFAULT_ALPHA_SMOOTH,
    DEFAULT_FPS,
    DEFAULT_FORMATS,
    DEFAULT_MAX_DURATION,
    DEFAULT_MAX_SIDE,
    DEFAULT_WEBP_QUALITY,
    all_output_targets,
    default_ffmpeg_path,
    default_ffprobe_path,
    probe_duration,
    process_video,
    run_command,
    validate_tools,
)

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
WORKSPACE = ROOT / "workspace"
UPLOADS = WORKSPACE / "uploads"
OUTPUTS = WORKSPACE / "outputs"
TEMP = WORKSPACE / "temp"
JOBS_DIR = WORKSPACE / "jobs"
JOBS_FILE = JOBS_DIR / "jobs.json"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


@dataclass
class Job:
    id: str
    type: str
    status: str
    progress: int
    inputs: list[dict[str, str]]
    outputs: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    message: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class UploadFile:
    field: str
    filename: str
    data: bytes


class JobStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.load()

    def load(self) -> None:
        if not JOBS_FILE.exists():
            return
        try:
            data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
            self.jobs = {item["id"]: Job(**item) for item in data}
        except Exception:
            self.jobs = {}

    def save(self) -> None:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        data = [asdict(job) for job in sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)]
        JOBS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, job: Job) -> None:
        with self.lock:
            self.jobs[job.id] = job
            self.save()

    def update(self, job_id: str, **changes: Any) -> None:
        with self.lock:
            job = self.jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
            self.save()

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def list(self) -> list[Job]:
        with self.lock:
            return sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)


STORE = JobStore()


def ensure_dirs() -> None:
    for path in (UPLOADS, OUTPUTS, TEMP, JOBS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def safe_filename(name: str) -> str:
    base = Path(name).name
    safe = "".join(char if char.isalnum() or char in "._- " else "_" for char in base).strip()
    return safe or f"file-{uuid.uuid4().hex}"


def parse_content_disposition(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        result[key.strip().lower()] = raw.strip().strip('"')
    return result


def parse_multipart(headers: Any, body: bytes) -> tuple[dict[str, list[str]], list[UploadFile]]:
    content_type = headers.get("content-type", "")
    if "multipart/form-data" not in content_type or "boundary=" not in content_type:
        raise ValueError("Expected multipart/form-data")
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
    marker = b"--" + boundary.encode("utf-8")
    fields: dict[str, list[str]] = {}
    files: list[UploadFile] = []
    for chunk in body.split(marker):
        chunk = chunk.strip()
        if not chunk or chunk == b"--":
            continue
        if chunk.endswith(b"--"):
            chunk = chunk[:-2].strip()
        header_bytes, sep, payload = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        header_lines = header_bytes.decode("utf-8", errors="replace").split("\r\n")
        part_headers: dict[str, str] = {}
        for line in header_lines:
            if ":" in line:
                key, value = line.split(":", 1)
                part_headers[key.strip().lower()] = value.strip()
        disposition = parse_content_disposition(part_headers.get("content-disposition", ""))
        name = disposition.get("name", "")
        filename = disposition.get("filename", "")
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if filename:
            files.append(UploadFile(name, filename, payload))
        elif name:
            fields.setdefault(name, []).append(payload.decode("utf-8", errors="replace"))
    return fields, files


def file_url(job_id: str, filename: str) -> str:
    return f"/api/files/{job_id}/{filename}"


def output_items(job_id: str, output_dir: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            item = {"name": path.name, "url": file_url(job_id, path.name)}
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                with contextlib.suppress(Exception):
                    from PIL import Image

                    with Image.open(path) as image:
                        item["size"] = f"{image.width}x{image.height}"
            items.append(item)
    return items


def input_items(job_id: str, upload_dir: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for path in sorted(upload_dir.iterdir()):
        if path.is_file():
            items.append({"name": path.name, "url": file_url(job_id, f"input/{path.name}")})
    return items


def process_image_with_cli(
    program: Path,
    model: Path,
    input_path: Path,
    output_path: Path,
    edge_mode: str,
    quality: str,
    edge_strength: int,
    edge_width: int,
) -> None:
    temp = output_path.with_name(output_path.stem + ".tmp.png")
    run_command([str(program), "run", "-i", str(input_path), "-o", str(temp), "-m", str(model)], "BiRefNet image processing")
    png_bytes = refine_edge_spill(input_path, temp.read_bytes(), edge_mode, quality, edge_strength, edge_width)
    temp.write_bytes(png_bytes)
    validate_output(input_path, temp)
    temp.replace(output_path)


def process_image_with_server(
    server: BackgroundRemoverServer,
    input_path: Path,
    output_path: Path,
    edge_mode: str,
    quality: str,
    edge_strength: int,
    edge_width: int,
) -> None:
    temp = output_path.with_name(output_path.stem + ".tmp.png")
    data_url = image_to_data_url(input_path)
    png_bytes = server.predict(data_url)
    png_bytes = refine_edge_spill(input_path, png_bytes, edge_mode, quality, edge_strength, edge_width)
    temp.write_bytes(png_bytes)
    validate_output(input_path, temp)
    temp.replace(output_path)


def run_image_job(job_id: str, options: dict[str, Any]) -> None:
    job = STORE.get(job_id)
    if job is None:
        return
    upload_dir = UPLOADS / job_id
    output_dir = OUTPUTS / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    files = [upload_dir / item["name"] for item in job.inputs]
    edge_mode = options.get("edge_mode", "auto")
    quality = options.get("quality", "clean")
    edge_strength = int(options.get("edge_strength", DEFAULT_EDGE_STRENGTH))
    edge_width = int(options.get("edge_width", DEFAULT_EDGE_WIDTH))
    engine = default_engine_path(ROOT)
    model = default_model_path(ROOT)
    server: BackgroundRemoverServer | None = None
    STORE.update(job_id, status="running", progress=2, message="启动抠图引擎")
    try:
        try:
            server = BackgroundRemoverServer(engine, model, 54768, 120, monitor_parent=False)
            server.start()
        except Exception:
            if server:
                server.stop()
            server = None
            STORE.update(job_id, message="server 模式不可用，切换单图模式")

        total = max(len(files), 1)
        for index, input_path in enumerate(files, start=1):
            STORE.update(job_id, progress=max(5, int((index - 1) / total * 90)), message=f"处理中：{input_path.name}")
            output_path = (output_dir / input_path.name).with_suffix(".png")
            if server:
                process_image_with_server(server, input_path, output_path, edge_mode, quality, edge_strength, edge_width)
            else:
                process_image_with_cli(engine, model, input_path, output_path, edge_mode, quality, edge_strength, edge_width)
        STORE.update(job_id, status="done", progress=100, outputs=output_items(job_id, output_dir), message="完成")
    except Exception as exc:
        STORE.update(job_id, status="failed", progress=100, error=str(exc), outputs=output_items(job_id, output_dir))
    finally:
        if server:
            server.stop()


def run_video_job(job_id: str, options: dict[str, Any]) -> None:
    job = STORE.get(job_id)
    if job is None:
        return
    upload_dir = UPLOADS / job_id
    output_dir = OUTPUTS / job_id
    temp_dir = TEMP / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = default_ffmpeg_path(ROOT)
    ffprobe = default_ffprobe_path(ROOT)
    engine = default_engine_path(ROOT)
    model = default_model_path(ROOT)
    video = upload_dir / job.inputs[0]["name"]
    formats = options.get("formats") or list(DEFAULT_FORMATS)
    args = argparse.Namespace(
        output=output_dir,
        temp=temp_dir,
        formats=formats,
        overwrite=True,
        fps=int(options.get("fps", DEFAULT_FPS)),
        max_side=int(options.get("max_side", DEFAULT_MAX_SIDE)),
        max_duration=float(options.get("max_duration", DEFAULT_MAX_DURATION)),
        edge_mode=options.get("edge_mode", "auto"),
        quality=options.get("quality", "clean"),
        edge_strength=int(options.get("edge_strength", DEFAULT_EDGE_STRENGTH)),
        edge_width=int(options.get("edge_width", DEFAULT_EDGE_WIDTH)),
        workers=max(1, int(options.get("workers", 2))),
        alpha_smooth=float(options.get("alpha_smooth", DEFAULT_ALPHA_SMOOTH)),
        webp_quality=int(options.get("webp_quality", DEFAULT_WEBP_QUALITY)),
        webp_lossless=bool(options.get("webp_lossless", False)),
        keep_frames=False,
        bin=engine,
        model=model,
    )
    server: BackgroundRemoverServer | None = None
    try:
        STORE.update(job_id, status="running", progress=5, message="检查视频")
        validate_tools(ffmpeg, ffprobe)
        duration = probe_duration(ffprobe, video)
        if duration > args.max_duration:
            raise ValueError(f"视频时长 {duration:.2f}s 超过限制 {args.max_duration:.2f}s")
        STORE.update(job_id, progress=12, message="启动抠图引擎")
        if options.get("engine_mode", "auto") != "run":
            try:
                server = BackgroundRemoverServer(engine, model, 54768, 120, monitor_parent=False)
                server.start()
            except Exception:
                if server:
                    server.stop()
                if options.get("engine_mode") == "server":
                    raise
                server = None
                STORE.update(job_id, message="server 模式不可用，切换单图模式")
        STORE.update(job_id, progress=20, message="抽帧与抠图中")
        result = process_video(server, ffmpeg, ffprobe, video, args)
        if result.status != "ok":
            raise RuntimeError(result.message or "视频处理失败")
        STORE.update(job_id, status="done", progress=100, outputs=output_items(job_id, output_dir), message="完成")
    except Exception as exc:
        STORE.update(job_id, status="failed", progress=100, error=str(exc), outputs=output_items(job_id, output_dir))
    finally:
        if server:
            server.stop()


def start_worker(job_id: str, job_type: str, options: dict[str, Any]) -> None:
    target = run_image_job if job_type == "image" else run_video_job
    thread = threading.Thread(target=target, args=(job_id, options), daemon=True)
    thread.start()


class GuiHandler(SimpleHTTPRequestHandler):
    server_version = "RemoveBgGUI/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, message: str, status: int = 400) -> None:
        self.send_json({"error": message}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            return self.serve_static(WEB_DIR / "index.html")
        if path == "/api/jobs":
            return self.send_json([asdict(job) for job in STORE.list()])
        if path.startswith("/api/jobs/"):
            job = STORE.get(path.rsplit("/", 1)[-1])
            if not job:
                return self.send_error_json("Job not found", HTTPStatus.NOT_FOUND)
            return self.send_json(asdict(job))
        if path.startswith("/api/files/"):
            return self.serve_job_file(path)
        return self.serve_static(WEB_DIR / path.lstrip("/"))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in {"/api/jobs/image", "/api/jobs/video"}:
            return self.create_job("image" if path.endswith("/image") else "video")
        if path.startswith("/api/open-output/"):
            job_id = path.rsplit("/", 1)[-1]
            output_dir = OUTPUTS / job_id
            if not output_dir.exists():
                return self.send_error_json("Output folder not found", HTTPStatus.NOT_FOUND)
            if sys.platform == "win32":
                os.startfile(output_dir)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(output_dir)])
            else:
                subprocess.Popen(["xdg-open", str(output_dir)])
            return self.send_json({"ok": True})
        return self.send_error_json("Not found", HTTPStatus.NOT_FOUND)

    def serve_static(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            return self.send_error_json("Not found", HTTPStatus.NOT_FOUND)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_job_file(self, request_path: str) -> None:
        rest = request_path[len("/api/files/") :]
        parts = rest.split("/", 1)
        if len(parts) != 2:
            return self.send_error_json("Invalid file path", HTTPStatus.BAD_REQUEST)
        job_id, name = parts
        if name.startswith("input/"):
            base = UPLOADS / job_id
            file_path = base / safe_filename(name[6:])
        else:
            base = OUTPUTS / job_id
            file_path = base / safe_filename(name)
        try:
            resolved = file_path.resolve()
            if base.resolve() not in resolved.parents:
                raise ValueError
        except Exception:
            return self.send_error_json("Invalid file path", HTTPStatus.BAD_REQUEST)
        return self.serve_static(file_path)

    def create_job(self, job_type: str) -> None:
        try:
            length = int(self.headers.get("content-length", "0"))
            fields, files = parse_multipart(self.headers, self.rfile.read(length))
        except Exception as exc:
            return self.send_error_json(str(exc))
        if not files:
            return self.send_error_json("No files uploaded")
        if job_type == "video" and len(files) != 1:
            return self.send_error_json("Video jobs accept one video at a time")

        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        upload_dir = UPLOADS / job_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved: list[dict[str, str]] = []
        allowed = IMAGE_EXTS if job_type == "image" else VIDEO_EXTS
        for item in files:
            filename = safe_filename(item.filename)
            if Path(filename).suffix.lower() not in allowed:
                return self.send_error_json(f"Unsupported file: {filename}")
            target = upload_dir / filename
            with target.open("wb") as output:
                output.write(item.data)
            saved.append({"name": filename, "url": file_url(job_id, f"input/{filename}")})

        options = self.form_options(fields, job_type)
        job = Job(job_id, job_type, "queued", 0, saved, message="排队中", options=options)
        STORE.add(job)
        start_worker(job_id, job_type, options)
        self.send_json(asdict(job), 201)

    def form_options(self, fields: dict[str, list[str]], job_type: str) -> dict[str, Any]:
        def value(name: str, default: str) -> str:
            values = fields.get(name) or []
            return values[0] if values else default

        def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                parsed = int(value(name, str(default)))
            except ValueError:
                parsed = default
            return max(minimum, min(maximum, parsed))

        options: dict[str, Any] = {
            "edge_mode": value("edge_mode", "auto"),
            "quality": value("quality", "clean"),
            "edge_strength": bounded_int("edge_strength", DEFAULT_EDGE_STRENGTH, 0, 100),
            "edge_width": bounded_int("edge_width", DEFAULT_EDGE_WIDTH, 0, 4),
        }
        if job_type == "video":
            selected = fields.get("formats") or []
            options.update(
                {
                    "fps": value("fps", str(DEFAULT_FPS)),
                    "max_side": value("max_side", str(DEFAULT_MAX_SIDE)),
                    "workers": value("workers", "2"),
                    "alpha_smooth": value("alpha_smooth", str(DEFAULT_ALPHA_SMOOTH)),
                    "engine_mode": value("engine_mode", "auto"),
                    "formats": selected or list(DEFAULT_FORMATS),
                }
            )
        return options


def find_port(start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No available port from {start}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start remove-bg local Web GUI.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    ensure_dirs()
    port = find_port(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", port), GuiHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"remove-bg GUI: {url}")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
