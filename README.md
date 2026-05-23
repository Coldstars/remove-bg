# ai-bg-remover

批量本地 AI 抠图工具，使用 BiRefNet Massive 本地引擎。支持 macOS 和 Windows 开发者运行版。

## 目录

- `src/`：批量处理主程序
- `scripts/download_assets.py`：按平台校验模型和推理引擎
- `input/`：放需要抠图的图片，支持 `png`、`jpg`、`jpeg`、`webp`
- `output/`：输出透明 PNG
- `bin/`：本地推理引擎，大文件，不进入 Git
- `models/`：BiRefNet 模型，大文件，不进入 Git

## 首次准备

安装 Python 3.10+，然后安装依赖：

```bash
python -m pip install -r requirements.txt
```

校验当前平台所需资源：

```bash
python scripts/download_assets.py --check-only
```

模型和推理引擎不进入 Git。第一次在新电脑使用时，把资源文件放到对应路径后再校验：

- macOS Intel：`bin/darwin-x64/BiRefNet-massive-epoch_240`
- macOS Apple Silicon：`bin/darwin-arm64/BiRefNet-massive-epoch_240`
- Windows x64：`bin/win32-x64/BiRefNet-massive-epoch_240.exe`
- 模型：`models/BiRefNet-massive-epoch_240.pth`

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

默认会自动检测半透明边缘里的绿色背景残留，并做保守去绿边处理：

- `--edge-mode auto`：默认，只在检测到明显绿色边缘时处理
- `--edge-mode green`：强制按绿幕图片处理
- `--edge-mode off`：关闭后处理，保留模型原始输出，适合对比
- `--quality clean`：默认，干净优先，会略微收缩脏边
- `--quality detail`：细节优先，去绿更保守

## Git 迭代

这个仓库只管理代码、脚本和说明文档。`bin/`、`models/`、`input/`、`output/` 都被 `.gitignore` 忽略。

在新电脑继续开发：

```bash
git clone <your-repo-url>
cd ai-bg-remover
python -m pip install -r requirements.txt
python scripts/download_assets.py --check-only
```

后续要给别人直接使用，建议把代码放 GitHub 仓库，把 `bin/` 和 `models/` 做成单独的 Release 压缩包或放到你自己的对象存储。不要把大模型直接提交进普通 Git 历史。

### 大文件分发建议

当前模型和平台引擎都属于大文件：

- 普通 Git 仓库只放源码、脚本、README、配置文件
- `models/` 和 `bin/` 继续由 `.gitignore` 排除
- 给别人用时，优先发 Release 压缩包，里面可以包含源码、模型、对应平台引擎
- 要多人协作迭代源码时，大家从 Git 拉代码，再从 Release 包或内部网盘补齐运行资源

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
- 提示模型或引擎缺失：把对应资源文件放到 `bin/` 和 `models/` 后运行 `python scripts/download_assets.py --check-only`
- macOS 提示可执行文件不可执行：运行 `chmod +x bin/darwin-x64/BiRefNet-massive-epoch_240`
- `input/` 为空：放入图片后重新运行
