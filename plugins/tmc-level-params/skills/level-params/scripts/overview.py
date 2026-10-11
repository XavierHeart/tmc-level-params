"""One picture of a theme's parameters against the whole competitor difficulty range.

Top: a difficulty ruler from 0 % (easiest competitor level) to 100 % (hardest),
the five tiers as equal bands, and where each of this theme's five parameter
sets lands (rank among all competitor levels and inside its tier). Bottom: for each of the six score features, the competitor
range of every tier (box 25-75 %, whiskers 10-90 %, line = median) and this
theme's value as a red dot.

Drawn from model.json and level-params.json only; needs matplotlib.
"""
import math
import os
import statistics

import tiers

PANELS = [
    ('T', '物件总数', lambda r: r['T'], lambda r: r['T']),
    ('U', '物件种类数', lambda r: r['U'], lambda r: r['U']),
    ('target_kinds', '目标种类数', lambda r: len(r['targets']), lambda r: len(r['targets'])),
    ('duration_s', '时长（秒）', lambda r: r['duration_s'], lambda r: r['duration_s']),
    ('stack_share', '叠放占比（%）', lambda r: 100 * tiers.real_features(r)['stack_share'],
     lambda r: 100 * tiers.row_features(r)['stack_share']),
    ('motion', '有运动载具的关卡（%）', lambda r: 100.0 * bool(r['motion']), lambda r: 100.0 * bool(r['motion'])),
]
TIER_COLORS = ['#DCEAF7', '#CFE3D4', '#FBEFD0', '#F9DCC4', '#F4C7C7']
CJK_FONTS = ['/System/Library/Fonts/Hiragino Sans GB.ttc', '/System/Library/Fonts/STHeiti Medium.ttc',
             '/System/Library/Fonts/PingFang.ttc', 'C:/Windows/Fonts/msyh.ttc',
             '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']


def _setup():
    try:
        import matplotlib
    except ImportError:
        raise SystemExit('error: the overview picture needs matplotlib: python3 -m pip install matplotlib')
    matplotlib.use('Agg')
    import logging
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
    for f in CJK_FONTS:
        if os.path.exists(f):
            font_manager.fontManager.addfont(f)
            plt.rcParams['font.family'] = font_manager.FontProperties(fname=f).get_name()
            break
    plt.rcParams['axes.unicode_minus'] = False
    return plt


def percentile_in(values, v):
    values = sorted(values)
    return 100 * sum(x < v for x in values) / len(values) if values else None


def q(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))]


def draw(model, result, out):
    """model: generate.Model; result: a level-params.json dict. Returns per-tier positions."""
    plt = _setup()
    real = model.levels
    scores = {id(r): tiers.score(model.tiers, tiers.real_features(r)) for r in real}
    rows = result['tiers']

    fig = plt.figure(figsize=(13, 10), dpi=150)
    grid = fig.add_gridspec(3, 3, height_ratios=[0.8, 1, 1], hspace=0.6, wspace=0.28)
    ax = fig.add_subplot(grid[0, :])
    all_scores = list(scores.values())
    for i in range(5):
        pool = model.pool[i + 1]
        hard = sum(r['difficulty'] != 'Normal' for r in pool) / len(pool)
        ax.axvspan(20 * i, 20 * (i + 1), color=TIER_COLORS[i], zorder=0)
        ax.text(20 * i + 10, 0.08, f'第{i + 1}档\n竞品 Hard/ExHard {100 * hard:.0f}%', ha='center', va='bottom',
                fontsize=10, color='#333')
    positions = []
    for r in rows:
        overall = round(percentile_in(all_scores, r['score']))
        within = round(percentile_in([scores[id(x)] for x in model.pool[r['tier']]], r['score']))
        positions.append({'tier': r['tier'], 'score': r['score'], 'percentile_overall': overall,
                          'percentile_in_tier': within})
        ax.plot([overall, overall], [0.45, 1.0], color='#C0392B', linewidth=2, zorder=3)
        ax.scatter([overall], [0.45], color='#C0392B', s=40, zorder=4)
        ax.annotate(f"第{r['tier']}档参数\n全体 {overall:.0f}%\n档内 {within:.0f}%", (overall, 0.72), ha='center',
                    va='center', fontsize=9, color='#C0392B', zorder=5,
                    bbox={'boxstyle': 'round,pad=0.25', 'fc': 'white', 'ec': '#C0392B', 'lw': 0.8})
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 1.05)
    ax.set_yticks([])
    ax.set_xticks(range(0, 101, 10), [f'{x}%' for x in range(0, 101, 10)])
    ax.set_xlabel(f'难度排位：竞品 {len(real)} 关中难度分低于它的比例（0% 最简单，100% 最难）')
    ax.set_title(f"「{result['theme']}」5 组参数在竞品整体难度中的位置", fontsize=13)
    ax.spines[['top', 'right', 'left']].set_visible(False)

    for n, (key, label, real_value, row_value) in enumerate(PANELS):
        ax = fig.add_subplot(grid[1 + n // 3, n % 3])
        for t in range(1, 6):
            vals = [real_value(r) for r in model.pool[t]]
            if key == 'motion':
                share = statistics.mean(vals)
                ax.bar(t, share, width=0.5, color='#C9D3DD', zorder=1)
                continue
            p10, p25, p50, p75, p90 = (q(vals, p) for p in (0.1, 0.25, 0.5, 0.75, 0.9))
            ax.plot([t, t], [p10, p90], color='#7F8C8D', linewidth=1.2, zorder=1)
            ax.add_patch(plt.Rectangle((t - 0.22, p25), 0.44, p75 - p25, facecolor='#C9D3DD',
                                       edgecolor='#7F8C8D', zorder=2))
            ax.plot([t - 0.22, t + 0.22], [p50, p50], color='#34495E', linewidth=1.5, zorder=3)
        for r in rows:
            v = row_value(r)
            ax.scatter([r['tier']], [v], color='#C0392B', s=36, zorder=5)
            text = ('有' if v else '无') if key == 'motion' else (f'{v:.0f}' if v >= 10 or v == int(v) else f'{v:.1f}')
            ax.annotate(text, (r['tier'], v), xytext=(7, 0), textcoords='offset points', fontsize=8,
                        color='#C0392B', va='center')
        ax.set_xticks(range(1, 6), [str(t) for t in range(1, 6)])
        ax.set_xlim(0.4, 5.6)
        ax.set_title(label + ('（灰柱 = 本档竞品比例）' if key == 'motion' else ''), fontsize=11)
        ax.spines[['top', 'right']].set_visible(False)
    fig.text(0.5, 0.015, '下方 6 图：横轴是档位。灰色箱体 = 本档竞品关卡 25%–75%，竖线 = 10%–90%，横线 = 中位数；'
                         '红点 = 本主题的值', ha='center', fontsize=10, color='#555')
    fig.savefig(out, bbox_inches='tight')
    plt.close(fig)
    return positions
