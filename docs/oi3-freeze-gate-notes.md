# OI-3 冻结/泄漏门禁说明（codex 开发交接）

更新时间：2026-06-21（本地会话）

## 本轮完成内容

- 运行目录已按分层布局使用：
  - 发现阶段产物：`data/runs/<run_id>/discovery/`
  - 未来验证阶段产物：`data/runs/<run_id>/validation/`
- `POST /api/discovery/freeze` 实现行为：
  - 检查发现阶段 8 类必需文件是否齐全
  - 对必需文件计算 `sha256:` 哈希
  - 回写 `discovery_frozen=true`
  - 回写 `discovery_artifact_hashes` 到 `run_manifest.json`
  - 失败时返回 409（缺失/不一致时）
- `POST /api/validation/run` 实现预检行为：
  - 先检查 run 是否存在
  - 检查发现阶段是否已冻结
  - 校验 manifest 里的发现工件哈希是否与当前一致
  - 不满足时返回 409
  - 当前返回 `validation_status="blocked_not_implemented"`，表示网关已通过但主验证流程尚未接入
- `GET /api/runs/{run_id}/status` 改为返回嵌套工件清单：
  - 例如 `discovery/raw_documents.parquet`、`discovery/graph.json`
- `POST /api/data/import` 的产物路径更新：
  - `artifacts=["discovery/raw_documents.parquet"]`

## 已提交

- `54f535d`：Add discovery freeze hash gate and validation preflight tests
- `3d93a50`：docs: mark OI-3 as in-progress with current backend state
- `eed707e`：Report nested run artifacts in status endpoint

## 目前真实状态

- OI-3 在“冻结 + 哈希预检”层面已落地；
- 但 **完整验证引擎接入仍是后续工作**，当前 `/api/validation/run` 仅用于门禁预检；
- 门禁相关单测已增加：freeze 阻塞、成功落盘、validation 前置校验、工件篡改后阻断。

## 下一步（按优先级）

- 1) 将真实 `validation` 计算接入 `validation` 路径（生成市场价格/基准结果等）
- 2) 增加“未来数据读取只能在 freeze 后发生”的执行端约束与日志审计
- 3) 用真实 demo 数据补一次完整的 leakage gate 运行验证（issue #2/#4 关注项）
