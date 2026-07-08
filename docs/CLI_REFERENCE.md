# benchgate CLI 参考

权威来源：`src/benchgate/cli.py`。本页列出全部**叶子命令**（29 个）及一行说明。

| 形式 | 文件 | 用途 |
|------|------|------|
| 命令树 | [diagrams/command-tree.puml](diagrams/command-tree.puml) | 一眼扫全层级 |
| 默认串联 | [diagrams/command-flow.puml](diagrams/command-flow.puml) | `watch once` 内部顺序 |
| 业务问题 → 命令 | [diagrams/cli-usecase.puml](diagrams/cli-usecase.puml) | 按场景选命令 |
| Agent 参数对照 | [.cursor/skills/benchgate/reference.md](../.cursor/skills/benchgate/reference.md) | CLI ↔ JSON 键名 |

全局：`benchgate --version` · `--design DIR`（默认 `design`，工程根含 `*.kicad_pro`）。

---

## 共用验证

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate mapping sync` | `mapping_sync` | 扫描原理图符号，更新 `models/manifest.yaml`；同步 SPICE 属性 |
| `benchgate mapping status` | `mapping_status` | 列出 ready / pending / unmapped |
| `benchgate sim run` | `sim_run` | `kicad-cli` 导出网表 → preflight → `ngspice -b`；checks + stress |
| `benchgate sim preflight` | — | 导出网表并做仿真前检查，**不**跑 ngspice |
| `benchgate sim stress-sweep` | `sim_stress_sweep` | 按 profile `stress_sweep` 轴扫描，汇总最坏应力 |
| `benchgate sim sweep` | `sim_sweep` | 参数/元件值网格扫描，每点采集一个 metric |
| `benchgate sim diagnose` | `sim_diagnose` | 汇总 preflight、`sim_report`、`ngspice.log` 为可操作建议 |
| `benchgate sim tolerance` | `sim_tolerance` | `blocks.yaml` 容差 MC：`lhs` / `adaptive` / `sequential` / `auto`；支持 `--jobs`、粗→细 |
| `benchgate sim cosim` | `sim_cosim` | ngspice + 固件 `control.c` 闭环 cosim |
| `benchgate gate report` | `gate_report` | metrics vs `spec`；operating_point vs `valid_range`；实测 vs 仿真 RMSE；`--rules`；可选 `--stress-sweep` |

---

## 编排

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate pipeline sync` | `pipeline_sync` | 读 `models/blocks.yaml` → 构建 subckt、写入 spec/metrics；不跑 mapping/sim/gate |
| `benchgate blocks validate` | — | 校验 `blocks.yaml` schema、路径、MC 层、`tolerance_sim` vs metric 窗口 |
| `benchgate watch once` | `watch_once` | 检测变更 → pipeline → mapping → sim [→ tolerance] → gate；见 [command-flow.puml](diagrams/command-flow.puml) |
| `benchgate watch loop` | `watch_loop` | 持续 poll + debounce，重复 `watch once` 流水线 |

`watch` 常用跳过：`--no-pipeline` · `--no-sim` · `--no-gate` · `--no-auto-capture` · `--no-tolerance`。

---

## 实验室

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate lab list` | `lab_list` | 列出仪器与有效角色绑定 |
| `benchgate lab read` | `lab_read` | 标量读数（默认 role `dmm`）；`--continuous` 流式输出 |
| `benchgate lab capture` | `lab_capture_waveform` | scope 采波形 → Session；`--out` 导出 CSV |
| `benchgate lab characterize` | `lab_capture` | 阶跃采波形 + 拟合 → subckt + manifest |
| `benchgate lab query sessions` | `lab_query_sessions` | 按时间/元件筛选历史 Session |
| `benchgate lab query metric` | `lab_metric_series` | 跨会话指标时间序列 |
| `benchgate lab query drift` | `lab_metric_drift` | 指标漂移趋势与统计 |
| `benchgate lab query waveform` | — | 从 Session 加载/导出波形（CLI only） |

Agent only：`lab_apply_model`（写 `Sim.*` + manifest，无对应 CLI）。

---

## 模型与预算

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate model build` | `model_build` | provider（`ltspice` / `datasheet` / `vendor` / `bench`）→ ngspice subckt + provenance |
| `benchgate model status` | `model_status` | 各元件 source、`valid_range`、`spec`、`metrics` |
| `benchgate spec set` | `spec_set` | 写 performance budget：`{metric: [min, max]}` |

---

## 元工具

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate kicad sim-fields` | — | KiCad 10：文本方式写 `Sim.Library` / `Sim.Name` / `Sim.Pins`（CLI only） |
| `benchgate agent tools` | — | 输出全部 Agent 工具 JSON schema |
| `benchgate agent call TOOL` | — | `dispatch(TOOL, --params '{}')` 调试入口 |
| `benchgate mcp serve` | — | stdio MCP，暴露与 `agent call` 相同的 dispatch 工具 |

---

## 顶栏命令组（12 组）

```
mapping · sim · kicad · watch · pipeline · blocks · gate · lab · model · spec · agent · mcp
```

逐组帮助：`benchgate <group> -h`。
