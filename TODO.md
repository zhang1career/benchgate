# benchgate TODO

项目级待办（Agent / 维护者）。架构与里程碑见 `docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md`、`docs/MINIMUM_SCOPE.md`。

**测试基线**：`pytest`（当前 220 tests）

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
- [x] **AC raw 复数解析** — `Flags: complex` 从前被当实数读，交错实/虚部到相邻变量，**不报错**；`parse_ngspice_raw` 现按 flag 读 `complex128`
- [x] **频域 metric 原语** — `bw_3db` / `peaking_db` / `gain_db_max` / `gain_db_first`；对解析 RLC 验证
- [x] **`sim block-sweep`** — 独立 block testbench 扫描，不需 KiCad 工程 / `sim_profiles.yaml`；`--metric` 可重复，每点一次仿真取多 metric
- [x] **参数化 `.subckt` 提取** — pin 后的默认参数从前被当 pin 计入 manifest `sim_pins`
- [x] **rule pack 默认回退** — 从前无 `models/rules/` 时静默加载 `docs/examples/rules`（含 `corp-derating-2024`，无 stress sweep 即硬失败）；默认只取 `$BENCHGATE_HOME/config/rules` + 设计自己的 `models/rules`，并加 `gate report --rules none`

- [ ] **block metrics 自动派生** — `blocks.yaml` 的 `metrics` 目前仍靠人工填 `*.metrics.json`，即 spec 对照的是手抄的数。让 block 声明 testbench + measure，`pipeline sync` 直接跑出 metrics（`sim block-sweep` 已提供执行层，缺 blocks.yaml 侧的声明与串联）
- [ ] **瞬态多点 measure** — `block-sweep` 每点只出标量；settling / charge 这类需要每点多条 `meas` 的研究仍要外部脚本（见 tars-io-buffer `sim/run_sims.py`）

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

---

## Lab · 数字总线 timing 表征（待做）

> 动机：固件/协议联调（如 TARS soft-I²C）仍以示波器交互为主；benchgate 不替代示波器或 USB shell，
> 只把 **可复现时序证据 → metrics → gate** 接进「表征 → 模型/spec → 签核」脊柱。
> 目标仪器示例：Rigol DS1104Z Plus + 逻辑探头（RPL1116）+（可选）I²C 解码。

- [ ] **`lab` 数字通道 / 总线采数** — 在现有 `lab capture` / `lab_capture_waveform` 之上支持 MSO 数字通道（或 SCL/SDA 绑定）；session 入库 `captured/sessions`，tags 如 `bus=i2c`
- [ ] **I²C / 总线 timing metrics** — 从波形或边沿表提取可 gate 的标量（例：`f_scl`、`t_r`/`t_f`、`t_stop_to_start`、ACK 窗口、时钟拉伸上限）；**不做**完整协议栈 / 寄存器 walk / 固件调试
- [ ] **manifest / lab 配置：bus interface spec** — 设计侧声明时序预算（与 pinmap / 网名绑定，如 `SCL`/`SDA`↔通道）；未知或 LITE 类约束可引用常量表，但不复制 MCU 协议实现
- [ ] **gate：lab timing vs bus spec** — rules 对照 session metrics（类似现有 waveform RMSE / correlation）；失败进 `gate_report.json`
- [ ] **与示波器分工写清（文档）** — 交互排障 / decode 用人眼+示波器 UI；benchgate 只消费 SCPI 证据做回归签核；CLI_REFERENCE / MINIMUM_SCOPE 补边界（明确排除 nodebus、flash、soft-I²C 状态机）
- [ ] **DS1104(+LA) 实机路径** — `instruments.yaml` / `lab.yaml` 示例；优先 SCPI 拉数字波形或测量；示波器自带 I²C decode 仅作人工确认，非硬依赖
