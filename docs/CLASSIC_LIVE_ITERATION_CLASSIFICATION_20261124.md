# ALLWE Classic 5176 | 不中断迭代：按变更类型发布

仅经典版 5176。切勿触碰其他版本、CUT 独立服务、真实数据库、生成提示词或在途付费任务。

## 开发者一条计划命令

在已同步候选提交的生产 main 上，先确定上次**已批准的发布基线**：

```powershell
.venv\Scripts\python.exe scripts\classic_release.py plan --change-base <APPROVED_BASE_SHA>
```

`plan` 只读当前 Git 提交差异和生产 API／任务安全状态，输出：
- `frontend-hot`：仅常规前端视图／样式／静态资源变更；人工确认当前 API 契约兼容之后可不停产发布；
- `api-canary-review`：API 或前端接口契约变更，需使用现有 8002/8003 受控双向准备和 API drain 检查，不能自动归入纯 UI；
- `worker-handoff-required`：服务层／Runner／CUT 相关代码，必须按独占锁、任务归属、持久检查点和现有闸门交接；
- `maintenance-review`、`prompt-approval-required`、`manual-review`：人工审查，不能热发。

**无权通过随意选择相同基线、改变 UNKNOWN 基线、忽略未知文件来绕过校验。** 已提交源码差异能证明范围，不能单独证明 API 兼容或已经部署。

## 纯前端不中断热发布

```powershell
.venv\Scripts\python.exe scripts\classic_release.py publish --apply --change-base <APPROVED_BASE_SHA> --expected-main-head <APPROVED_40_SHA> --expected-api-revision <LIVE_64_SHA> --expected-old-frontend <LIVE_INDEX_64_SHA>
```

发布代码会在构建前和发布前复查原提交、变更范围、旧首页哈希、实际 API／配置／schema，使用受控静态资源原子发布。已有云端视频／CUT 任务可继续，且不会停止 Runner。未经审批的 API、Worker、模型提示词或数据库变更禁止走此入口。

**已知限制：** 如果 main 有尚未部署的后端源码，`BACKEND_MAIN_NOT_ACTIVE` 等检查仍会阻止直接从 main 打包前端。这是真实来源差异，不应直接跳过。必须先准备与当前运行 API 兼容的隔离前端候选并完成来源验证，或等待后端正常发布。

## 后端 API 双向准备与 Worker 安全交接

最新 main 已有 `classic_api_slot_plan.py`、`classic_blue_api.py`、`classic_api_drain.py`、`classic_worker_checkpoint.py`、`classic_worker_source.py`，这是未来不停站替换的基础。不添加第二套竞争的 8002 管理器。

只读检查：

```powershell
.venv\Scripts\python.exe scripts\classic_api_slot_plan.py --candidate <FROZEN_RELEASE_WORKTREE>
.venv\Scripts\python.exe scripts\classic_worker_handoff.py plan
```

如果在途付费／CUT 状态不安全、旧版进程未排空、Supervisor 未加载新能力、源指纹不符，就**保留旧 API、Runner 和 CUT 工作**，不触发切流或重复付费。双向准备的源码与可用性，不等于生产运行时已经启用。

当前生产有持续的 Kling 命令、历史 UNKNOWN 与线上配置差异。真正放宽 API open-intake 切流或并行 Worker 需要实测证明进程／任务归属、接单幂等、旧请求完成、账单和回执均一致；尚未获证据之前保留现有严格切换闸门。

## 每次只汇报五项

修改范围、测试结果、main Git 提交、5176 运行版本及验收、GitHub 发布状态。代码合入与生产 API 切换分开陈述，不以 HTTP 200 宣称任务成功。
