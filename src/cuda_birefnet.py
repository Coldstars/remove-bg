#!/usr/bin/env python3
"""Persistent CUDA FP32 inference backend using local BiRefNet Massive weights."""

from __future__ import annotations

import base64
import io
import threading
from pathlib import Path
from typing import Any

ARCHITECTURE_ID = "ZhengPeng7/BiRefNet"
ARCHITECTURE_REVISION = "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4"
WEIGHTS_NAME = "BiRefNet-massive-epoch_240.pth"
MODEL_LABEL = "BiRefNet Massive"


class CudaBiRefNetBackend:
    """Lazily load one FP32 CUDA model and serialize predictions through it."""

    def __init__(self, root: Path) -> None:
        self.cache_dir = root / "models" / "huggingface-cache"
        self.weights_path = root / "models" / WEIGHTS_NAME
        self.lock = threading.RLock()
        self.model: Any = None
        self.transform: Any = None
        self.torch: Any = None
        self.to_pil: Any = None
        self.state = "checking"
        self.device = "cpu"
        self.gpu_name = ""
        self.fallback_reason = ""
        self._disabled = False
        self._inspect_cuda()

    def _inspect_cuda(self) -> bool:
        with self.lock:
            if self._disabled:
                return False
            try:
                import torch
            except ImportError:
                self.state = "cpu_fallback"
                self.fallback_reason = "未安装 CUDA 推理依赖，请运行 setup-gpu-windows.bat"
                return False
            if not torch.cuda.is_available():
                self.state = "cpu_fallback"
                self.fallback_reason = "PyTorch 未检测到可用的 NVIDIA CUDA 设备"
                return False
            self.torch = torch
            self.device = "cuda"
            self.gpu_name = torch.cuda.get_device_name(0)
            if self.model is None:
                self.state = "cuda_available"
            self.fallback_reason = ""
            return True

    def is_available(self) -> bool:
        return self.model is not None or self._inspect_cuda()

    def ensure_loaded(self) -> None:
        with self.lock:
            if self.model is not None:
                return
            if not self._inspect_cuda():
                raise RuntimeError(self.fallback_reason)
            self.state = "loading"
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                from torchvision import transforms
                from transformers import AutoConfig, AutoModelForImageSegmentation

                assert self.torch is not None
                if not self.weights_path.exists():
                    raise FileNotFoundError(f"缺少高质量模型文件：{self.weights_path.name}，请运行 scripts/download_assets.py")
                self.torch.set_float32_matmul_precision("high")
                config = AutoConfig.from_pretrained(
                    ARCHITECTURE_ID,
                    revision=ARCHITECTURE_REVISION,
                    trust_remote_code=True,
                    cache_dir=str(self.cache_dir),
                )
                model = AutoModelForImageSegmentation.from_config(config, trust_remote_code=True)
                weights = self.torch.load(self.weights_path, map_location="cpu", weights_only=True)
                model.load_state_dict(weights, strict=True)
                model.to("cuda")
                model.eval()
                self.transform = transforms.Compose(
                    [
                        transforms.Resize((1024, 1024)),
                        transforms.ToTensor(),
                        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
                    ]
                )
                self.to_pil = transforms.ToPILImage()
                self.model = model
                self.state = "cuda"
            except Exception as exc:
                self._disabled = True
                self.state = "cpu_fallback"
                self.fallback_reason = f"CUDA 模型加载失败：{exc}"
                self.model = None
                raise RuntimeError(self.fallback_reason) from exc

    def predict(self, image_data_url: str) -> bytes:
        self.ensure_loaded()
        encoded = image_data_url.split(",", 1)[-1]
        source = base64.b64decode(encoded)
        from PIL import Image

        with Image.open(io.BytesIO(source)) as opened:
            original = opened.convert("RGBA")
        rgb = original.convert("RGB")
        assert self.torch is not None and self.model is not None and self.transform is not None
        try:
            with self.lock:
                inputs = self.transform(rgb).unsqueeze(0).to("cuda")
                with self.torch.inference_mode():
                    predictions = self.model(inputs)[-1].sigmoid().cpu()
                mask = self.to_pil(predictions[0].squeeze()).resize(original.size)
        except self.torch.OutOfMemoryError as exc:
            self.torch.cuda.empty_cache()
            raise RuntimeError("CUDA 显存不足，请降低视频最长边或改用 CPU 模式") from exc
        original.putalpha(mask)
        output = io.BytesIO()
        original.save(output, format="PNG")
        return output.getvalue()

    def status(self) -> dict[str, str]:
        with self.lock:
            return {
                "backend": self.state,
                "device": self.device,
                "gpu_name": self.gpu_name,
                "model": MODEL_LABEL,
                "variant": "massive",
                "precision": "fp32",
                "weights": str(self.weights_path),
                "architecture": ARCHITECTURE_ID,
                "model_revision": ARCHITECTURE_REVISION,
                "cache_dir": str(self.cache_dir),
                "fallback_reason": self.fallback_reason,
            }
