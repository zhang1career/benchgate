# 记录台账与自动复核

benchgate 每写一次结果，就在设计目录的 `models/index.jsonl` 追加一行，并刷新给人看的 `models/INDEX.md`。报告文件下次运行会被覆盖，这份台账留下当时的结论。

台账自己不会让仿真发生。两道触发挂在本来就会做的动作上：Agent 结束一轮，以及提交改过的设计文件。仪器采集不在自动路径里。

台账只记事实：跑了什么、结论是什么、当时的设计输入是什么。要不要跑仿真、要不要按 gate 拦提交，由每个仓库装 hook 时自己选，写在该仓库的 hook 命令里。benchgate 不保存「哪个项目做不做仿真」。

命令一览见 [CLI_REFERENCE.md](CLI_REFERENCE.md) 的「记录台账与自动触发」。

---

## 每个仓库装一次

在硬件项目的 git 根目录执行，二选一：

```bash
benchgate hooks install --profile <这个板常用的 profile>   # 做仿真的仓库
benchgate hooks install --no-sim                          # 不做仿真的仓库
```

写入两处：

| 文件 | 默认 | 加 `--no-sim` |
|------|------|---------------|
| `.cursor/hooks.json`（`benchgate hook stop`） | Agent 一轮结束时，有 `models/manifest.yaml` 的设计若输入相对上次 gate 变了，跑 mapping → 仿真 → 应力（profile 里有的话）→ gate，不采集、不跑容差，未通过时把结论送回给 Agent。所有设计都检查这一轮新增的实验记录。 | 不跑仿真，只检查新增的实验记录。命令末尾带 `--no-sim`。 |
| `.git/hooks/pre-commit`（`benchgate index check --staged`） | 暂存区改了有 manifest 的设计的原理图、`manifest.yaml` 或 `blocks.yaml`，而 gate 缺失、已过期或失败时，拒绝提交。台账改了却没放进这次提交时提示一句。 | 不拦提交，只提示台账。命令末尾带 `--no-gate`。 |

仓库里原有的 pre-commit 会保留，并且先执行。重新执行 `hooks install` 会替换 benchgate 自己的那两条，可以随时在两种之间切换。

hook 运行时自己找设计：有 `*.kicad_pro` 和 `models/manifest.yaml` 的目录，或者 `models/` 下已经有台账、采集数据的目录（不要求有 KiCad 工程）。还没有任何数据的新目录，第一次采集或导入后自动纳入。

`--profile` 可以省略。省略时，自动复核沿用该设计最近一次仿真记录里的 profile；记录里还没有 profile 时用 `default`。第一次安装建议写上，因为回填的历史记录不带 profile。已有的名字用 `benchgate sim profile list` 查看；复制、改名、删除用同组的 `add`、`rename`、`remove`。

撤掉：`benchgate hooks uninstall`。

临时停用全部 hook：`BENCHGATE_NO_HOOKS=1`。只停台账写入：`BENCHGATE_NO_INDEX=1`。

改完 benchgate 后，重启 Cursor 里的 benchgate MCP，新的 `index_status`、`index_query`、`lab_import` 和 `purpose` 参数才会出现。保存 `hooks.json` 后 Cursor 会重新加载 hook；不生效时重启 Cursor。

---

## 不做仿真的仓库

没有仿真模型、或者仿真跑不通的板，用 `--no-sim` 安装，不需要基线。

一轮对话结束时：

- 检查这一轮新增的采集：有空通道、采样率异常、没写目的的，告诉 Agent，让它转告你。重新测量要先征得你同意。同一条记录只提醒一次。
- 台账 `models/index.jsonl` 还没加入 git、或被 `.gitignore` 忽略时，提醒一次。

提交时不拦，台账改了却没放进这次提交时提示一句。

`INDEX.md` 顶部写事实：没有仿真记录时写「没有仿真或 gate 记录」；有的话写当前 gate 是否通过、是否过期。`index status` 在没有通过的 gate 时退出码为 1，这只表示「没有验证结论」，不做仿真的仓库可以不看它。

数据怎么进来：

- benchgate 自己采的（`lab capture`、`lab sa`、`lab thermal`，或 Agent 调对应的 MCP 工具），每次自动记一行。
- 不是 benchgate 采的，用 `lab import` 导入，见下一节。

---

## 导入外部数据

示波器 U 盘里的 CSV、厂家软件导出的数据、手抄的读数、截图，用 `lab import` 存成一次 Session，记一行台账。不碰仪器。

```bash
benchgate lab import --design pcb \
  --wave ~/Downloads/ripple.csv \
  --value vout=23.6V --value iin=85mA \
  --file ~/Downloads/screen.png \
  --instrument "DS1054Z U 盘导出" \
  --at 2026-10-11T09:30 \
  --purpose "12V 输入、满载时的输出纹波"
```

| 参数 | 说明 |
|------|------|
| `--wave` | 波形 CSV，可重复。全是数字的行算数据；第一列是时间，后面每列一个通道；数据上方最后一行文字当作通道名。原文件同时作为附件保留。 |
| `--time-scale` | 时间列乘的系数。时间列单位是 ms 时写 `0.001`。 |
| `--sample-interval` | 第一列是采样序号时用：时间 = 序号 × 这个秒数。 |
| `--value` | 读数，写成 `名字=数值单位`，可重复。单位按原样保存，不换算。 |
| `--file` | 附件，可重复：截图、PDF、原始导出文件。复制进这次 Session 的 `attachments/`。 |
| `--instrument` | 数据从哪来，写一句话。 |
| `--at` | 测量时间。不写就用当前时间；不带时区按本机时间。 |
| `--tags` | 额外标签，逗号分隔。`import` 标签总会加上。 |

