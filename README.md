# TMC 关卡难度参数生成器

给策划用的 Codex 插件，按 harness 流程运行。输入一段关卡区间和三类难度（Normal、Hard、ExHard）的关数，为每一关生成难度参数：

- 联合形态；
- 物件总数、物件种类数；
- 建筑、道具、载具、角色四类各几种、各多少个；
- 目标（几种、每种几个）；
- 是否叠放、叠放结构；
- 是否有运动载具；
- 时长。

harness 还会给每一关算 8 项难度压力和综合难度，与竞品同编号关卡对比，画出曲线图并列出需要复核的关卡。agent 查看图后重抽或保留（写明理由），`finalize` 通过后才输出最终文件。

物件用占位名（建筑1、道具3），不绑定主题。关卡生产（`tmc-level-designer-plugin`）读取输出的 `level-params.json`，再把占位名换成具体主题的物件。

## 安装

```bash
git clone https://github.com/XavierHeart/tmc-level-params.git
cd tmc-level-params
./install.sh
```

安装后必须新建 Codex 任务。已有任务不会加载新安装的技能。

运行环境需要 Python 3 和 Pillow（画图）。生成 xlsx 需要 `xlsxwriter`；没有安装时只跳过 xlsx。安装依赖：`python3 -m pip install -r plugins/tmc-level-params/requirements.txt`。

## 使用

在 Codex 里：

```text
使用 $level-params，生成 101–150 关：Normal 35 关，Hard 10 关，ExHard 5 关。
```

也可以直接运行 harness：

```bash
H=plugins/tmc-level-params/skills/level-params/scripts/level-params-harness
$H init --run out/101-150 --start 101 --end 150 --normal 35 --hard 10 --exhard 5
$H generate --run out/101-150          # 参数、打分、曲线图、复核项
$H level --run out/101-150 --level 120 # 看单关
$H reroll --run out/101-150 --level 120 --reason "尖峰"
$H review-template --run out/101-150   # 填写 review.json
$H finalize --run out/101-150          # 通过后写到 out/101-150/final/
```

运行目录、复核规则和执行流程见 [SKILL.md](plugins/tmc-level-params/skills/level-params/SKILL.md)。生成方法、难度压力和复核项的定义见 [references/method.md](plugins/tmc-level-params/skills/level-params/references/method.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `plugins/tmc-level-params/skills/level-params/SKILL.md` | 技能说明 |
| `.../scripts/level-params-harness`、`params_harness.py` | harness 命令入口 |
| `.../scripts/generate.py` | 参数生成和验算 |
| `.../scripts/learn.py` | 从竞品数据学习，生成 `model.json` |
| `.../scripts/pressure.py` | 8 项难度压力、打分和曲线指标 |
| `.../scripts/charts.py` | 曲线图（PNG） |
| `.../scripts/model.json` | 学习结果（竞品原始数据不放进仓库） |
| `.../scripts/tests/` | 测试 |

## 测试

```bash
python3 plugins/tmc-level-params/skills/level-params/scripts/tests/test_level_params.py
python3 plugins/tmc-level-params/skills/level-params/scripts/tests/test_harness.py
```

## 发布新版本

1. 修改代码并运行测试。
2. 修改 `plugins/tmc-level-params/.codex-plugin/plugin.json` 的 `version`，格式为 `x.y.z+codex.<UTC 时间戳>`。
3. 提交并推送。
4. 重新运行 `./install.sh`，然后新建 Codex 任务。
