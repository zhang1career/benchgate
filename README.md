# benchgate

硬件项目的损失，多在风险暴露得太晚：指标在样机上才发现够不着，应力在失效后才被看见，一轮轮改板把周期和 BOM 成本抬上去。工程师需要的不是「多一套仿真工序」，而是**在投板和生产之前，尽量把问题拦住，把证据留下来**。

benchgate 跟着设计工程运行：用户画原理图、定指标；系统在后台维护模型、跑检查、出签核报告、留存实测记录。仿真、实验室采数、指标比对，都是为上述目的服务的**手段**——用户主要看结论和风险项，而不是亲自维护 SPICE 网表。

原理图仍是设计真源。benchgate 不替代编辑器，不负责布局布线；当前从 KiCad 格式工程读取网表，该后端可替换。

架构：[docs/MINIMUM_SCOPE.md](docs/MINIMUM_SCOPE.md) · 模型来源：[docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md](docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md)

---

## 用户要的结果

| 目标 | benchgate 如何支撑 | 能力 |
|------|-------------------|------|
| **提前暴露风险** — 投板前发现指标、应力问题 | 指标预算 vs 仿真/实测结果；应力扫描与降额签核；仿真前网表检查 | **已覆盖** |
| **控制生产成本** — 减少盲目打板、过量设计裕量 | 改图后自动回归（`watch`）；签核报告留档，避免重复争论 | **已覆盖** |
| **指标预算是否成立** — 功能/非功能是否在设计范围内 | 子电路配置 / `spec` → `gate report` | **已覆盖** |
| **电气应力与安全裕量** | `stress-sweep`、profile 应力探针（器件/电路级，非安规认证） | **已覆盖** |
| **实物异常时对照分析** | 实测 Session、实测 vs 预测 RMSE、`sim diagnose` | **部分覆盖** |
| **实验复现与审计** | `captured/sessions/`、`lab query` | **已覆盖** |
| **可制造性、批次一致性** | `blocks validate` + `blocks.yaml` 容差/环境/mix + `sim tolerance`（auto/并行/粗→细）→ `gate report` | **已覆盖**（M1–M4） |

用例图：[cli-usecase.puml](docs/diagrams/cli-usecase.puml)。

### 用语

| 正文 | 文件 / 命令 |
|------|-------------|
| 仿真模型与指标数据 | `models/manifest.yaml` |
| 子电路配置 | `models/blocks.yaml`、`models/blocks/` |
| 签核 / 签核报告 | `gate report` → `gate_report.json` |
| 仿真前网表检查 | `sim preflight` |
| 实验室实测 | `lab` 子命令 |

---

## 设计过程中如何运行

用户改原理图或指标配置；benchgate 在后台更新**仿真模型与指标数据**，批跑检查并写报告。无需单独开一套仿真流程。

```
改图 / 改预算
    → 更新模型与指标数据（Agent 或 watch）
    → 检查：指标、应力、网表可跑性
    → 签核报告 → reports/
    → 实测 → captured/sessions/（可选，供日后对照）
```

```bash
benchgate watch loop --design <工程目录> --profile <profile名>
```

默认在 `blocks.yaml` 含 tolerances 时跑 MC：`--tolerance-strategy auto`、`--tolerance-jobs 4`。长跑前建议 `benchgate blocks validate`。

单次全流程：`benchgate watch once`。

### 手段（实现层，非用户目标本身）

| 手段 | 作用 |
|------|------|
| Agent 汇集原厂/手册/理想化/实测模型 | 降低用户维护模型的成本，使检查能跑起来 |
| ngspice 批跑 | 在投板前对整网表做指标与应力预检 |
| 实验室 Session | 实物数据入库，支持与预测对照、审计 |
| `watch` | 改图后自动重跑，避免「改过了但没人再验」 |

流程图：`plantuml docs/diagrams/*.puml`

| 文件 | 内容 |
|------|------|
| [cli-usecase.puml](docs/diagrams/cli-usecase.puml) | 7 个业务问题（整理自 11 题）→ 命令流程 |
| [workflow-cases.puml](docs/diagrams/workflow-cases.puml) | 设计过程中后台跟跑 |
| [command-tree.puml](docs/diagrams/command-tree.puml) | CLI 命令树（mindmap） |
| [CLI_REFERENCE.md](docs/CLI_REFERENCE.md) | 全部叶子命令说明 |
| [command-flow.puml](docs/diagrams/command-flow.puml) | `watch once` 默认串联 |
| [case-charge-pump.puml](docs/diagrams/case-charge-pump.puml) | charge-pump 示例 |

