# ALLWE Classic ↔ CUT：异步接单与排队协议（2026-11-21）

本文件用于跨进程、跨机器集成。新任务使用 **`libtv-cut-lan-multi/2`**；不修改历史 `/1` / Bridge 任务，也不修改生成提示词、服装业务规则或剪辑质量策略。

## 核心原则

**先持久化通知与任务身份，后由后台验证并接单，再等待剪辑空位。**
HTTP `202 {"seen":true,"taskId":"..."}` 仅表示收到并保存通知，**不等于 CUT 已接单**。只有具有有效策略回执、稳定 taskId 和正向 revision 的 NAS `status.json`（`status=accepted`）才构成已接单凭证。

CUT 接单的 HTTP 路由：
`POST /api/integrations/libtv-multisource`。现有 POST 会发送含 `manifest`、`editPolicy`、`taskId`、`handoffKey`、`idempotencyKey`、`manifestRelativePath`、`manifestSha256` 的 JSON。**保持既有请求字段**。请求体大小由 CUT 服务配置负责（部署方反馈现已调整至 256 KiB），本次不改为精简协议。

## 同一任务的固定身份

- `contractVersion=libtv-cut-lan-multi/2`
- `statusSchema=yiying-cut-status/2`
- `taskId=<UUID>`
- `handoffKey=multi-<taskId>`
- `idempotencyKey=libtv-cut-lan-multi/2:<taskId>`
- `manifestSha256=SHA256(manifest.json 原始字节)`
- `sources[]` 包含 1–8 条原片：单条及多条走统一入口

共享目录协议：

```text
inbox/multi-<taskId>/manifest.json
inbox/multi-<taskId>/READY
inbox/multi-<taskId>/sources/*
inbox/multi-<taskId>/references/*
outbox/<taskId>/status.json
outbox/<taskId>/<output>.mp4
receipts/classic/multi-<taskId>.json
```

`READY` 必须最后原子发布；CUT 以落盘 manifest 的 SHA256、素材大小/哈希和策略为准，不得仅信任 HTTP 的字段。

## CUT 端修改要求

1. **HTTP 快速唤醒**：仅鉴权、验证基本格式/幂等身份并持久化待验证接收记录；尽快返回 `202`。不得在 HTTP 请求内同步做全部原片 NAS 读取、视频哈希、商品图解码、质量分析、排程或等待剪辑空位。请求体限额已经提高，**无需再次修改为 8 KiB 或更小**。
2. **后台核验与真实接单**：扫描 `READY` 和 HTTP 唤醒信号都进入同一持久队列/幂等门；后台完成完整文件、来源、账户和策略验证后才写权威 `accepted`。
3. **同 ID 原子去重**：相同 `taskId + idempotencyKey + manifestSha256` 的并发 POST、超时重发和 READY scanner 仅有一个接单记录、一个 batch 和一次计费。相同 taskId、不同清单哈希必须拒绝并保留既有记录。
4. **已接单即可排队**：CUT 剪辑并发空位为零时也写 `accepted`，不能阻塞 HTTP 请求。后台取得剪辑资源后开始工作，发布 `processing`；输出校验完成发布 `completed`；真实失败发布含 `error.code` 的 `failed`。
5. **权威状态**：`outbox/<taskId>/status.json` 采用 `schemaVersion=yiying-cut-status/2`，`revision` 从 1 开始并单调增加；文件同目录临时写、flush、原子替换。不得让旧 revision 覆盖新 revision，相同 revision 也不得改动业务状态或 outputs。
6. **恢复与保护**：CUT 重启、HTTP 丢包、NAS 状态暂时不可读时，仅恢复原 taskId 和原 batch；不要新建剪辑订单或重复计费。剪辑失败不应由“通知重试”触发自动重跑。
7. **成片与反向回执**：仅在 `outbox/<taskId>/` 的输出文件完全落盘、大小、SHA256 和解码验证通过后发布 `completed`。ALLWE 完成入库之后写 `receipts/classic/multi-<taskId>.json`，仅作反向证据，不触发再次剪辑。

有效接单状态（示意；字段按既有契约齐备）：

```json
{
  "schemaVersion": "yiying-cut-status/2",
  "revision": 1,
  "taskId": "<taskId>",
  "handoffKey": "multi-<taskId>",
  "manifestSha256": "<sha256>",
  "batchId": "<stable-batch-id>",
  "status": "accepted",
  "policyReceipt": {
    "state": "accepted",
    "taskId": "<taskId>",
    "handoffKey": "multi-<taskId>",
    "manifestSha256": "<sha256>",
    "policyVersion": "yiying-ootd/2"
  }
}
```

## ALLWE 当前状态语义

| ALLWE 状态 | 依据 |
| --- | --- |
| `QUEUED` | ALLWE 本地已创建唯一送剪任务 |
| `READY` | NAS 包和 READY 已发布，等待 CUT 权威回执 |
| `ACCEPTED` | 验证了 CUT `accepted`，前端显示“CUT 已接单，排队中” |
| `EDITING` | 验证了 CUT `processing` |
| `PENDING_REVIEW` | 验证了 CUT `completed` 且已正确导入成片 |

ALLWE **不因为 POST 202 或超时而切换成 ACCEPTED/FAILED**；无 NAS 状态时仍以同一 taskId 等待核验并限频唤醒。已验证接单后，临时丢失 status 文件不能导致再次投递。

## 双机联调必测

- CUT 剪辑槽全满时，同时新建 2 单：仍快速应答并持久排队，ALLWE 分别见到 `ACCEPTED`。
- 模拟 POST 响应丢失或 90 秒超时：维持相同 taskId、唯一 batch、无重复收费。
- 同一 taskId 的 POST 与 READY scanner 并发：必须严格去重。
- CUT 重启与 NAS status 暂时丢失：revision 不倒退，ALLWE 不会创建第二单。
- 一条原片与 2–8 条原片都走 `/2`，输出导入后进入 `PENDING_REVIEW`。
- 保留旧 `/1`、历史任务及权限、预算、质量策略回归。

**验收证据**：CUT 和 ALLWE 分别提供运行中的 commit/build、任务 ID、manifest SHA256、唯一 batch、状态 revision、计费唯一性和成片完整性。单元模拟通过不等于两台生产机器已经完成联调。
