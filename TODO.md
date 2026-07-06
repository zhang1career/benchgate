# benchgate TODO

项目级待办（Agent / 维护者）。已完成里程碑见 `docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md`。

---

## 工程 / Git

- [ ] **Commit & merge pipeline 分支** — `feature-sim-20260702` 上 pipeline、watch 编排、blocks 示例、文档对齐、85 tests 尚未入库

---

## Agent 自动化

- [x] **`watch loop`** — 持续监听 `design/` + `models/blocks.yaml` / `blocks/*`，变更时自动跑 `watch_once`；CLI `benchgate watch loop`，Agent `watch_loop`
- [ ] **`auto_capture`** — watch 检测到 pending 元件时自动触发 `lab capture`（MINIMUM_SCOPE 旧描述曾提及，未实现）

---

## RFC 代码缺口（文档已写、实现未齐）

- [x] **`BenchModelProvider`** — `providers/bench.py` + `apply_measured_model` → `build_model()`
- [x] **`mapping/engine.build_model()`** — 统一 `provider.build()` → `register_model()` 收口
- [x] **`DatasheetModelProvider`** — `providers/datasheet.py` + `config/datasheet_models.yaml` + `ensure_datasheet_models()` on mapping sync
- [x] **`--from-meas`** — CLI `model build --from-meas block.log` + pipeline `metrics_file: *.log`
- [x] **`sim-ltspice` optional extra** — `pip install benchgate[sim-ltspice]` → `spicelib>=1.6`（`.asc` 直采，可选）
- [x] **`operating_point` 自动推断** — `sim/profile.infer_operating_point` + `operating_point_infer` in sim_profiles
- [x] **应力签核** — `stress` block, limits catalog, Ic/Pd/Tj probes, `sim stress-sweep`
- [ ] **Vendor Provider** — `ModelSource.VENDOR` 直接引用厂商 `.lib`（DATASHEET 已由 DatasheetModelProvider 实现）

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
