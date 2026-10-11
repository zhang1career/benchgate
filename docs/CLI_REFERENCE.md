# benchgate CLI 参考

权威来源：`src/benchgate/cli.py`。本页列出叶子命令及一行说明。

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
| `benchgate sim tolerance` | `sim_tolerance` | `blocks.yaml` 容差 MC：`lhs` / `adaptive` / `sequential` / `auto`；支持 `--jobs`、粗→细；`--profile` 必填 |
| `benchgate sim cosim` | `sim_cosim` | ngspice + 固件 `control.c` 闭环 cosim；`--profile` 必填（目前只适配 dcdc/h-bridge 那块板的网络名） |
| `benchgate sim profile list` | — | 列出当前生效的 `sim_profiles.yaml` 和里面的名字 |
| `benchgate sim profile show NAME` | — | 打印这一段配置 |
| `benchgate sim profile add NAME --from OLD` | — | 复制一段配置。加 `--local` 时写进这块板的 `models/sim_profiles.yaml`，来源在板内文件找不到就从本机共用文件拿，可以同名搬入（`add X --from X --local`）。板内文件一旦存在，这块板就不再读共用文件 |
| `benchgate sim profile rename OLD NEW` | — | 改名，并改掉本仓库 `.cursor/hooks.json` 里同一个 `--profile`。台账里的旧名字不改 |
| `benchgate sim profile remove NAME` | — | 删除这个名字，并从 hook 里去掉对应的 `--profile`。`default` 要加 `--force` |
| `benchgate gate report` | `gate_report` | spec · valid_range · 波形 RMSE/标量对照 · rules；可选 `--stress-sweep` |
| `benchgate diagnose` | `diagnose` | sim + gate + lab 统一诊断；`attribution` 归因提示 |

**sim_profiles.yaml 放在哪**：设计目录下有 `models/sim_profiles.yaml` 就只用它（随项目仓库，换机器能复现）；没有才用本机共用的 `~/.benchgate/config/sim_profiles.yaml`。只给一个项目用的配置应放进项目里。

**板子专用的网表改动写在 profile 里**，benchgate 代码不针对任何器件或网络名：

```yaml
my_board:
  netlist_rewrite:            # 按顺序做字面替换
    - find: "XU5 GND +3V3 VIN_PORT AMS1117_3V3"
      replace: "XU5 VIN_PORT +3V3 GND AMS1117_3V3"
    - find: "R97 /Gate_Drive/_NO_NET_ /Gate_Drive/_NO_NET_"
      replace: "* R97 removed"
      when: ["UCC27211"]      # 可选：替换前的网表含有这些字串才做
  netlist_append:             # 加在 .control 之前
    when: ["/Sense_&_Control/ADC_IOUT"]
    lines:
      - "Eiout /Sense_&_Control/ADC_IOUT GND VALUE={...}"
```

---

