# ALLWE 经典版 · Kling 图片绑定与原生分镜 Phase 1

状态：隔离实验分支，**未合并、未部署、未发起真实生成或送 CUT**。

基线：GitHub `main` 远端提交 `ead3796a5ee79426317128d050f002898c2573a9`。
实验分支：`experiment/classic-image-bindings-contract-20261121`。

## 目标

修复在同时提供显式首帧、结束帧、多分镜参考、补充参考和商品图时，图片角色说明可能与 Kling `contents[].id` 错位的问题。保留 Sol 原文，使用同一套素材清单驱动 API payload、提示词别名校验和审计角色。

## 修改范围

1. `backend/src/libtv_agent/adapters/kling_api_ootd.py`
   - `kling_ordered_image_slots()` 按官方 adapter 的传输顺序构造统一清单：显式/隐式首帧 → 结束帧 → 后续分镜 → 补充视觉参考 → 商品参考。
   - `_ootd_role_header()` 用真实素材清单生成编号，避免基于 `len(image_paths)` 推导商品图起始编号。
   - 图片引用语法可兼容 `图一`、`图片1`、`@image1`、`@image_1`；超出已发送素材数量的编号在付费提交前失败。
   - 旧版 simple/motion 原生分镜支持保持不变。cross-scene/dynamic 只在 `ALLWE_KLING_NATIVE_SHOTS_PILOT=1` 时启用格式转换，默认保持原链路，避免未经验证地更改真实请求。
2. `backend/src/libtv_agent/adapters/kling_official_api.py`
   - 真实 `contents` 使用相同的图片顺序清单，不再维护一份独立的排序实现。
3. `backend/src/libtv_agent/adapters/kling_ootd_audit.py`
   - 审计的意图角色源于统一清单；原生分镜是否启用也按相同的 pilot 规则判断。
4. `backend/tests/test_kling_api_ootd.py`
   - 测试显式首帧＋结束帧＋分镜＋视觉参考＋商品图组合。
   - 测试对不存在的图片 ID 采取明确报错。
   - 测试新导演模式默认不变，pilot 开启后才尝试无损原生分镜编译。

## 已知边界

- 图片编号正确不等于 Sol **语义上**已选对相应图片角色。后续需要 typed asset ID / role 指代，不能只依赖数字别名。
- 只尝试无损转换明确的整数时间分镜；自由文本、分数秒、时长不完整等仍遵循既有退回路径。
- `ALLWE_KLING_NATIVE_SHOTS_PILOT` 是新的隔离候选开关，**不能直接在生产环境启用**。
- 缺失背面参考图时禁止编排背面图案的判断属于 **Sol 导演可行性**，本次尚未完成自动验证。
- A+ 相关源码当前仅在历史 Windows 实验工作区有交接记录，不在本分支；未在此阶段复制、合并或部署。

## 验证要求

在可访问完整本地工作区的环境执行：

```powershell
uv run --extra dev pytest backend/tests/test_kling_api_ootd.py backend/tests/test_ootd_observability_isolated.py backend/tests/test_kling_api_adapter.py -q
```

继续执行原有全量后端测试，并对下述 payload 进行离线快照比对：

- 仅首帧＋一张商品图。
- 首帧＋第二张分镜＋正背商品图。
- 显式首帧＋结束帧＋后续分镜＋补充参考＋商品图。
- 未知 `@image_9` 和合法 `@image_1` 混合。
- cross-scene/dynamic pilot 开、关时同一 Sol 输出的请求差异。

这些验证应只构建本地 payload 或模拟 provider，不提交真实视频。真实 Kling A/B 应另有明确样本、预算、唯一任务 ID 和回滚策略，不自动送 CUT。

## 进入下一阶段的条件

- 编译和 API 载荷一致性通过上述测试；现有导演模式默认请求不发生非预期变化。
- 重新访问本地正式版和 A+ 隔离工作树，确认 git 状态与已有修改。
- A+ 仅作为独立的 `商品自然阅读时刻` 小优化推进，不夹带未经真实验证的长期知识库和 Kling 执行限制。
- 商品事实及历史规则采用按任务检索、证据核验、可撤销、后台非阻塞的分层方案。
