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
- Windows NVIDIA 高质量模式使用固定修订版本的官方 Hugging Face `ZhengPeng7/BiRefNet` 模型结构，并加载与 CPU 引擎一致的本地 `BiRefNet-massive-epoch_240.pth` 权重，在 GUI 生命周期中以 FP32 常驻复用。
- GitHub Release `assets-v1` 的 BiRefNet 模型与跨平台 CPU 引擎继续作为无 CUDA环境和 macOS 的兼容回退资源，通过 `scripts/download_assets.py` 下载和校验。
- 运行时不得依赖其他应用程序或插件安装目录。
- 视频最长限制为 10 秒，日常默认参数为 `12 FPS + 最长边 720`，默认输出 APNG、透明 PNG 序列帧 ZIP、透明 GIF 与精灵图；CUDA 不可用时回退现有 CPU 流程。

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

### 当前目标：BiRefNet CUDA 常驻推理与卡通视频提速

以卡通/IP 图片和短视频为主要素材，保留现有通用边缘精修，同时消除视频 CPU 回退流程逐帧重新启动模型导致的主要耗时。

当前进行中：

- 本轮已新增 BiRefNet CUDA 后端，模型在 GUI 进程中懒加载并常驻，由图片和视频任务共享；官方结构修订版本已固定。
- 本轮已将 GUI 任务记录扩展为可报告推理后端、设备、处理帧数及耗时，并提供 `/api/runtime` 状态接口。
- 本轮已将 CUDA 作业按完整任务串行排队，避免多视频任务交替抢占 8GB 显存。
- 本轮已把视频 GUI 默认值统一为 `12 FPS + 720`，CUDA 模式下禁用仅供 CPU 回退使用的并发项。
- 本轮已新增独立 Windows GPU 安装入口；基础依赖仍保持轻量，CPU/macOS 路径继续可用。
- 本轮已为安装 NumPy 的环境增加向量化边缘精修；该能力仅保留给命令行实验比较，GUI 默认不调用。
- 本轮已增加启动会话清理：每次重新打开 GUI 自动清空任务历史、上传副本与临时文件，同时保留已导出的输出成果。
- 本轮已扩展视频输出选项：GUI 默认生成 APNG、透明 PNG 序列帧 ZIP、透明 GIF 与精灵图；不提供会丢失透明通道的 MP4。
- 本轮正在修正卡通视频质量问题：GIF 改用透明调色板合成；视频 alpha 平滑默认关闭，手动启用时仅在相邻帧共同主体区域混合，避免运动轮廓拖影或新轮廓变淡。
- 已撤回依赖单色背景的外部连通清除策略：素材可能是黑色、白色或彩色背景，不能按背景颜色假定删除角色附近的深色区域。
- 已对用户最近视频代表帧对比原始 CUDA BiRefNet 与精修输出：手臂内侧深色在原始模型结果中已存在，下一步需在保留源视频帧的会话中评估通用背景估计或模型级改进，不再按单一背景颜色硬删。
- 已定位今天比昨天抠图变脏的回归来源：此前 CUDA 后端使用标准 `ZhengPeng7/BiRefNet` 权重，而旧 CPU 路径使用 `BiRefNet-massive-epoch_240.pth`；同一视频帧 A/B 显示 Massive 能正确清除卡通角色腋下的黑背景。
- 本轮已将 CUDA 常驻后端改为装入本地 Massive 权重；该权重与官方 CUDA 模型结构 `754` 个状态项严格匹配，避免以速度优化换掉原有高质量模型。
- 已进一步定位 Massive CUDA 质量回归来自 FP16：同一 `720` 视频帧脸部中心 alpha 在 CPU Massive 与 CUDA FP32 均为 `254`，而 CUDA FP16 降为 `224` 并出现主体半透明；高质量 CUDA 路径现已固定为 FP32。
- 已定位浅灰渐变背景视频的灰白轮廓毛边来自通用边缘精修，而非 Massive FP32 原始 alpha：同帧 A/B 中关闭精修明显优于默认平衡及强清边。GUI 默认已改为保留模型原始 alpha，视频流程强制关闭该精修；批量图片界面不再暴露参数设置。
- 现有边缘精修、alpha 平滑、APNG 与精灵图输出均继续保留；本轮不接入 RVM 或其他人像视频模型。

待验证与提交：

- 本轮已验证：RTX 5060 可运行 `torch 2.11.0+cu128`；本地 Massive 权重能够在 CUDA FP32 后端严格加载。同一卡通视频帧 CUDA FP32 约 `0.74s`、CPU Massive 约 `18.6s`，并保持脸部不半透明。
- 本轮已验证：既有卡通视频以默认 `12 FPS + 720` 和 Massive CUDA FP32 完整重跑，生成 APNG、透明 PNG 序列 ZIP、透明 GIF 与精灵图，共 `120` 帧；总耗时约 `67.8s`，逐帧推理约 `55.3s`。抽查六帧脸部中心 alpha 均为 `254`，并目视确认手臂内侧未再出现此前的明显黑背景残留。
- 本轮已验证：最新浅灰渐变背景视频在新默认流程下完整重跑，任务参数实际为 `edge_mode=off`、`edge_strength=0`、`edge_width=0`；与旧默认精修同帧对比后，头盔、耳朵和手臂外轮廓的明显灰白 halo 已消失。
- 待人工检查卡通视频动画播放中的边缘观感和跨帧闪烁，并在需要时进一步调节 alpha 稳定策略。
- 验证没有 CUDA 的机器与 macOS 仍能下载 Release 资产并走 CPU 回退路径。
- 检查模型缓存、运行输出、平台引擎和 FFmpeg 不进入普通 Git 历史。

## 后续方向

- 继续评估卡通视频的 alpha 跨帧稳定与边缘平滑，减少闪烁。
- 由于主要素材不是人像，不规划 RVM 接入；仅在未来素材方向改变时重新评估视频专用模型。

## 参考项目

- RVM: https://github.com/PeterL1n/RobustVideoMatting
- MODNet: https://github.com/ZHKKKe/MODNet
- video-background-remover-cli: https://github.com/sunwood-ai-labs/video-background-remover-cli
- backgroundremover: https://github.com/nadermx/backgroundremover