---

## 安装

Python 3.11+ · ngspice · 当前网表导出需 **kicad-cli**（随 KiCad 安装，加入 PATH）

```bash
conda activate hw
pip install -e ".[lab,agent,dev]"
export PATH="/Applications/KiCad/KiCad.app/Contents/MacOS:$PATH"
```

`--design` 为设计工程目录。MCP：[docs/examples/cursor-mcp.json](docs/examples/cursor-mcp.json)。

---

## 操作示例

**投板前：指标与签核**

```bash
benchgate watch once --design design/myboard
benchgate gate report --design design/myboard
```

**应力与裕量**

```bash
benchgate gate report --design design/myboard --stress-sweep --profile default
```

**样机异常：实测入库并对照**

```bash
benchgate lab characterize --design design/myboard \
  --component-ref Q1 --mpn SS8050 --kicad-key "..."
benchgate gate report --design design/myboard
```

**charge-pump 工程**

```bash
DESIGN=/path/to/charge-pump/pcb
benchgate blocks validate --design $DESIGN --profile charge_pump
benchgate watch loop --design $DESIGN --profile charge_pump
benchgate sim diagnose --design $DESIGN
```

子电路配置示例：[docs/examples/blocks.yaml](docs/examples/blocks.yaml)。

---

## CLI 索引

[command-tree.puml](docs/diagrams/command-tree.puml) · [CLI_REFERENCE.md](docs/CLI_REFERENCE.md) · [command-flow.puml](docs/diagrams/command-flow.puml) · [MINIMUM_SCOPE §8](docs/MINIMUM_SCOPE.md#8-benchgate-自有-agent-工具最小集)

```
mapping · sim · kicad · watch · pipeline · blocks · gate · lab · model · spec · agent · mcp
```

### 报告对比与日常巡检（命令行即可）

benchgate 刻意不增加 `gate diff`、`spec list` 等子命令；以下用系统自带的 `diff`、`jq` 即可。

**签核报告两次改版之间有什么变化**

```bash
diff -u pcb/reports/gate_report.prev.json pcb/reports/gate_report.json
jq '.summary.rules' pcb/reports/gate_report.json
```

**Monte Carlo 两次跑数的 yield 与样本数**

```bash
jq '{n:.n_samples, yield:.yield_pct, ci_lo:.yield_ci_low_pct, ci_hi:.yield_ci_high_pct}' \
  pcb/reports/mc_tolerance/run_200.log \
  pcb/reports/mc_tolerance/run_200_auto_j4_ci2.json
```

**manifest 里哪些元件还缺模型**

```bash
yq '.entries[] | select(.status != "ready") | .reference' pcb/models/manifest.yaml
```

**长跑 MC 前先校验 blocks.yaml**

```bash
benchgate blocks validate --design pcb --profile charge_pump
```

---

## 仿真模型与指标数据（manifest.yaml）

工程内统一登记：SPICE 绑定、指标预算、实测/仿真达成值、签核依据。

| 字段 | 内容 | 签核 |
|------|------|------|
| `spec` | 性能预算 | 未达标 → fail |
| `provenance.metrics` | 达成值 | 与 spec 比 |
| `provenance.valid_range` | 模型适用条件 | 与工作点比 → warn |
| `sim_library` / `sim_name` | SPICE 绑定 | 网表检查 |

---

## 目录

```
<design>/
├── 原理图工程文件
├── models/manifest.yaml        # 仿真模型与指标数据
├── models/blocks.yaml          # 子电路配置（可选）
├── models/captured/sessions/   # 实测留档
└── reports/                    # 签核与检查报告

~/.benchgate/config/
```

---

## 依赖与边界

| 可选 extra | 用途 |
|------------|------|
| `[lab]` | 实验室仪器 |
| `[agent]` | MCP |
| `[sim-ltspice]` | `.asc` 网表 |

```bash
pytest && ruff check src tests
```

**当前不做**：交互仿真 GUI、ERC/DRC、Gerber、改线改布局、**可制造性与批次一致性评估**。

---

## 文档

| 路径 | 内容 |
|------|------|
| [CLI_REFERENCE.md](docs/CLI_REFERENCE.md) | 全部叶子命令说明 |
| [MINIMUM_SCOPE.md](docs/MINIMUM_SCOPE.md) | 职责边界 |
| [RFC ModelProvider](docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md) | Provider 协议 |
| [TODO.md](TODO.md) | 待办 |
