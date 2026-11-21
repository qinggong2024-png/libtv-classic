# 衣影工坊 · 经典版

面向 Windows 桌面浏览器的服装图片与视频制作工作台，包含图片中心、视频制作、任务管理、素材管理和管理中心。前端使用 React 和 TypeScript，后端使用 FastAPI 和 SQLAlchemy。

## 本机预览

准备 Python 3.11、uv、Node.js，以及视频处理所需的 FFmpeg 和 ffprobe。然后在仓库根目录运行：

```powershell
uv sync --frozen --extra dev --python 3.11
npm.cmd --prefix frontend ci
.\start-classic.cmd
```

浏览器入口为 [http://127.0.0.1:5176/](http://127.0.0.1:5176/)。预览使用独立的本机 SQLite 数据库和模拟生成配置，默认关闭真实供应商调用及付费生成。运行 `.\stop-classic.cmd` 可停止本机预览。

## 项目结构

- `frontend/` 包含桌面 Web 界面和前端测试。
- `backend/` 包含 API、后台执行器、数据库迁移和后端测试。
- `scripts/` 包含启动和验证工具。带 `-lan` 的启动脚本面向已有局域网部署，使用前需配置目标电脑的地址。
- `.env*.example` 提供环境配置示例。
