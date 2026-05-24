# remove-bg GUI project notes

## Summary

This project is now a local Web GUI tool for image and short-video background removal. Users start it with `start-gui.bat`, which launches a local HTTP server on `127.0.0.1` and opens the browser UI.

## Key changes

- `src/gui_server.py` provides the local API, upload handling, task queue, and output browsing.
- `web/` contains the native HTML/CSS/JS UI. Do not introduce a frontend build step for the current version.
- `src/batch_remove_bg.py` and `src/video_remove_bg.py` remain the core processing modules.
- Runtime files live under `workspace/` and are ignored by Git.
- Keep local FFmpeg binaries under `bin/ffmpeg/win32-x64/`; do not depend on Eagle plugin paths at runtime.
- Treat FFmpeg binaries as large runtime assets. Keep them locally, but do not commit them to normal Git history; distribute later with Release assets, Git LFS, or a download script.
- Limit video processing to videos at or below 10 seconds.
- Default video output formats are APNG and sprite sheet. Also support animated WebP, GIF, and WebM with alpha.
- Video processing uses `--engine-mode auto` by default: try BiRefNet server mode first, then fall back to single-image `run` mode if this engine build exits during server startup. The fallback is reliable but much slower because it reloads the model per frame.
- Speed/stability controls are exposed in the GUI: FPS, max side, workers, alpha smoothing, and output formats.

## CLI examples

```powershell
.\start-gui.bat
python src\gui_server.py --port 8765
```

## Iteration stages

- Stage 1: FFmpeg frame extraction + existing BiRefNet + output synthesis.
- Stage 2: Add flicker reduction such as fixed FPS presets, edge smoothing, and alpha post-processing.
- Stage 3: Evaluate RVM or MatAnyone as a dedicated high-quality video mode.

## References

- RVM: https://github.com/PeterL1n/RobustVideoMatting
- MODNet: https://github.com/ZHKKKe/MODNet
- video-background-remover-cli: https://github.com/sunwood-ai-labs/video-background-remover-cli
- backgroundremover: https://github.com/nadermx/backgroundremover
