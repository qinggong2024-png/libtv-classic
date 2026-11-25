# ALLWE Classic 项目维护手册

本仓库是衣影工坊经典版（ALLWE Classic）的公开源码。只维护经典版，不与其他版本或 CUT 独立系统混用。

## 1. 安装、运行与配置

准备 Python 3.11、uv、Node.js/npm、FFmpeg/ffprobe。在仓库根目录执行：

```powershell
uv sync --frozen --extra dev --python 3.11
npm.cmd --prefix frontend ci
.\start-classic.cmd
```

本机预览使用 5176，API 使用 8002；默认不执行真实付费生成。停止预览使用 `stop-classic.cmd`。局域网部署前先配置目标主机地址，按照实际环境准备独立运行配置及前端构建；不可直接复用其他部署的凭据、数据库或运行目录。局域网启动及停止使用对应的 `-lan.cmd` 脚本。

## 2. 源码与运行数据

- backend/src：API、任务服务、导演编排、供应商适配、后台执行器。
- backend/alembic、backend/tests：数据库迁移及测试。
- frontend/src、frontend/public：界面、状态展示和必要静态资源。
- scripts、infra：运行、验证及基础设施工具。
- 保留依赖锁文件、配置模板、接口与协议文档。
- 不提交用户素材、Kling 原片、CUT 成片、数据库、日志、真实环境配置、凭据、构建产物或本地缓存。
- 实际运行数据与 Git 源码分离；清理源码不等于清理生产数据。

## 3. 任务故障与恢复

区分供应商余额、风控、限流、网络异常、下载失败和本地持久化失败。UNKNOWN 状态先对账原任务 ID、幂等键、回执和扣费记录，不能盲目重投。保留已成功原片，不重复生成已成功的片段。

CUT 接收 HTTP 请求不等于正式接单。根据权威状态、revision、成片校验及反向回执判断结果。已恢复任务不应被历史错误覆盖。

数据库迁移前做一致性备份，不以旧备份覆盖更新数据。不得输出真实凭据或绕过付费门禁。

## 4. 测试与发布

```powershell
uv run pytest
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
```

在独立工作树完成修改、测试及完整 diff 审查。禁止覆盖其他会话未提交修改，禁止强推或镜像上传内部历史。源码交付、运行部署与线上验收分别记录。

按变更类型选择发布路径；后端、Runner、数据库和提示词变更不能以纯前端热发布代替。未经授权不得启动真实生成、剪辑、迁移或切换生产服务。

## 5. 专项参考

- [发布入口与故障恢复](docs/CLASSIC_RELEASE_MANAGER_V1.md)
- [前端热发布](docs/CLASSIC_FRONTEND_HOT_RELEASE.md)
- [变更分类与不中断发布](docs/CLASSIC_LIVE_ITERATION_CLASSIFICATION_20261124.md)
- [CUT 统一任务合同](docs/CLASSIC_CUTFLOW_UNIFIED_V2_20261029.md)
- [CUT 侧执行单](docs/CLASSIC_CUTFLOW_UNIFIED_V2_CUT_TASK_20261029.md)
- [CUT 异步接单协议](docs/ALLWE_CUT_ASYNC_ACCEPTANCE_PROTOCOL_20261121.md)
- [当前视觉系统与验收](docs/classic-visual-system-v1.md)

专项协议及发布说明以当前源码实现和实测结果为准。