## 编排

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate pipeline sync` | `pipeline_sync` | 读 `models/blocks.yaml` → 构建 subckt、写入 spec/metrics；不跑 mapping/sim/gate |
| `benchgate blocks validate` | — | 校验 `blocks.yaml` schema、路径、MC 层、`tolerance_sim` vs metric 窗口 |
| `benchgate watch once` | `watch_once` | 设计/blocks 变更或 **tagged session** → pipeline → mapping → sim [→ tolerance] → gate |
| `benchgate watch loop` | `watch_loop` | 持续 poll + debounce，重复 `watch once` 流水线 |

`watch` 常用跳过：`--no-pipeline` · `--no-sim` · `--no-gate` · `--no-auto-capture` · `--no-tolerance`。`watch once --force` 在没检测到变化时也照跑。

**Session 触发 tag**（仅这些 tag 的新 session 会触发 watch）：`anomaly` · `baseline` · `characterize` · `compare`。

---

## 实验室

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate lab list` | `lab_list` | 列出仪器与有效角色绑定 |
| `benchgate lab read` | `lab_read` | 标量读数（默认 role `dmm`；转速表 `--role tach`）；`--continuous` 流式输出 |
| `benchgate lab capture` | `lab_capture_waveform` | scope 采波形 → Session；`--tags`；`--out` 导出 CSV |
| `benchgate lab import` | `lab_import` | 外部数据 → Session：`--wave` CSV 波形、`--value name=23.6V` 读数、`--file` 附件；不碰仪器。见 [RECORD_LEDGER.md](RECORD_LEDGER.md#导入外部数据) |
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

## 记录台账与自动触发

使用说明：[RECORD_LEDGER.md](RECORD_LEDGER.md)。

每次 Session、仿真、扫参、应力扫描、容差、gate、建模、协同仿真，都会在 `<design>/models/index.jsonl` 追加一行（结论、关键数字、输入哈希、父记录、`purpose`），并重新生成给人看的 `models/INDEX.md`。报告文件之后被覆盖也不影响这份历史。所有设计类命令都接受 `--purpose "为什么跑"`；Agent 工具都接受 `purpose` / `chat_id`。`BENCHGATE_NO_INDEX=1` 关闭台账。

| 命令 | Agent 工具 | 说明 |
|------|------------|------|
| `benchgate index status` | `index_status` | 各类型最近一条记录、是否已过期、当前设计为何仍未验证；未验证时退出码 1 |
| `benchgate index list` | `index_query` | 检索记录（`--kind` `--status` `--since` `--tag` `--text`），最新在前 |
| `benchgate index rebuild` | — | 回填现有 Session 和当前磁盘上的报告；更早被覆盖的报告无法恢复；回填记录没有输入哈希，不算当前结论 |
| `benchgate index render` | — | 重新生成 `models/INDEX.md` |
| `benchgate index check` | — | gate 缺失、过期或失败时退出码 1；`--staged` 只检查本次 git 暂存改动涉及、且有 manifest 的设计（pre-commit 用），并提示没放进提交的台账；`--no-gate` 只提示、不拦 |
| `benchgate hooks install` | — | 在仓库写入 `.cursor/hooks.json` 的 `stop` hook 和 `.git/hooks/pre-commit`；`--profile` 指定自动复核用的 profile；`--no-sim` 表示这个仓库不做仿真（hook 命令带上 `--no-sim` / `--no-gate`）；已有 pre-commit 会被保留并先执行 |
| `benchgate hooks uninstall` | — | 移除上述两个 hook，恢复原 pre-commit |
| `benchgate hook stop` | — | Cursor 调用的入口：有 manifest 的设计输入相对上次 gate 变了，就跑 mapping → sim → gate（不采集、不跑容差）；所有设计检查这一轮新增的采集；有问题时回传给 Agent。`--no-sim` 只做后一项 |

“输入”指原理图各页、`models/manifest.yaml`、`models/blocks.yaml` 与 `models/blocks/*`、所用的 `sim_profiles.yaml`。自动复核默认沿用该设计最近一次仿真记录里的 profile。

做不做仿真是仓库的选择，写在 hook 命令的参数里（`--no-sim`、`--no-gate`），benchgate 不另存项目设置。`index status` 和 `INDEX.md` 同时写出这个设计用的是哪份 `sim_profiles.yaml`；用的是本机共用文件时，仿真类命令会在 stderr 提示一句。

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
| `benchgate agent tools` | — | 输出全部 Agent 工具 JSON schema（45 个，含 `benchgate_version`） |
| `benchgate agent call TOOL` | — | `dispatch(TOOL, --params '{}')` 调试入口 |
| `benchgate mcp serve` | — | stdio MCP，暴露与 `agent call` 相同的 dispatch 工具 |

---

## 顶栏命令组（17 组）

```
mapping · sim · diagnose · kicad · watch · pipeline · blocks · gate · lab · model · spec · agent · init · mcp · index · hooks · hook
```

逐组帮助：`benchgate <group> -h`。
