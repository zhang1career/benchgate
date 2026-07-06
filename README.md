# benchgate

**KiCad 旁的设计–验证闭环：自顶向下定指标、自底向上出模型，经 ngspice 全局仿真与 gate 收敛。**

benchgate 在 KiCad 10 工程旁运行，把 **实验室实测** 与 **局部仿真表征** 统一为 manifest 里的 subckt + metrics，再经 **ngspice 批跑** 与 **gate** 完成签核。原理图/PCB 仍在 KiCad 人工审核；Agent 改图走 MCP。

> 架构与职责边界：[docs/MINIMUM_SCOPE.md](docs/MINIMUM_SCOPE.md)（[§1.1 设计–验证闭环](docs/MINIMUM_SCOPE.md#11-设计验证闭环)）

---

## 闭环结构

benchgate 支持两种设计方法，共享同一条验证脊柱：

```
                    ┌──────────────────────────────────────┐
  自顶向下          │  spec / budget（要什么）              │
  （要求驱动）       │  blocks.yaml · spec set              │
                    └──────────────┬───────────────────────┘
                                   │  gate 比对
                    ┌──────────────▼───────────────────────┐
  共享脊柱          │  manifest → sim (ngspice) → gate     │
                    └──────────────▲───────────────────────┘
                                   │  metrics + subckt
                    ┌──────────────┴───────────────────────┐
  自底向上          │  表征（做到了什么）                    │
  （证据驱动）       │  lab · LTspice · 厂商模型 …           │
                    └──────────────────────────────────────┘
                                   ▲
                    ┌──────────────┴───────────────────────┐
  基础环节          │  实验室 lab：采数 · Session · 拟合    │
                    └──────────────────────────────────────┘
```

| 方向 | 起点 | 产出 | benchgate 入口 |
|------|------|------|----------------|
| **自顶向下** | 系统/块级性能预算 | `spec` + `operating_point` | `blocks.yaml` · `spec set` · `pipeline sync` |
| **自底向上** | 实测或局部仿真 | subckt + `metrics` + `valid_range` | `lab characterize` · `model build` · `pipeline sync` |
| **基础** | 台架仪器 | 结构化 Session、波形、拟合参数 | `lab capture` · `lab characterize` · `instruments.yaml` |

**gate** 是两种方向的汇合点：`metrics` vs `spec`（硬 fail）、工作点 vs `valid_range`（软 warn）、bench 波形 vs 仿真 RMSE。

全局仿真只有 **ngspice**；LTspice、台架等均为**离线**模型来源，不做运行时 co-sim。

---

## 1. 自顶向下

从指标出发，再驱动局部设计与表征。

1. 在 `models/blocks.yaml` 写 **spec**（如 `eff_pct: [90, 100]`）和 **operating_point**
2. 按 spec 在 LTspice 等工具里调电路，导出 `blocks/*.net` + `*.metrics.json`
3. `benchgate watch once` → pipeline 构建 subckt、写入 manifest、跑全局 sim、出 gate 报告
4. **spec_failures > 0** → 改电路或修订预算，重复

```bash
# 编辑 models/blocks.yaml 与 models/blocks/* 后
benchgate watch once --design design/myboard
```

示例：[docs/examples/blocks.yaml](docs/examples/blocks.yaml) · 细节：[RFC §10](docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md#10-扩展自顶向下-spec--metrics-闭环v03待评审)

---

## 2. 自底向上

从可测/可仿的具体对象出发，逐级集成到全局网表。

| 表征手段 | 典型场景 | benchgate 命令 |
|----------|----------|----------------|
| **实验室**（台架） | 样机、签核、与 bench 波形对齐 | `lab characterize` |
| **局部仿真**（LTspice 等） | 无硬件的设计阶段、复杂 IC 块 | `model build` · `pipeline sync` |
| **厂商/手册模型** | 已知 SPICE 库 | `model build`（后续扩展） |

共性路径：**表征 → subckt → manifest → sim → gate**。

```bash
benchgate mapping sync --design design/myboard
benchgate lab characterize --design design/myboard \
  --component-ref C1 --mpn 100n --kicad-key "Device:C::100n"
benchgate sim run --design design/myboard
benchgate gate report --design design/myboard
```

同一 manifest 可混用来源：块 A 来自台架、块 B 来自 LTspice。

---

## 实验室（基础环节）

实验室不是某条「可选路径」，而是 **设计–验证闭环的物理测量层**：无论自顶向下还是自底向上，最终签核往往要回到可重复的实测 Session。

- **Session**：`models/captured/sessions/<id>/`（波形 NPZ + 标量 + derived 指标）
- **拟合 subckt**：`~/.benchgate/models/subckt/*.lib` → 登记 manifest 的 `provenance.metrics`
- **配置**：全局 `~/.benchgate/config/instruments.yaml` + 项目 `models/lab.yaml`（CLI 可临时覆盖）

```bash
cp docs/examples/instruments.yaml ~/.benchgate/config/instruments.yaml
benchgate lab list --design design/myboard
benchgate lab capture --design design/myboard --channel 1
```

更多 lab 子命令：[MINIMUM_SCOPE §8](docs/MINIMUM_SCOPE.md#8-benchgate-自有-agent-工具最小集)

---

## 核心数据（manifest）

| 字段 | 方向 | gate |
|------|------|------|
| `spec` | 自顶向下 | 未达标 → **fail** |
| `provenance.metrics` | 自底向上 | 与 spec 比对 |
| `provenance.valid_range` | 模型契约 | 与 operating_point 比对 → **warn** |

---

## 快速开始

**环境**：Python 3.11+ · KiCad 10 + `kicad-cli` · ngspice · 台架需 `pip install -e ".[lab]"`

```bash
git clone <repo> benchgate && cd benchgate
pip install -e ".[lab,dev]"
```

`--design` 指向 KiCad 工程根（含 `.kicad_pro`）。

**benchgate 不做**：交互仿真 GUI、ERC/DRC、Gerber、Agent 改线改布局 → KiCad / kicad-mcp-pro。

---

## 目录约定

```
design/myboard/
├── *.kicad_pro
├── models/
│   ├── manifest.yaml       # 绑定 + spec + provenance
│   ├── blocks.yaml         # 自顶向下自动化（可选）
│   ├── blocks/             # 局部仿真网表 + metrics
│   ├── lab.yaml
│   └── captured/sessions/  # 实验室 Session
└── reports/

~/.benchgate/               # 本机全局，勿提交 Git
├── config/instruments.yaml
├── models/subckt/
└── state/
```

---

## 依赖 · 开发 · 文档

| 组件 | 用途 |
|------|------|
| KiCad 10 + `kicad-cli` | 工程真源 |
| kicad-tools | 读/写 `.kicad_sch` |
| ngspice | 全局批跑 |
| PyVISA / pyserial（`[lab]`） | 实验室 |

```bash
pytest && ruff check src tests
```

| 文档 | 内容 |
|------|------|
| [MINIMUM_SCOPE](docs/MINIMUM_SCOPE.md) | 职责边界、Agent 工具 |
| [RFC ModelProvider](docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md) | 局部模型、spec/metrics |
| [blocks.yaml 示例](docs/examples/blocks.yaml) | 自顶向下配置 |
| [instruments / lab 示例](docs/examples/) | 实验室配置 |