Agent 用 MCP 工具 `lab_import`，参数同名（`waveforms`、`values`、`files`、`captured_at` 等）。

厂家 CSV 格式五花八门。导入后用 `benchgate lab query waveform` 看一眼通道和时间轴对不对；不对就调 `--time-scale` / `--sample-interval` 重新导入，原文件一直在附件里。

---

## 能仿真的板先建立基线

回填出来的记录没有输入哈希，`index status` 会一直显示「尚未验证」，直到真正跑过一次：

```bash
benchgate watch once --design pcb --profile charge_pump \
  --no-auto-capture --force --purpose "建立基线"
```

`--force` 是因为 `watch once` 没检测到 KiCad / blocks 变化时会直接跳过，什么都不记。`--no-auto-capture` 避免这次去动仪器。

装了 stop hook 之后，第一次在该仓库里结束一轮对话只会记下当前输入、不会跑仿真。下一次设计输入变化才会复核。所以上面这条基线命令仍然要跑一次。

---

## 平时怎么看

打开 `<design>/models/INDEX.md`。顶部是当前状态：各类最近一次结论、相对现在的原理图是否已过期、为什么还不算验证。有 manifest 的设计还会写明仿真配置来自项目里的 `models/sim_profiles.yaml`，还是本机共用文件（后者换机器无法复现）。下面按日期列出记录。

终端：

```bash
benchgate index status --design pcb          # 未验证时退出码 1
benchgate index list --design pcb --kind gate
benchgate index list --design pcb --text R51 --since 2026-09-01
```

跑任何设计类命令时加上 `--purpose "为什么跑"`，这句话写进这一行。Agent 调 MCP 工具时传 `purpose`，知道会话 ID 时再传 `chat_id`。

`models/index.jsonl` 和 `models/INDEX.md` 放在 `models/` 下，目的是进 git。报告目录经常被忽略，台账不要忽略。

确实要先提交、跳过检查：`git commit --no-verify`。

---

## 什么算「已验证」

`benchgate index check --design pcb` 和 pre-commit 用同一套判断：

- 有一条带输入哈希的 gate，且哈希等于当前设计输入。
- 这条 gate 的结论不是失败。
- 同一份输入上最近一次仿真也不是失败。

「输入」指原理图各页、`models/manifest.yaml`、`models/blocks.yaml` 与 `models/blocks/` 下的网表和 metrics、以及当时用的 `sim_profiles.yaml`。这些变了，旧的 pass 标成已过期。

gate 写着 pass、但没有任何 spec 或规则可判，状态是「未判定」，同样不算已验证。

---

## 自动复核做什么

设计输入相对上次带哈希的 gate 没变：什么都不跑。

变了：对该设计执行 `watch once --force`，并关掉自动采集和容差。profile 用安装时指定的，否则用台账里最近一次仿真的 profile。结论带 `purpose`「设计输入变化后自动复核（Cursor 回合结束）」。

复核失败时，hook 把失败摘要和 `INDEX.md` 路径送回 Agent。同一份输入不会反复触发；改文件或修结论之后才会再跑。

仿真可能要几十秒。hook 超时设为 15 分钟。一次对话里跟进最多两轮，避免失败时无限循环。

仪器相关的采集、扫频、热成像仍要显式调用。

---

## 已有记录

`benchgate index rebuild --design pcb` 把现有 Session 和磁盘上还在的报告补进台账。报告每次运行都会覆盖，更早的那次补不回来。回填行标「回填」，没有输入哈希，不充当当前结论。再跑一次不会重复追加。

`--dry-run` 只打印会补多少条。

---

## 限制

- 台账记的是「跑了什么、结论是什么、当时的设计长什么样」。为什么这么判断，靠 `--purpose` 和项目里的实验笔记，不靠聊天记录。
- Session 必须在 `<design>/models/captured/sessions/<id>/`。多嵌一层（例如 `sessions/sessions/<id>`）时，台账和 `lab query` 都看不到。
- 非默认输出目录的报告（例如对照实验的临时 manifest）会记上，但标成「非默认输出」，不代表这个设计的当前结论。
- 一个仓库里有多个设计时，stop hook 和 `--staged` 检查会逐个处理有改动的设计。
- `lab read`（万用表、转速表读数）只把数值打印出来，不存 Session，也不进台账。要留下来，用 `lab import --value` 再记一次。
- 现有硬件仓库的 `.gitignore` 大多忽略 `models/captured/`：完整波形和附件只留在本机，进 git 的是台账里的结论、关键数字和文件路径。要随仓库保存原始数据，需要自己调整 `.gitignore`。
- 一个仓库里既有做仿真的板、又有不做的板时，`--no-sim` 对整个仓库生效。没有 `models/manifest.yaml` 的板本来就不会被仿真、也不会被拦提交，通常不需要 `--no-sim`；它用于有 manifest 但仿真暂时跑不通的仓库。
