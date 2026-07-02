# benchgate TODO

项目级待办（Agent / 维护者）。已完成里程碑见 `docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md`。

---

## 工程 / Git

- [ ] **Commit & merge pipeline 分支** — `feature-sim-20260702` 上 pipeline、watch 编排、blocks 示例、文档对齐、85 tests 尚未入库

---

## Agent 自动化

- [ ] **`watch loop`** — 持续监听 `design/` + `models/blocks.yaml` / `blocks/*`，变更时自动跑 `watch_once`（pipeline → mapping → sim → gate）；CLI `benchgate watch loop`，Agent `watch_loop`；可选 `--interval`、debounce、Git hook 集成
- [ ] **`auto_capture`** — watch 检测到 pending 元件时自动触发 `lab capture`（MINIMUM_SCOPE 旧描述曾提及，未实现）

---

## RFC 代码缺口（文档已写、实现未齐）

- [ ] **`BenchModelProvider`** — `providers/bench.py`：包裹现有 `lab/fit` + `apply_measured_model`，与 `LtspiceModelProvider` 同形（RFC §2、§4）
- [ ] **`mapping/engine.build_model()`** — 统一 `provider.build()` → `register_model()` 收口；`apply_measured_model` 改为 BenchModelProvider 薄封装（RFC §2.3、§4）
- [ ] **`--from-meas`** — CLI `model build --from-meas block.log`：解析 LTspice/ngspice `.MEAS` 日志 → `provenance.metrics`（RFC §10.5，M7 延后）
- [ ] **`sim-ltspice` optional extra** — `pyproject.toml` 增加 `sim-ltspice = ["spicelib>=1.6"]`，与 `lab` / `agent` / `dev` 并列（RFC §5.2）
- [ ] **`operating_point` 自动推断** — 从 `sim_profiles.yaml` 或 sim 结果推断工作点，供 gate `valid_range` 校验（RFC §3.2，现仅 CLI / `blocks.yaml` 显式传入）
- [ ] **LTspice 方言归一化补全** — `.step`、加密 LT/ADI 模型明确报错、未覆盖原语显式 fail（RFC §5.3）
- [ ] **Vendor / Datasheet Provider** — `ModelSource.VENDOR` / `DATASHEET` 的 `ModelProvider` 实现（RFC §2.1 枚举，无实现）

---

## 测试

- [ ] **`watch_once` 端到端集成测** — 改 `blocks.yaml` / `blocks/*` → 全流水线结果断言（现仅有 `detect_changes` / `pipeline_files`）
- [ ] **真实 KiCad 工程 + pipeline** — 绑仓库内 `design/myboard`（或等价 fixture）跑 pipeline → manifest → gate
- [ ] **`.asc` → netlist** — `resolve_spice_source` 在有 LTspice/Wine 环境下的可选集成测（CI 可 skip）

---

## 文档 / 元数据

- [ ] **RFC §4 实现对照表** — 更新为实际代码（`providers/bench.py`、`build_model()` 等待办状态）
- [ ] **`pyproject.toml` description** — 反映自顶向下 / 自底向上 / blocks.yaml，不单写「台架→KiCad」

---

## 示例 / 落地（可选）

- [ ] **设计目录 blocks 模板** — 在 `design/myboard/models/` 放可跑的 `blocks.yaml` + 示例网表（现仅 `docs/examples/`）
- [ ] **MCP server 封装** — 将 `agent/dispatch` + `TOOLS` 暴露为独立 MCP 进程（现仅有内部 dispatch）
