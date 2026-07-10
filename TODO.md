# benchgate TODO

项目级待办（Agent / 维护者）。架构与里程碑见 `docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md`、`docs/MINIMUM_SCOPE.md`。

**测试基线**：`pytest`（当前 185+ tests）

---

## Agent 自动化

- [x] **`watch loop`** — 持续监听 KiCad + `models/blocks.yaml` / `blocks/*`，变更时跑 `watch_once`；CLI `benchgate watch loop`，Agent `watch_loop`
- [x] **`watch once`** — pipeline → mapping sync → sim → gate（+ 可选 stress_sweep）；CLI / Agent `watch_once`
- [x] **`watch` + `sim tolerance`** — blocks.yaml 含 tolerances 时自动跑 MC 并写入 gate yield 签核
- [x] **`auto_capture`** — pending SUBCKT 元件触发 `lab capture`；`models/auto_capture.yaml` 可配；CLI `--no-auto-capture` / `--auto-capture-dry-run`
- [x] **MCP `sim_tolerance`** — Agent / MCP 工具注册与 dispatch 对齐

---

## ModelProvider（RFC）

- [x] **`BenchModelProvider`** — `providers/bench.py`
- [x] **`DatasheetModelProvider`** — `providers/datasheet.py` + `config/datasheet_models.yaml`
- [x] **`LtspiceModelProvider`** — `.net`/`.cir`（默认）；`.asc` 可选 `[sim-ltspice]`
- [x] **`VendorModelProvider`** — `providers/vendor.py`；CLI `model build --provider vendor --lib …`
- [x] **`build_model()` / `register_model()`** — `mapping/engine.py` 统一收口
- [x] **`--from-meas`** — `model build --from-meas` + pipeline `metrics_file: *.log`
- [x] **`ensure_datasheet_models()`** — mapping sync 时自动补 catalog 模型

---

## 仿真 · 签核

- [x] **`operating_point` 推断** — `sim/profile.infer_operating_point`
- [x] **应力签核** — `stress` block、`stress_limits.yaml`、`sim stress-sweep`
- [x] **`sim diagnose`** — 汇总 preflight / sim_report / ngspice.log
- [x] **gate `--stress-sweep`** — 单独跑 stress_sweep 再写 gate report
- [x] **连接器 preflight** — J* / `Connector:` → info `connector_dropped`（charge-pump 已验）
- [x] **KiCad 10 安全 Sim.\*** — `kicad sim-fields`（文本编辑，不用 `Schematic.save()`）
- [x] **`sim tolerance` M1–M3** — LHS/adaptive/sequential、环境轴、mix 多 provider、surrogate
- [x] **`sim tolerance` M4** — 并行 `--jobs`、`strategy auto`、粗→细 `tolerance_sim`、块级 MC 分层
- [x] **`blocks validate`** — CLI `benchgate blocks validate`；MC 前校验 YAML / 路径 / transient 窗口
- [x] **`benchgate diagnose`** — 顶层 sim + gate + lab 归因；`benchgate diagnose` / Agent `diagnose`
- [x] **实物对照分析** — `bench_compare` profile+manifest；`sim_waveform*.csv`；gate RMSE/标量；rules `waveform_rmse_lte` / `correlation_gte`
- [x] **`lab compare`** — CLI / Agent `lab_compare_waveforms`；`watch` tagged session 触发（`anomaly` 等）

---

## 测试

- [x] **`watch_once` 基础 E2E** — `tests/test_watch_once_e2e.py`（mapping + gate，无 ngspice）
- [x] **`watch_once` + blocks 变更** — 改 `blocks.yaml` / metrics → pipeline + manifest + gate 断言
- [x] **pipeline / blocks** — `tests/test_pipeline.py`
- [x] **providers / vendor / auto_capture / diagnose** — 单元测覆盖
- [x] **bench_compare / diagnose / waveform rules** — `test_bench_compare.py`、`test_diagnose.py`、`test_watch_sessions.py`、`test_rules.py`
- [x] **tolerance M1–M3** — `tests/test_tolerance.py`、`tests/test_tolerance_m3.py`
- [x] **tolerance M4 / blocks validate** — `test_tolerance_batch.py`、`test_tolerance_sim.py`、`test_tolerance_layers.py`、`test_blocks_validate.py`
- [ ] **真实 KiCad 工程 fixture** — 绑 `design/myboard` 或 charge-pump 跑 sim（CI 需 KiCad + ngspice）
- [ ] **`.asc` → netlist** — 有 LTspice/Wine 时的可选集成测（CI skip）
- [ ] **auto_capture 实机** — 实验室仪器 + `lab.yaml` 端到端（非 dry-run）

---

## 文档 / 元数据

- [x] **README 重构** — 根本问题（仿真被忽视）+ 业务问题对照表 + PlantUML
- [x] **可制造性 / 产品一致性** — M1–M4 `sim tolerance` + watch/MCP 接入；见 RFC_RULES_AND_TOLERANCE.md
- [x] **P3 换料多源** — cli-usecase.puml：`model build` → `blocks.yaml mix` → `sim tolerance` → `gate report`
- [x] **P6 blocks validate** — command-map / cli-usecase / README 报告对比（diff·jq）
- [x] **实物异常对照文档** — CLI_REFERENCE、command-tree、cli-usecase P5、`bench_compare.yaml`、`rules/bench-waveform.yaml`
- [ ] **RFC §4 实现对照表** — 与当前 providers / CLI 对齐（MINIMUM_SCOPE §8 已部分对齐）
- [ ] **`pyproject.toml` description`** — 反映自顶向下 / 自底向上 / blocks.yaml

---

## 示例 / 落地（可选）

- [ ] **`design/myboard/models/blocks.yaml`** — 仓库内可跑模板（现 `docs/examples/blocks.yaml`）
- [ ] **charge-pump** — VOUT ~20 V 设计收敛；Q2 ic_peak 信息性 warn；批量写 Sim.* 到原理图
