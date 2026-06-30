# benchgate

**把示波器上的波形，变成 KiCad 里可回归仿真的 SPICE 模型。**

benchgate 是面向硬件工程师的 Python 工具链：在 KiCad 10 工程旁运行，连接实验室仪器（示波器、万用表、数字激励），完成 **实测采数 → 拟合 subckt → 写回 manifest / Sim.\*** → **后台 ngspice 批跑** → **实测 vs 仿真质量门禁**。原理图与 PCB 仍在 KiCad 里人工审核；Agent 可通过 MCP 改图，benchgate 负责「测到仿」这条闭环。

> 架构细节、职责边界与接口清单见 **[docs/MINIMUM_SCOPE.md](docs/MINIMUM_SCOPE.md)**。

---

## 它解决什么问题

| 痛点 | benchgate 的做法 |
|------|------------------|
| 实测参数散落在 CSV、截图里 | 按 **Session** 结构化落盘（NPZ 波形 + 指标 + 元数据） |
| SPICE 模型与板子上的元件对不上 | `manifest.yaml` 审计 + 只写回实测相关的 `Sim.*` |
| 改了一个电容，不知道仿真还准不准 | `gate` 对比 bench 波形与仿真 RMSE |
| 每台电脑的仪器地址不同 | 全局 `instruments.yaml` + 项目 `lab.yaml`，CLI 可临时覆盖 |

**benchgate 不做的事**（交给 KiCad / MCP）：交互仿真 GUI、ERC/DRC、Gerber、Agent 改线改布局。见 [职责边界](docs/MINIMUM_SCOPE.md#2-职责边界表)。

---

## 工作流一览

```
KiCad 原理图 (真源)
       │
       ▼
 mapping sync ──► models/manifest.yaml
       │
       ▼
 lab characterize ──► captured/sessions/<id>/  +  ~/.benchgate/models/subckt/
       │
       ▼
 sim run ──► reports/sim/
       │
       ▼
 gate ──► reports/gate_report.json   (bench vs sim)
```

---

## 快速开始

### 环境要求

- **Python 3.11+**（推荐 3.12）
- **KiCad 10** + `kicad-cli`（在 PATH 中）
- **ngspice**（KiCad 自带或系统安装）
- 实验室功能额外需要：`pip install -e ".[lab]"`（PyVISA、pyserial）

### 安装

```bash
git clone <your-repo-url> benchgate && cd benchgate

conda create -n benchgate python=3.12 -y && conda activate benchgate
# 或: python -m venv .venv && source .venv/bin/activate

pip install -e ".[lab,dev]"
```

### 配置仪器（可选，使用 lab 命令时需要）

```bash
mkdir -p ~/.benchgate/config
cp docs/examples/instruments.yaml ~/.benchgate/config/instruments.yaml
# 编辑 address：串口路径、VISA 资源名等（每台机器不同，不要提交到 Git）
```

项目级角色与采数默认可放在 `<design>/models/lab.yaml`，示例见 [`docs/examples/lab.yaml`](docs/examples/lab.yaml)。

### 跑通主流程

将 `--design` 指向你的 KiCad 工程根目录（内含 `.kicad_pro`）。仓库内示例路径为 `design/myboard`：

```bash
# 1. 扫描原理图，生成/更新 manifest
benchgate mapping sync --design design/myboard

# 2. 后台 SPICE 批跑（产出在 <design>/reports/sim）
benchgate sim run --design design/myboard

# 3. 单次流水线：sync → sim → gate
benchgate watch once --design design/myboard
```

外部工程同样适用，例如：

```bash
benchgate mapping sync --design /path/to/h-bridge-pcb
```

---

## 实验室（lab）

仪器层：**Transport（VISA / Serial）→ Driver → Capability**，经 Registry 绑定到逻辑角色。

| 角色 | 典型设备 | 能力 |
|------|----------|------|
| `scope` | Rigol DS1104Z | 波形采集 |
| `dmm` | Uni-T UT61E | 标量读数（串口） |
| `awg` | TARS（GPIO） | 数字阶跃激励（非模拟 AWG） |

**配置优先级**（高 → 低）：CLI `--scope` / `--dmm` / `--awg` → 项目 `lab.yaml` → 全局 `instruments.yaml`。环境变量 `BENCHGATE_<ROLE>_ADDRESS` 可覆盖地址。

```bash
# 查看当前绑定
benchgate lab list --design design/myboard

# 万用表连读
benchgate lab read --design design/myboard --count 5

# 示波器单次采集（--out 可选导出 CSV）
benchgate lab capture --design design/myboard --channel 1

# 完整表征：采数 + RC 拟合 + 写 subckt + 更新 manifest
benchgate lab characterize --design design/myboard \
  --component-ref C1 --mpn 100n --kicad-key "Device:C::100n"

# 历史查询：会话列表 / 指标序列 / 漂移 / 波形时间窗
benchgate lab query sessions --design design/myboard --component-ref C1
benchgate lab query metric   --design design/myboard --metric tau_s --component-ref C1
benchgate lab query drift    --design design/myboard --metric tau_s --component-ref C1
benchgate lab query waveform --design design/myboard --session <id> --t-start 0 --t-end 5e-3
```

---

## 文件都落在哪

以 `--design design/myboard` 为例：

```
design/myboard/
├── *.kicad_pro                 # KiCad 工程（Git 真源）
├── models/
│   ├── manifest.yaml           # 元件 ↔ SPICE 绑定 + measured.session_id
│   ├── lab.yaml                # 本项目仪器角色与 capture 默认
│   └── captured/sessions/<id>/ # 实测 Session（npz / csv / derived.json）
└── reports/                    # 仿真、门禁报告、图表（建议放这里）

~/.benchgate/                   # 本机全局（勿提交 Git）
├── config/instruments.yaml
├── models/subckt/              # characterize 生成的 .lib
└── state/                      # watch 状态
```

复制 [`docs/examples/instruments.yaml`](docs/examples/instruments.yaml) 到 `~/.benchgate/config/` 即可起步；**不要**把整个 `~/.benchgate` 目录放进 Git。

---

## 目录结构

```
benchgate/
├── design/              # KiCad 工程（本地；可 track lab.yaml 模板）
├── docs/                # 架构文档与配置示例
├── src/benchgate/       # Python 包
│   ├── instruments/     # 仪器 Transport / Driver / Registry
│   ├── lab/             # 采数、存储、分析
│   ├── mapping/         # manifest 同步
│   ├── sim/             # kicad-cli + ngspice 批跑
│   ├── gate/            # bench vs sim 质量报告
│   └── agent/           # Agent 工具 dispatch
└── tests/
```

---

## 依赖一览

| 组件 | 用途 |
|------|------|
| [KiCad 10](https://www.kicad.org/) + `kicad-cli` | 工程真源、SPICE 网表 |
| [kicad-tools](https://pypi.org/project/kicad-tools/) | 读/写 `.kicad_sch`、扫描 `Sim.*` |
| [kicad-mcp-pro](https://pypi.org/project/kicad-mcp-pro/) | 可选；Agent 改 sch/PCB |
| ngspice | 后台批跑仿真 |
| PyVISA / pyserial | 实验室仪器（`[lab]` 可选依赖） |

---

## 开发

```bash
pytest          # 单元测试
ruff check src tests
```

---

## 相关文档

- [最小职责边界（MINIMUM_SCOPE）](docs/MINIMUM_SCOPE.md)
- [全局仪器配置示例](docs/examples/instruments.yaml)
- [项目 lab 配置示例](docs/examples/lab.yaml)
