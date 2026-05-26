# remove-bg

本地 AI 图片/视频抠图工具。在浏览器里的本地 Web GUI 批量处理图片或 10 秒以内短视频，支持 Windows 与 macOS。

## 功能

- 批量图片抠图：支持 `png`、`jpg`、`jpeg`、`webp`，输出透明 PNG。
- 视频抠图：支持 10 秒以内短视频，默认输出 APNG、透明 PNG 序列帧 ZIP、GIF 和精灵图，也可选择 WebP 与 WebM。
- 本地运行：Windows NVIDIA 设备可使用常驻显存的 BiRefNet Massive CUDA 高质量模式；无 CUDA 与 macOS 自动使用项目内 CPU 引擎回退，不上传素材。
- GUI 任务队列：支持拖拽上传、预览、进度状态、输出文件访问。

## 首次准备

安装 Python 3.10+，然后安装依赖：

```bash
python -m pip install -r requirements.txt
```

下载或校验 BiRefNet 模型和推理引擎：

```bash
python scripts/download_assets.py
```

视频功能还需要项目内 FFmpeg：

```text
bin\ffmpeg\win32-x64\ffmpeg.exe
bin\ffmpeg\win32-x64\ffprobe.exe
```

这些大文件默认不提交进普通 Git 历史。分发时建议使用 GitHub Release assets、Git LFS 或下载脚本。

### Windows GPU 加速

配有 NVIDIA 显卡的 Windows 电脑可双击：

```text
setup-gpu-windows.bat
```

该入口安装 PyTorch CUDA 12.8 与 BiRefNet 官方 Hugging Face 推理依赖。CUDA 后端使用固定修订版本的官方 `ZhengPeng7/BiRefNet` 代码结构，并加载 `models/BiRefNet-massive-epoch_240.pth` 作为高质量权重，与 CPU 兼容引擎使用同一份 Massive 模型。第一次执行任务时会缓存所需的模型结构文件到 `models/huggingface-cache/`，之后 GUI 进程会保持同一个 Massive FP32 CUDA 模型常驻并供图片和视频任务复用。缓存和大权重已被忽略，不会进入普通 Git 提交。

未安装 GPU 依赖、CUDA 不可用或模型加载失败时，GUI 会显示 `CPU 回退` 并继续使用现有 Release 引擎。

GPU 环境仍提供 NumPy 加速的实验性边缘精修供命令行手工比较；GUI 默认不运行该步骤。

高质量 CUDA 路径刻意使用 FP32：对黑色面板、深色间隙一类卡通素材，Massive FP16 可能把主体预测成半透明。FP32 在 RTX 5060 上仍能显著快于 CPU，并保持与原 Massive 质量路径一致的 alpha 表现。

GUI 默认直接保留 Massive 模型生成的 alpha，不自动套用通用边缘精修。对浅灰、白色或渐变背景的视频，该精修可能反而产生可见的灰白毛边，因此视频流程固定关闭这层后处理。

## 运行

Windows 双击：

```text
start-gui.bat
```

也可以命令行启动：

```powershell
python src\gui_server.py
```

macOS 启动：

```bash
cd /path/to/remove-bg
python3 -m pip install -r requirements.txt
python3 scripts/download_assets.py
python3 src/gui_server.py
```

默认服务地址：

```text
http://127.0.0.1:8765
```

如果端口被占用，程序会自动尝试后续端口。

## GUI 使用

- `批量抠图`：拖入多张图片，点击开始处理。
- `视频抠图`：拖入一个 10 秒以内视频；日常默认配置为 `12 FPS + 最长边 720`，默认输出 APNG、透明 PNG 序列帧 ZIP、透明 GIF 与精灵图。
- `推理状态`：标题栏显示 `CUDA · 显卡名称 · Massive FP32`、首次模型加载或 `CPU 回退`；CUDA 模式会自动禁用 CPU 并发选项。
- `预览背景`：在透明、白色、黑色、灰色背景间切换检查边缘；仅改变预览，不改变输出文件。
- 默认视频输出：APNG、包含透明 PNG 帧与元数据的 ZIP、透明 GIF、精灵图 PNG/JSON。
- `GIF` 使用透明调色板输出，但 GIF 只支持单级透明，半透明边缘质量仍不如 APNG/PNG 序列帧。
- 移动物体视频默认关闭 alpha 跨帧平滑，避免上一帧轮廓残留为拖影；手动启用时也只平滑前后帧重叠区域，不在新出现或已消失的轮廓位置产生影子。
- 输出与任务数据保存在 `workspace/`，该目录已被 `.gitignore` 忽略。
- 每次重新启动 GUI 时，右侧任务历史会自动清空，同时清除旧上传副本和临时文件；已导出的 `workspace/outputs/` 成果保留。

## 边缘精修

命令行仍保留实验性的边缘精修能力，用于特定素材手工比较。GUI 默认不使用该处理，因为它不是对所有背景都稳定安全。

- `平衡`：实验选项，用于手工比较。
- `细节优先`：适合发丝、半透明材质，降低边缘收缩。
- `干净优先`：适合明显绿幕轮廓线或商品硬边，使用最大清边力度。
- `关闭`：保留模型原始结果，用于比较。

命令行可使用：

```bash
python3 src/batch_remove_bg.py --overwrite --edge-mode auto --quality clean --edge-strength 60 --edge-width 2
python3 src/batch_remove_bg.py --overwrite --edge-mode color --quality clean --edge-strength 100 --edge-width 4
python3 src/batch_remove_bg.py --overwrite --edge-mode off
```

## 目录说明

- `src/`：GUI 后端和抠图核心脚本。
- `web/`：本地 Web GUI 页面、样式和交互脚本。
- `scripts/`：运行资产下载/校验脚本。
- `bin/`：本地推理引擎和 FFmpeg 运行资产。
- `models/`：BiRefNet 模型文件。
- `workspace/`：上传文件、任务记录、输出和临时文件，不进入 Git。

## 常见问题

- 找不到 Pillow：运行 `python -m pip install -r requirements.txt`；边缘精修和 GUI 图片处理均要求 Pillow。
- 缺少模型或引擎：运行 `python scripts/download_assets.py`。
- 视频功能缺少 FFmpeg：确认 `bin\ffmpeg\win32-x64\ffmpeg.exe` 和 `ffprobe.exe` 存在。
- Windows 视频处理慢：先运行 `setup-gpu-windows.bat` 并检查标题栏是否显示 CUDA；CPU 回退模式下可降低 FPS/最长边或调整并发数。
- CUDA 显存不足：降低视频最长边，或暂时卸载/禁用 GPU 推理依赖以使用 CPU 回退。
- CUDA 输出比 CPU Massive 边缘更差：确认标题栏包含 `Massive FP32`；旧版标准权重会误保留卡通角色内侧背景，旧版 Massive FP16 还会使黑色主体出现半透明，当前高质量路径已改为 Massive FP32。
