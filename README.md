# TMC 关卡难度参数生成器

给策划用的 Codex 插件，按 harness 流程运行。策划只输入主题名，插件为这个主题在 5 个难度档位上各生成一组参数：

- 联合形态；
- 物件总数、物件种类数；
- 建筑、道具、载具、角色四类各几种、各多少个，每种物件的数量；
- 目标（几种、每种几个）；
- 是否叠放、叠放结构；
- 是否有运动载具；
- 时长。

同时给出 5 档共用的素材清单（四类各需要几种）。难度档位从竞品数据学出：用竞品自己的 Hard/ExHard 标签训练难度分，再按难度分把竞品关卡等分成 5 档。

物件用占位名（建筑1、道具3）。关卡生产（`tmc-level-designer-plugin`）读取输出的 `level-params.json`，再把占位名换成具体主题的物件。

## 安装

```bash
git clone https://github.com/XavierHeart/tmc-level-params.git
cd tmc-level-params
./install.sh
```

安装后必须新建 Codex 任务。已有任务不会加载新安装的技能。

运行环境需要 Python 3。生成 xlsx 需要 `xlsxwriter`，画总览图需要 `matplotlib`；没有安装时只跳过这两项。安装依赖：`python3 -m pip install -r plugins/tmc-level-params/requirements.txt`。

## 使用

在 Codex 里：

```text
使用 $level-params，给"月球基地"主题生成各难度档位的参数。
```

也可以直接运行 harness：

```bash
H=plugins/tmc-level-params/skills/level-params/scripts/level-params-harness
$H init --run out/月球基地 --theme 月球基地
$H generate --run out/月球基地          # 5 档参数 + 素材清单，验算，写出 json/csv/xlsx
$H tier --run out/月球基地 --tier 5     # 看一档
$H overview --run out/月球基地          # 总览图：5 组参数在竞品整体难度中的位置
```

运行目录和执行流程见 [SKILL.md](plugins/tmc-level-params/skills/level-params/SKILL.md)。难度档位的学习方法、生成方法和统计口径见 [references/method.md](plugins/tmc-level-params/skills/level-params/references/method.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `plugins/tmc-level-params/skills/level-params/SKILL.md` | 技能说明 |
| `.../scripts/level-params-harness`、`params_harness.py` | harness 命令入口 |
| `.../scripts/generate.py` | 参数生成和验算 |
| `.../scripts/learn.py` | 从竞品数据学习，生成 `model.json` |
| `.../scripts/tiers.py` | 难度分模型和 5 档切分 |
| `.../scripts/overview.py` | 总览图 |
| `.../scripts/model.json` | 学习结果（竞品原始数据不放进仓库） |
| `.../scripts/tests/` | 测试 |

## 测试

```bash
python3 plugins/tmc-level-params/skills/level-params/scripts/tests/test_level_params.py
```

## 发布新版本

1. 修改代码并运行测试。
2. 修改 `plugins/tmc-level-params/.codex-plugin/plugin.json` 的 `version`，格式为 `x.y.z+codex.<UTC 时间戳>`。
3. 提交并推送。
4. 重新运行 `./install.sh`，然后新建 Codex 任务。
