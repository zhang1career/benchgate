# Rules 规则包与 Tolerance 容差 schema

## 规则包 `rules/*.yaml`

```yaml
id: corp-derating-2024          # 规则包唯一 id
version: 1
source: "企业降额规范 §3"        # 出处（审计）
applies_to: [gate]              # gate | sim | monte_carlo
severity_default: fail

rules:
  - id: stress_all_passed
    when: {}                      # 空 = 始终适用；可扩展 profile / application
    limit:
      type: stress_passed         # stress_passed | check_metric | yield_gte
    severity: fail
    evidence: sim_report.stress   # 签核报告引用的证据路径
```

### `limit.type`

| type | 含义 | 参数 |
|------|------|------|
| `check_metric` | 仿真 check 指标区间 | `signal`, `metric`, `min`, `max` |
| `stress_passed` | 应力签核全通过 | `allow_warn: false` 可选 |
| `yield_gte` | Monte Carlo 良率下限 | `min_pct` |

### 默认加载顺序

1. `~/.benchgate/config/rules/corp-derating.yaml`（或内置 examples）
2. `<design>/models/rules/*.yaml`

`benchgate gate report --rules auto`（默认）合并上述路径。

---

## Tolerance `models/blocks.yaml`

```yaml
version: 1
operating_point:
  vsupply_v: 12.0
  temp_c: 25

circuit_spec:
  checks:
    - id: vout_avg
      signal: v(vout)
      metric: avg
      bounds: [8, 26]
    - id: u1_out_pp
      signal: v(net-_u1-out_)
      metric: pp
      bounds: [0.5, null]

tolerances:
  - ref: R1
    group: timing              # 同 group 共享一个 LHS 维度（批次相关）
    distribution: uniform
    tolerance_pct: 1.0          # ±1% 电阻
  - ref: R2
    group: timing
    distribution: uniform
    tolerance_pct: 1.0
  - ref: C2
    distribution: uniform
    tolerance_pct: 10.0         # ±10% 电容
```

`benchgate sim tolerance --design … --samples 200` 使用 LHS 抽样，产出 `reports/mc_tolerance/mc_tolerance.json`；
`gate report` 在存在该文件时评估 `yield_gte` 规则。

### M2：相关抽样与灵敏度

| 字段 | 含义 |
|------|------|
| `tolerances[].group` | 同组 ref 共用 LHS 一维（同批次同向漂移） |
| `sampling_dims` | 报告中的抽样维度与 ref 映射 |
| `sensitivity` | 各 metric 对各 ref 的 Spearman ρ（`u_norm` vs 指标） |
| `failure_drivers` | 失效样本（若有）上 \|ρ\| 最大的 ref；全通过时用全体样本 |
| `points[].u_norm` | 各 ref 在容差范围内的归一化位置 [0,1] |

M3（已实现）：环境轴扰动、自适应抽样、线性 surrogate 良率估计、混批 `mix`。

### M3：环境、自适应、surrogate、混批

| 字段 / 参数 | 含义 |
|-------------|------|
| `environment[]` | 扰动工作点（如 `VSUP`）；`apply: param` + `nominal_from: operating_point.vsupply_v` |
| `tolerances[].mix[]` | 多源来料：`id` + `weight` + 各自 `tolerance_pct` |
| `--strategy adaptive` | 先 warmup LHS，再对高灵敏度维度角点加密 |
| `--warmup-ratio` | adaptive 预热样本占比（默认 0.25） |
| `surrogate` | 各 metric 线性 OLS 模型（`r2`、系数） |
| `surrogate_yield_pct` | surrogate 在超立方上蒙特卡罗估计的良率 |
| `points[].mix_choice` | 各 ref 本次抽中的 mix 来源 |
| `points[].u_dim` | 按抽样维度 key 记录的 [0,1] 位置 |

### M3+：自动化、混批 model、算法增强

| 项 | 说明 |
|----|------|
| MCP `sim_tolerance` | Agent 工具与 CLI/dispatch 对齐 |
| `watch once/loop` | `blocks.yaml` 含 tolerances 时默认跑 tolerance → gate yield 签核；`--no-tolerance` 跳过 |
| `mix[].sim_name` / `sim_library` | 多源换料：按权重切换器件 SPICE model（如 Q1 vendor_a/b） |
| `environment apply: temp` | `.temp` 指令扰动（如 `temp_c` 0–50°C） |
| `--strategy sequential` | Wilson 良率 CI 序贯停止 |
| `--surrogate-degree 2` | 二次 surrogate（默认）；`1` 为线性 |
| `yield_ci_*` | 报告 Wilson 良率置信区间 |

后续（未排期）：Sobol、GP surrogate、工艺变量 DFM。

### M4：并行、粗精 transient、分层 MC

| 字段 / 参数 | 含义 |
|-------------|------|
| `--jobs N` | 并行 ngspice 样本数；`0` = `cpu_count-1` |
| `--strategy auto` | 序贯停止 + 自动并行 + 粗→细 transient |
| `tolerance_sim.coarse/fine` | `tran_step` / `tran_stop` / `maxstep` 预设；CLI `--tran-*` 可覆盖 |
| `tolerance_sim.tier` | `auto`（默认）\| `coarse` \| `fine`；`--sim-tier` 覆盖 |
| `refine_margin_pct` | 粗仿真 metric 距规格边界 ≤ 该 % 时跑细仿真；**按项目**在 `models/blocks.yaml` → `tolerance_sim` 配置，缺省回退 `sim_profiles.yaml` 对应 profile，再缺省为 5 |
| `points[].sim_tier` | 本样本实际使用的 transient 档位 |
| `mc_layers[]` | 显式分层 MC；缺省时由顶层 `tolerances`/`environment` 合成 `full` 层 |
| `blocks[].tolerances` | 块级层（`scope: block`，读 `blocks/*.net`） |
| `report.layers` | 多 layer 时各层完整报告字典 |
