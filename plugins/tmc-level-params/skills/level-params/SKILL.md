---
name: level-params
description: 策划用的关卡难度参数生成器（harness 流程）。策划给出关卡区间和 Normal/Hard/ExHard 三类难度的关数，harness 按竞品主线数据为每一关生成难度参数：联合形态、物件总数、物件种类数、建筑/道具/载具/角色四类拆分、目标、叠放、运动载具、时长；计算 8 项难度压力和综合难度曲线，与竞品同编号关卡对比，画出曲线图并列出需要复核的关卡。agent 查看图和复核项，重抽或保留并写明理由，finalize 通过后输出 json/csv/xlsx。适用于排关、定难度曲线、给关卡生产准备参数；不生成图片，不摆放关卡。
---

# 关卡难度参数生成

本技能给策划用。输入一段关卡区间和三类难度的关数，输出这一段每一关的难度参数。物件用占位名（建筑1、道具3），不绑定主题；关卡生产时再把占位名换成具体主题的物件。

分工：

- **harness**（`scripts/level-params-harness`）负责生成、验算、打分、画图、列出复核项和最终把关。所有数字都由它算，不由 agent 估。
- **agent** 负责查看曲线图，判断复核项是重抽还是保留，写明理由，并向策划汇报。

所有统计数值来自 `scripts/model.json`（由竞品主线数据学习得到），规则来自《关卡参数生成规则.xlsx》（统一生成规则）。生成方法、压力定义和复核规则见 [references/method.md](references/method.md)。

## 需要策划给出的输入

| 输入 | 必填 | 说明 |
|---|---|---|
| 关卡区间 | 是 | 起止关卡号，例如 101–150 |
| Normal / Hard / ExHard 关数 | 是 | 三者之和必须等于区间关数 |
| 随机种子 | 否 | 缺省时由输入派生；同一输入、种子和重抽记录得到完全相同的结果 |
| 叠放率、运动率 | 否 | 覆盖关段的默认比例（0–1），对应规则表的"机制偏好" |
| 综合难度权重 | 否 | 8 项压力的权重，缺省 N20 K15 G15 P15 S10 M5 D10 C10 |

缺少必填项时只问缺的那一项，不猜。三类关数之和不等于区间关数时，把差值告诉策划，请策划改。

## 流程

`$SKILL_DIR` 是本 SKILL.md 所在目录，`H="$SKILL_DIR/scripts/level-params-harness"`。运行目录缺省用当前工作目录下的 `level-params-<起>-<止>/`。

1. 建立运行目录：

   ```bash
   "$H" init --run <运行目录> --start <起> --end <止> --normal <N> --hard <H> --exhard <E> \
       [--seed <种子>] [--stack-rate <0-1>] [--motion-rate <0-1>]
   ```

   策划给了权重时，接着运行 `"$H" weights --run <运行目录> N=20 K=15 ...`。

2. 生成：`"$H" generate --run <运行目录>`。它写出参数文件、`scores.json`、两张图和 `review/summary.md`，并在终端列出复核项。返回 1 表示验算失败；验算失败时不进入复核，把失败项告诉策划。

3. 查看：必须实际打开 `review/curve.png` 和 `review/pressures.png`，并读 `review/summary.md`。只读终端输出不算查看。

4. 处理复核项。每一项二选一：
   - **重抽**：`"$H" reroll --run <运行目录> --level <关卡号> --reason "<原因>"`。只换这一关的抽样，其他关保留，但这一关之后的关卡可能随形态连续规则一起变化。重抽后重新查看图和复核项。重抽结果更差时用 `--undo` 退回。
   - **保留**：在 review.json 里写 `keep` 和理由。理由要具体，例如"竞品 140–145 段也有同幅度的尖峰"，不写"可以接受"。
   
   用 `"$H" level --run <运行目录> --level <关卡号>` 看单关的参数、依据（取自哪个竞品关卡）、8 项压力和竞品对比。

   预算：每关最多重抽 3 次，整批最多重抽 10 次。用完预算仍有复核项时，保留并在理由里写明，向策划汇报，不无限重抽。

5. 写复核记录：`"$H" review-template --run <运行目录>` 生成 `review.json`。看过两张图后把 `charts_viewed` 改为 `true`，逐项填 `decision: "keep"` 和 `reason`。参数或权重变了以后，旧的 review.json 作废，要重新运行 review-template；没有变化的复核项会保留原来的决定。

6. 最终把关：`"$H" finalize --run <运行目录>`。它重新验算，并检查 review.json 是否对应当前参数和权重、是否每一项都有决定和理由。全部通过才把结果复制到 `<运行目录>/final/`；不通过时按它列出的问题处理。

7. 向策划汇报：
   - `final/` 下的文件路径；
   - 每类难度的关数、物件总数和种类数的范围、叠放关数、运动载具关数（取自 `summary.md`）；
   - 曲线指标：σ、相邻关平均跳变、三类难度均分，以及竞品对应值；
   - 重抽了哪些关、为什么；保留了哪些复核项、理由是什么；
   - `curve.png` 的路径，请策划自己看一眼。

   不在对话里贴整张参数表，策划看 xlsx。finalize 没有通过时，不说成已完成。

其他命令：`status`（当前状态和下一步）、`check`（复查一份 level-params.json）、`note`（记录决定）、`journal`（查看操作记录）。

## 运行目录

| 文件 | 内容 |
|---|---|
| `run.json` | 输入、权重、重抽记录、当前参数的 sha256 |
| `level-params.json` / `.csv` / `.xlsx` | 当前参数。xlsx 需要 `xlsxwriter`，没有安装时只跳过 xlsx |
| `scores.json` | 每关 8 项压力分、综合分、竞品同编号关卡的分、复核项、曲线指标 |
| `review/curve.png` | 综合难度曲线：生成（红）、竞品同编号（灰）、5 关滑动中位（黑虚线）、复核关（橙竖线）、Hard/ExHard 底色 |
| `review/pressures.png` | 8 项压力的分项曲线，生成与竞品对比 |
| `review/summary.md` | 验算、分布、曲线指标、复核项 |
| `review.json` | agent 的复核记录 |
| `journal.jsonl` | 每一步操作和原因 |
| `final/` | finalize 通过后的交付：参数三件套（json 里加了 `harness` 字段，记录权重、重抽和复核决定）、两张图、摘要 |

交给关卡生产的是 `final/level-params.json`。

## 更新竞品数据

竞品数据更新后重新学习一次，生成新的 `model.json`：

```bash
python3 "$SKILL_DIR/scripts/learn.py" --data "<tmc资源包/数据>"
```

`model.json` 变了以后，同一输入和种子的结果也会变。`run.json` 和参数文件里都记录了 `model_sha256`。

## 范围和限制

- 只生成参数，不生成图片，不摆放关卡，不判断主题。
- 难度位置按规则排位，不是从竞品顺序学来的。
- 参数只是难度的事前估计。D 摆放密度、C 拥挤度、M 运动载具三项是按关段预估的，摆放后才有实测值。
- 复核项是和竞品统计比较得出的提示，不代表关卡一定有问题；保留的理由由 agent 判断并写明。
- 物件总数包含装饰物，和规则表的统计一致。

## 测试

```bash
python3 "$SKILL_DIR/scripts/tests/test_level_params.py"
python3 "$SKILL_DIR/scripts/tests/test_harness.py"
```
