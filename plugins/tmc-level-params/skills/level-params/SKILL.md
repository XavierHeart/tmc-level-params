---
name: level-params
description: 策划用的关卡难度参数生成器（harness 流程）。策划给出关卡区间和 Normal/Hard/ExHard 三类难度的关数，harness 按竞品主线数据为每一关生成并验算难度参数：联合形态、物件总数、物件种类数、建筑/道具/载具/角色四类拆分、每种物件数量、目标、叠放、运动载具、时长；输出 json/csv/xlsx。适用于排关、给关卡生产准备参数；不生成图片，不摆放关卡。
---

# 关卡难度参数生成

本技能给策划用。输入一段关卡区间和三类难度的关数，输出这一段每一关的难度参数。物件用占位名（建筑1、道具3），不绑定主题；关卡生产时再把占位名换成具体主题的物件。

分工：

- **harness**（`scripts/level-params-harness`）负责生成和验算。所有数字都由它算，agent 不估、不改。
- **agent** 负责向策划收集输入、运行各步，并汇报结果。

所有统计数值来自 `scripts/model.json`（由竞品主线数据学习得到），规则来自《关卡参数生成规则.xlsx》（统一生成规则）。生成方法和统计口径见 [references/method.md](references/method.md)。

## 需要策划给出的输入

| 输入 | 必填 | 说明 |
|---|---|---|
| 关卡区间 | 是 | 起止关卡号，例如 101–150 |
| Normal / Hard / ExHard 关数 | 是 | 三者之和必须等于区间关数 |
| 随机种子 | 否 | 缺省时由输入派生；同一输入和种子得到完全相同的结果 |
| 叠放率、运动率 | 否 | 覆盖关段的默认比例（0–1），对应规则表的"机制偏好" |

缺少必填项时只问缺的那一项，不猜。三类关数之和不等于区间关数时，把差值告诉策划，请策划改。

## 流程

`$SKILL_DIR` 是本 SKILL.md 所在目录，`H="$SKILL_DIR/scripts/level-params-harness"`。运行目录缺省用当前工作目录下的 `level-params-<起>-<止>/`。

1. 建立运行目录：

   ```bash
   "$H" init --run <运行目录> --start <起> --end <止> --normal <N> --hard <H> --exhard <E> \
       [--seed <种子>] [--stack-rate <0-1>] [--motion-rate <0-1>]
   ```

   输入有错时它报错并停止，把错误原文告诉策划。

2. 生成：`"$H" generate --run <运行目录>`。它生成参数、逐关和整批验算，写出 `level-params.json`、`.csv`、`.xlsx`。返回 1 表示批次验算失败，把失败项告诉策划，不说成通过。

3. 策划问到某一关时：`"$H" level --run <运行目录> --level <关卡号>`，显示这一关的参数、依据（取自哪个竞品关卡）和验算结果。

4. 向策划汇报：
   - 三个文件的路径；
   - 每类难度的关数、物件总数和种类数的范围、叠放关数、运动载具关数（`generate` 的输出里有）；
   - 批次验算结果。

   不在对话里贴整张参数表，策划看 xlsx。

其他命令：`check`（复查一份 level-params.json，策划手改过文件后用它）、`status`（当前状态）、`note`（记录决定）、`journal`（查看操作记录）。

## 每关参数

| 字段 | 含义 |
|---|---|
| `level`、`difficulty` | 关卡号；难度 Normal / Hard / ExHard |
| `form`、`form_description` | 联合形态（竞品关卡聚类出的 F1–F5）和说明 |
| `T`、`U` | 物件总数（含装饰物，3 的倍数）、物件种类数 |
| `categories` | 建筑、道具、载具、角色各几种、共几个 |
| `per_kind` | 每种物件的数量（占位名，每种是 3 的倍数） |
| `targets` | 目标：哪几种、每种几个；目标数量等于这种物件在关卡里的全部数量 |
| `stacking` | 是否叠放、几层、哪几种物件叠在一起、各几套 |
| `motion` | 是否有运动载具 |
| `duration_s` | 时长（秒） |
| `basis`、`variation` | 依据：T/U、四类拆分、叠放结构分别取自哪个竞品关卡，以及 T/U 的变化量 |
| `checks` | 单关验算结果 |

## 运行目录

| 文件 | 内容 |
|---|---|
| `run.json` | 输入、竞品统计数据的 sha256、当前参数的 sha256 |
| `level-params.json` | 完整结果，交给关卡生产的就是这个文件 |
| `level-params.csv` / `.xlsx` | 每关一行，中文列名。xlsx 需要 `xlsxwriter`，没有安装时只跳过 xlsx，并提示 `pip install xlsxwriter` |
| `journal.jsonl` | 每一步操作 |

## 更新竞品数据

竞品数据更新后重新学习一次，生成新的 `model.json`：

```bash
python3 "$SKILL_DIR/scripts/learn.py" --data "<tmc资源包/数据>"
```

`model.json` 变了以后，同一输入和种子的结果也会变。`run.json` 和参数文件里都记录了 `model_sha256`。

## 范围和限制

- 只生成参数，不生成图片，不摆放关卡，不判断主题。
- 难度位置按规则排位，不是从竞品顺序学来的。
- 参数只是难度的事前估计。
- 物件总数包含装饰物，和规则表的统计一致。

## 测试

```bash
python3 "$SKILL_DIR/scripts/tests/test_level_params.py"
```
