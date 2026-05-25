# remove-bg 项目协作说明

## 项目目标

本项目是本地运行的 AI 图片/短视频抠图工具，提供 Web GUI，在浏览器中完成批量图片处理和 10 秒以内短视频处理。素材仅在本机处理，不上传远端服务。

## 每次任务的计划对齐规则

- 开始任何开发、修复、测试或发布任务前，先读取本文件、`README.md` 和 `git status`，确认当前计划、已有约束及未提交改动。
- 每次实施代码或文档改动时，必须同步更新本文件的“当前计划与进度”，记录本轮目标、完成状态、验证结果和下一步。
- 工作区中已有的未提交改动视为正在进行的工作，应在其基础上继续，不擅自覆盖或回退。
- 模型、引擎、FFmpeg、上传素材、任务数据和处理输出不提交到普通 Git 历史。

## 架构与运行约束

- `src/gui_server.py` 提供本地 HTTP API、上传处理、任务队列和输出访问。
- `web/` 使用原生 HTML/CSS/JavaScript，当前版本不引入前端构建流程。
- `src/batch_remove_bg.py` 是图片抠图与边缘精修核心；`src/video_remove_bg.py` 负责短视频抽帧、逐帧处理和合成输出。
- BiRefNet 模型与跨平台引擎由 GitHub Release `assets-v1` 分发，通过 `scripts/download_assets.py` 下载和校验。
- 运行时不得依赖其他应用程序或插件安装目录。
- 视频最长限制为 10 秒，默认输出 APNG 与精灵图；视频引擎模式默认 `auto`，服务模式失败时回退单图处理。

## 运行方式

Windows：

```powershell
.\start-gui.bat
```

macOS：

```bash
python3 -m pip install -r requirements.txt
python3 scripts/download_assets.py
python3 src/gui_server.py --port 8765
```

GUI 默认地址为 `http://127.0.0.1:8765`；若端口被占用，服务会尝试后续端口。

## 当前计划与进度

### 当前目标：通用边缘精修

解决人物发丝、肩膀和商品硬边上被模型保留为高 alpha 像素的背景色轮廓，支持绿色、黑色、白色和彩色背景。

当前进行中：

- `src/batch_remove_bg.py` 工作区改动正在实现原图与 alpha 联合精修，并增加 `edge-strength`、`edge-width` 参数。
- `src/gui_server.py` 与 `src/video_remove_bg.py` 工作区改动正在接入统一精修参数。
- `web/` 工作区改动正在增加边缘预设、力度/宽度控制和透明/白/黑/灰预览背景。
- `README.md` 工作区改动正在补充 macOS 启动方式与通用边缘精修说明。
- 本轮已完成：将本文件中文化，并确立每次任务必须更新/对齐计划的规则。
- 本轮已验证：Python 可导入 Pillow `12.2.0`，源码语法检查通过，最新工作区 GUI 已在 macOS 上启动于 `http://127.0.0.1:8765`。
- 提交前验证：Python 与前端脚本语法检查通过，Release 资源校验通过；真实图片经服务失败回退单图模式后成功输出 `RGBA 1086x1448` PNG。

待验证与提交：

- 使用明显绿边人像图检查头发、肩膀、手指轮廓改善。
- 增加白底、黑底和彩色背景图，验证通用精修效果。
- 在 GUI 中人工验收参数传递与轮廓视觉效果，并继续验证视频逐帧精修和输出格式。
- 关注单图回退模式处理速度较慢的问题，后续评估提升服务模式稳定性。
- 如在其他 macOS 环境再次发生 Pillow 安装证书失败，补充可复用的安装修复说明。
- 验证通过后提交并推送本轮边缘精修改动。

## 后续方向

- 增加视频边缘跨帧稳定以降低闪烁。
- 如发丝质量仍不足，再评估 RVM 或 MatAnyone 作为高质量模式。

## 参考项目

- RVM: https://github.com/PeterL1n/RobustVideoMatting
- MODNet: https://github.com/ZHKKKe/MODNet
- video-background-remover-cli: https://github.com/sunwood-ai-labs/video-background-remover-cli
- backgroundremover: https://github.com/nadermx/backgroundremover
