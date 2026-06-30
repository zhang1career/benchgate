# benchgate

Agent 辅助电路设计平台：**KiCad 10（人工审核）+ Python（实测建模/仿真/编排）+ Agent（自动化）**。

详细职责边界与依赖接口清单见 **[docs/MINIMUM_SCOPE.md](docs/MINIMUM_SCOPE.md)**。

## 职责摘要

| benchgate 做 | 委托 KiCad / MCP |
|----------|------------------|
| PyVISA 实测 → subckt | 交互仿真 GUI |
| manifest 审计 | Simulation Model Editor（厂商模型） |
| 后台 ngspice 批跑 | ERC / DRC / Gerber |
| 写实测相关的 `Sim.*` | Agent 改原理图/PCB（kicad-mcp-pro） |

## 目录

```
design/          # KiCad 工程 *.kicad_pro（项目内 models/、reports/）
~/.benchgate/    # 全局 config、subckt、state（或 BENCHGATE_HOME）
docs/            # 架构与边界文档
src/benchgate/       # Python 包（watch / mapping / lab / sim / gate / agent）
```

## 依赖

| 组件 | 用途 |
|------|------|
| KiCad 10 + `kicad-cli` | 工程真源、SPICE 网表导出 |
| [kicad-tools](https://pypi.org/project/kicad-tools/) | 读/写 `.kicad_sch`、扫描 `Sim.*` |
| [kicad-mcp-pro](https://pypi.org/project/kicad-mcp-pro/) | 可选 MCP；Agent 改 sch/PCB |
| ngspice | benchgate 后台批跑 |
| PyVISA | 实验室采数 |

## 快速开始

CLI 命令为 **`benchgate`**（bench 实测 → SPICE 建模 → 回归仿真 → 质量门禁）。Python 包名仍为 `benchgate`。

```bash
cd benchgate
conda create -n benchgate python=3.12 -y && conda activate benchgate
# 或: python -m venv .venv && source .venv/bin/activate
pip install -e ".[lab,dev]"

# 扫描原理图 → manifest（写入 <design>/models/manifest.yaml）
benchgate mapping sync --design design/myboard

# 批跑仿真（内部调用 kicad-cli + ngspice；产出在 <design>/reports/sim）
benchgate sim run --design design/myboard

# 单次 watch 流水线
benchgate watch once --design design/myboard
```

## 仪器控制（lab）

统一的仪器抽象在 `src/benchgate/instruments/`：**Transport（VISA / Serial）→ Driver（适配器）→ Capability（Protocol）**，经 Registry/Factory 绑定到逻辑角色 `scope / dmm / awg`。

| 角色 | 能力接口 | 内置驱动 | 说明 |
|------|----------|----------|------|
| `scope` | `Oscilloscope` | `rigol_ds1104z` | 波形采集（USB VISA / SCPI） |
| `dmm` | `ScalarReader` | `uni_t_ut61e` | 只读串口遥测（ES51922 解包） |
| `awg` | `DigitalStimulus` | `tars_shell` | TARS GPIO 数字电平 / 阶跃沿（幅度固定为逻辑电平；PWM 预留） |

配置示例见 [`docs/examples/instruments.yaml`](docs/examples/instruments.yaml)（全局，放 `~/.benchgate/config/`）与 [`docs/examples/lab.yaml`](docs/examples/lab.yaml)（项目级，放 `<design>/models/`）。角色绑定优先级：CLI 参数 > 项目 `lab.yaml` > 全局 `instruments.yaml`。

```bash
# 列出仪器与生效的角色绑定
benchgate lab list --design design/myboard

# 读取标量（默认角色 dmm；可 --instrument 指定具体设备；--count N 连读）
benchgate lab read --design design/myboard --count 5

# 采集波形（默认角色 scope；--out 导出 CSV）
benchgate lab capture --design design/myboard --channel 1 --out wave.csv

# 完整表征：采集 + 拟合 + 写 subckt/manifest
benchgate lab characterize --design design/myboard \
  --component-ref C1 --mpn 100n --kicad-key "Device:C::100n"

# 沿时间轴查询：历史会话 / 派生指标 / 趋势 / 单次波形
benchgate lab query sessions --design design/myboard --component-ref C1
benchgate lab query metric   --design design/myboard --metric tau_s --component-ref C1
benchgate lab query drift    --design design/myboard --metric tau_s --component-ref C1
benchgate lab query waveform --design design/myboard --session <id> --t-start 0 --t-end 5e-3 --out w.csv
```

采集数据以 **Session** 为单位落盘于 `<design>/models/captured/sessions/<id>/`：波形 `*.npz`、标量序列 `*.csv`、`derived.json`、`session.yaml`。详见 `src/benchgate/lab/store.py`。

分析层 `src/benchgate/lab/analyze.py` 提供两类沿时间轴计算：单次采集内的 `crop` / `resample_uniform` / `align_waveforms` / `overlay` / `compare_waveforms`，以及跨会话的 `metric_stats` / `drift`（线性趋势，slope/s）/ `compare_runs`。

> 重试策略统一：任意仪器操作默认重试 3 次（`RetryPolicy`），耗尽抛 `InstrumentError`；过载/未触发等语义状态通过 `Reading.flags` 表达，不触发重试。
