# benchgate CLI 参考

权威来源：`src/benchgate/cli.py`。本页列出全部**叶子命令**（44 个）及一行说明。

| 形式 | 文件 | 用途 |
|------|------|------|
| 命令树 | [diagrams/command-tree.puml](diagrams/command-tree.puml) | 一眼扫全层级 |
| 默认串联 | [diagrams/command-flow.puml](diagrams/command-flow.puml) | `watch once` 内部顺序 |
| 业务问题 → 命令 | [diagrams/cli-usecase.puml](diagrams/cli-usecase.puml) | 按场景选命令 |
| Agent 参数对照 | [.cursor/skills/benchgate/reference.md](../.cursor/skills/benchgate/reference.md) | CLI ↔ JSON 键名 |
| 探针映射 | [examples/bench_compare.yaml](examples/bench_compare.yaml) | `bench_compare` profile + manifest |
| 波形签核规则 | [examples/rules/bench-waveform.yaml](examples/rules/bench-waveform.yaml) | `waveform_rmse_lte` / `correlation_gte` |

全局：`benchgate --version` · `--design DIR`（默认 `design`，工程根含 `*.kicad_pro`）。

---

## 共用验证

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate mapping sync` | `mapping_sync` | 扫描原理图符号，更新 `models/manifest.yaml`；同步 SPICE 属性 |
| `benchgate mapping status` | `mapping_status` | 列出 ready / pending / unmapped |
| `benchgate sim run` | `sim_run` | `kicad-cli` 导出网表 → preflight → `ngspice -b`；checks + stress；导出 `sim_waveform*.csv` |
| `benchgate sim preflight` | — | 导出网表并做仿真前检查，**不**跑 ngspice |
| `benchgate sim stress-sweep` | `sim_stress_sweep` | 按 profile `stress_sweep` 轴扫描，汇总最坏应力 |
| `benchgate sim sweep` | `sim_sweep` | 参数/元件值网格扫描；`--metric` 可重复，每点一次仿真取多个 metric |
| `benchgate sim block-sweep` | `sim_block_sweep` | 扫描独立 block testbench `.cir`，**不需要** KiCad 工程或 `sim_profiles.yaml` |
| `benchgate sim diagnose` | `sim_diagnose` | 汇总 preflight、`sim_report`、`ngspice.log`（仅仿真侧） |
| `benchgate sim tolerance` | `sim_tolerance` | `blocks.yaml` 容差 MC：`lhs` / `adaptive` / `sequential` / `auto`；支持 `--jobs`、粗→细 |
| `benchgate sim cosim` | `sim_cosim` | ngspice + 固件 `control.c` 闭环 cosim |
| `benchgate gate report` | `gate_report` | spec · valid_range · 波形 RMSE/标量对照 · rules；可选 `--stress-sweep` |
| `benchgate diagnose` | `diagnose` | sim + gate + lab 统一诊断；`attribution` 归因提示 |

---

## 编排

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate pipeline sync` | `pipeline_sync` | 读 `models/blocks.yaml` → 构建 subckt、写入 spec/metrics；不跑 mapping/sim/gate |
| `benchgate blocks validate` | — | 校验 `blocks.yaml` schema、路径、MC 层、`tolerance_sim` vs metric 窗口 |
| `benchgate watch once` | `watch_once` | 设计/blocks 变更或 **tagged session** → pipeline → mapping → sim [→ tolerance] → gate |
| `benchgate watch loop` | `watch_loop` | 持续 poll + debounce，重复 `watch once` 流水线 |

`watch` 常用跳过：`--no-pipeline` · `--no-sim` · `--no-gate` · `--no-auto-capture` · `--no-tolerance`。

**Session 触发 tag**（仅这些 tag 的新 session 会触发 watch）：`anomaly` · `baseline` · `characterize` · `compare`。

---

