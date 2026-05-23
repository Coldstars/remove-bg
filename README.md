# ai-bg-remover

批量本地 AI 抠图工具，使用 BiRefNet Massive 本地引擎。支持 macOS 和 Windows 开发者运行版。

## 目录

- `src/`：批量处理主程序
- `scripts/download_assets.py`：按平台下载/校验模型和推理引擎
- `input/`：放需要抠图的图片，支持 `png`、`jpg`、`jpeg`、`webp`
- `output/`：输出透明 PNG
- `bin/`：本地推理引擎，大文件，不进入 Git
- `models/`：BiRefNet 模型，大文件，不进入 Git

## 首次准备

安装 Python 3.10+，然后安装依赖：

```bash
python -m pip install -r requirements.txt
```

下载或校验当前平台所需资源：

```bash
python scripts/download_assets.py
```

模型和推理引擎不进入 Git，而是从本项目 GitHub Release 下载。脚本会自动识别当前平台并补齐所需资源：

- macOS Intel：`bin/darwin-x64/BiRefNet-massive-epoch_240`
- macOS Apple Silicon：`bin/darwin-arm64/BiRefNet-massive-epoch_240`
- Windows x64：`bin/win32-x64/BiRefNet-massive-epoch_240.exe`
- 模型：`models/BiRefNet-massive-epoch_240.pth`

只检查已有资源是否完整，不下载：

```bash
python scripts/download_assets.py --check-only
```

## 使用

### macOS

把图片放进 `input/`，然后运行：

```bash
./run.sh
```

也可以双击 `双击开始抠图.command`。如果 `input/` 是空的，双击脚本会自动打开 `input/` 文件夹，放好图片后按回车继续。

### Windows

把图片放进 `input\`，然后双击：

```bat
run.bat
```

也可以在命令行运行：

```bat
run.bat --overwrite
```

## 常用参数

```bash
./run.sh --input input --output output
./run.sh --overwrite
./run.sh --glob "*.png" --glob "*.jpg"
./run.sh --overwrite --edge-mode auto --quality clean
./run.sh --overwrite --edge-mode off
```

Windows 下把 `./run.sh` 换成 `run.bat`。默认不会覆盖原图，也不会覆盖已有输出文件。输出统一为透明 PNG。

## 边缘优化

默认会自动检测透明边缘里的背景色残留，并做通用边缘清理。背景可以是绿色、黑色、白色、彩色页面或复杂色块；脚本会从低透明度边缘估计残留背景色，再修正半透明边缘的 RGB 和 alpha。

- `--edge-mode auto`：默认，自动估计边缘背景色，只在检测到明显残边时处理
- `--edge-mode color`：强制按通用背景色残留处理，适合黑、白、彩页等背景
- `--edge-mode green`：兼容旧参数，强制按绿幕残留处理
- `--edge-mode off`：关闭后处理，保留模型原始输出，适合对比
- `--quality clean`：默认，干净优先，会略微收缩低透明脏边
- `--quality detail`：细节优先，边缘修正更保守

## Git 迭代

这个仓库只管理代码、脚本和说明文档。`bin/`、`models/`、`input/`、`output/` 都被 `.gitignore` 忽略。

在新电脑继续开发：

```bash
git clone <your-repo-url>
cd ai-bg-remover
python -m pip install -r requirements.txt
python scripts/download_assets.py
```

后续要给别人直接使用，代码从 Git 仓库拉取，`bin/` 和 `models/` 从 GitHub Release 自动下载。不要把大模型直接提交进普通 Git 历史。

### 大文件分发建议

当前模型和平台引擎都属于大文件：

- 普通 Git 仓库只放源码、脚本、README、配置文件
- `models/` 和 `bin/` 继续由 `.gitignore` 排除
- 给别人用时，大家从 Git 拉代码，再运行 `python scripts/download_assets.py` 自动补齐运行资源
- Release tag 默认是 `assets-v1`，后续升级资源时可以发布 `assets-v2` 并更新脚本默认值

## 工作方式

脚本会按当前平台选择本地引擎：

- macOS Intel：`bin/darwin-x64/BiRefNet-massive-epoch_240`
- macOS Apple Silicon：`bin/darwin-arm64/BiRefNet-massive-epoch_240`
- Windows x64：`bin/win32-x64/BiRefNet-massive-epoch_240.exe`

模型文件统一放在：

```text
models/BiRefNet-massive-epoch_240.pth
```

程序启动本地服务，把图片转成 PNG base64，调用本机 `/api/v1/predict`，对返回 PNG 做边缘清理，再写到 `output/`。

## 常见问题

- 提示缺少 Pillow：运行 `python -m pip install -r requirements.txt`
- 提示模型或引擎缺失：运行 `python scripts/download_assets.py`
- 提示 Release asset 缺失：说明该平台资源还没有上传到 GitHub Release，上传后重新运行
- macOS 提示可执行文件不可执行：运行 `chmod +x bin/darwin-x64/BiRefNet-massive-epoch_240`
- `input/` 为空：放入图片后重新运行
