# ALLWE Classic：Windows 服务器首次部署（候选）

本说明适用于全新的独立部署目录，不用于覆盖正在运行的实例。
现有实例的更新、迁移、热发布、恢复请使用已经验收的发布与维护流程。

## 前置条件

- Windows 主机；Python 3.11、uv、Node.js/npm、FFmpeg 和 ffprobe 可用。
- 安装需要访问 Python 与 npm 依赖源。源码不包含虚拟环境、node_modules 或构建文件。
- 服务器拥有可用的私有 IPv4 地址；默认 Web 5176，API 8002，预留备用 API 8003。
- 仅允许可信内网访问 Web；API 保持本机监听。不在公网直接暴露此 HTTP 登录入口。

## 首次安装

在新的 Git checkout 根目录执行：

```powershell
.\install.cmd
.\start.cmd <本机私有IPv4>
.\status.cmd
```

如果 uv 不在 PATH 中，可传入其实际路径：`install.cmd -Uv C:\path\to\uv.exe`。
安装脚本检查空白部署目录，按锁定版本安装依赖并构建前端。
只有完整构建成功才将首次静态产物移到 frontend/dist。
首次启动使用现有经典版初始化逻辑创建隔离数据库、会话密钥、管理员和初始路由。
管理员初始凭据保存在本机受保护的
`var/classic-lan/runtime/initial-admin.json`，不要上传、打印到日志或复制到其他实例。
启动后从可信内网访问对应的 5176 地址。

```powershell
.\stop.cmd
```

若安装失败，保留错误日志并使用新的干净 checkout 复验。
不要删除或覆盖已有运行数据。安装命令拒绝在已有部署上运行。
首次启动保存本机地址到受保护的 runtime/site-lan.json。之后可直接执行 start.cmd。
如需变更地址，先停机并审查本地站点配置，不得覆盖现有任务数据。

## 配置真实生产能力

初始配置不授权付费视频生成。首次安装不带入任何现有生产账号、供应商凭据、任务或媒体。
实际 Sol、Kling、图片及深度视频供应商需要在新实例管理员配置中完成授权；
不得复制其他实例的加密密钥文件而不验证其保护方式和归属。
CUT 是独立系统，需要可访问的 CUT 服务和独立授权的共享目录。
配置通过本机受保护的 runtime 目录提供，不能提交 Git。
CUT 协议参见 docs 下的统一任务合同和异步接单协议。

只有明确授权并核对供应商计费与共享目录之后，才允许启用真实生成、Runner 和 CUT Worker。
先验证模拟链路、幂等和状态恢复，再进行单独批准的真实小样本验收。

## 更新与备份

首次安装脚本不能作为升级工具；已有部署继续使用维护手册和已验证的发布管理器。
数据库、账号、加密配置与素材应单独受保护备份；在独立环境验证恢复结果。
源码包不代表历史数据备份，也不能自动迁移旧任务。

## 验收边界

本说明对应待验收的可迁移源码候选，不是已批准的生产发布。

2026-11-25 验收记录：基于生产 f009cac / f4499da 和独立 CUT Worker 0013094 的归并版本；锁定依赖安装、前端构建、Web/API 健康检查、数据库与管理员初始化、登录和会话均已在隔离目录通过。npm 依赖修复后重新安装审计为 0 项，115 个前端测试文件共 718 项通过。后台恢复组件仍需在独占标准端口 8002/8003 的新服务器确认；真实供应商调用及 CUT 实际剪辑尚未验收。

安全边界：不要通过公网明文 HTTP 传输账号凭据；先部署在可信内网，按公司要求设置防火墙或 HTTPS 反向代理。生产现有账号、素材、任务、密钥和运行配置不进入 GitHub。

## Public source distribution / private director rules

The curated brand-specific rule data and historical site-specific brand reconciliation utility are intentionally not distributed. Director memory remains off by default; to use approved local rule data, install it separately via `ALLWE_DIRECTOR_MEMORY_FILE` or the ignored `backend/knowledge/ootd_director_memory_v1.json` path. The checked-in `.json.example` contains an empty valid schema, not production creative guidance. Do not upload local rule data or real credentials.

Source candidate includes reviewed increments `e036f30`, `3d8e9eb`, `28eb671`, `7a9e665`, and `3893c55`. The Runner checkpoint changes are included for new installations but have not been activated on the existing production server. Standard-port worker and paid-provider end-to-end acceptance remain pending.