## 实验室

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate lab list` | `lab_list` | 列出仪器与有效角色绑定 |
| `benchgate lab read` | `lab_read` | 标量读数（默认 role `dmm`；转速表 `--role tach`）；`--continuous` 流式输出 |
| `benchgate lab capture` | `lab_capture_waveform` | scope 采波形 → Session；`--tags`；`--out` 导出 CSV |
| `benchgate lab characterize` | `lab_capture` | 阶跃采波形 + 拟合 → subckt + manifest；默认 tag `characterize`；默认重跑 sim+gate |
| `benchgate lab compare` | `lab_compare_waveforms` | 单 session 波形 vs `reports/sim/*.csv`（RMSE、correlation） |
| `benchgate lab query sessions` | `lab_query_sessions` | 按时间/元件筛选历史 Session |
| `benchgate lab query metric` | `lab_metric_series` | 跨会话指标时间序列 |
| `benchgate lab query drift` | `lab_metric_drift` | 指标漂移趋势与统计 |
| `benchgate lab query waveform` | — | 从 Session 加载/导出波形（CLI only） |
| `benchgate lab sa sweep` | `lab_sa_sweep` | 频谱仪扫频 → Session；`--out` 导出 CSV |
| `benchgate lab sa peak` | `lab_sa_peak` | 读屏上峰值（dBm）；`--mode AVR\|MIN\|MID\|RMS` |
| `benchgate lab sa floor` | `lab_sa_floor` | 读屏上噪声底（ADC） |
| `benchgate lab sa gen` | `lab_sa_gen` | 跟踪源控制（频率 / 功率 / 衰减） |
| `benchgate lab sa cal` | `lab_sa_cal` | S 参数校准（OPEN / SHORT / LOAD） |
| `benchgate lab sa sparam` | `lab_sa_sparam` | 采集 S 参数历史曲线 → Session |
| `benchgate lab thermal capture` | `lab_thermal_capture` | 热成像采帧 → Session（`kind: frame2d`）；默认 unit=`count`；`--apply-calibration` 才写 °C；`--reduce` 含 `median` |
| `benchgate lab thermal hotspot` | `lab_thermal_hotspot` | 对已存 session 重算热点 / 阈值（无硬件） |
| `benchgate lab thermal calibrate` | `lab_thermal_calibrate` | 两点 count→°C 标定落盘（须 capture `--apply-calibration` 才应用） |
| `benchgate lab thermal map` | `lab_thermal_map` | 4 点单应 → 板面 mm → KiCad 候选；板外为 `out_of_board` |
| `benchgate lab thermal register` | `lab_thermal_register` | 4 个亮点拟合成长×宽矩形单应（夹具坐标，写入 `~/.benchgate/config/thermal_map/`） |
| `benchgate lab thermal baseline` | `lab_thermal_baseline` | 空闲态 median+sigma 基线 → `~/.benchgate/config/thermal_baseline/` |
| `benchgate lab thermal alert` | `lab_thermal_alert` | ΔT 相对基线报警；每个区域各自映射到 KiCad 候选 |
| `benchgate lab thermal watch` | `lab_thermal_watch` | 轮询 alert（独立于 KiCad `watch_loop`）；gate 只用 lab.yaml 里点名的 session |

Agent only：`lab_apply_model`（写 `Sim.*` + manifest，无对应 CLI）。

---

## 模型与预算

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate model build` | `model_build` | provider（`ltspice` / `datasheet` / `vendor` / `bench`）→ ngspice subckt + provenance |
| `benchgate model status` | `model_status` | 各元件 source、`valid_range`、`spec`、`metrics` |
| `benchgate spec set` | `spec_set` | 写 performance budget：`{metric: [min, max]}` |

---

## 脚手架

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate init` | — | 在 `--design` 下生成 `models/blocks.yaml`、`models/rules/project-spec.yaml`、`models/lab.yaml`；`--force` 覆盖已有文件 |

---

## 元工具

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate kicad sim-fields` | — | KiCad 10：文本方式写 `Sim.Library` / `Sim.Name` / `Sim.Pins`（CLI only） |
| `benchgate agent tools` | — | 输出全部 Agent 工具 JSON schema（33 个，含 `benchgate_version`） |
| `benchgate agent call TOOL` | — | `dispatch(TOOL, --params '{}')` 调试入口 |
| `benchgate mcp serve` | — | stdio MCP，暴露与 `agent call` 相同的 dispatch 工具 |

---

## 顶栏命令组（14 组）

```
mapping · sim · diagnose · kicad · watch · pipeline · blocks · gate · lab · model · spec · agent · init · mcp
```

逐组帮助：`benchgate <group> -h`。
