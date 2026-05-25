# remove-bg

本地 AI 图片/视频抠图工具。在浏览器里的本地 Web GUI 批量处理图片或 10 秒以内短视频，支持 Windows 与 macOS。

## 功能

- 批量图片抠图：支持 `png`、`jpg`、`jpeg`、`webp`，输出透明 PNG。
- 视频抠图：支持 10 秒以内短视频，默认输出 APNG 和精灵图。
- 本地运行：使用项目内 BiRefNet 引擎、模型和 FFmpeg，不上传素材。
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
- `视频抠图`：拖入一个 10 秒以内视频，选择 FPS、最长边、并发数、alpha 平滑和输出格式。
- `边缘优化`：默认使用“平衡”预设，可切换“细节优先”“干净优先”或关闭，也可调整去边力度和收边宽度。
- `预览背景`：在透明、白色、黑色、灰色背景间切换检查边缘；仅改变预览，不改变输出文件。
- 默认视频输出：APNG + 精灵图 PNG/JSON。
- 输出与任务数据保存在 `workspace/`，该目录已被 `.gitignore` 忽略。

## 边缘精修

精修阶段同时使用原图和模型生成的 alpha 遮罩，不限于绿色背景。程序会在透明边界附近局部估计背景颜色，对绿色、白色、黑色或彩色背景残留进行颜色修正，并处理被模型保留为高透明度轮廓的色边。

- `平衡`：默认推荐，适合大多数人像和商品图。
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
- 视频处理慢：降低 FPS、降低最长边，或把并发数设为 2。
