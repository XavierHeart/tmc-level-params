"""One picture of a batch of levels against the competitor difficulty range.

Top: each level of the batch, in input order, on a difficulty ruler from 0 %
(easiest competitor level) to 100 % (hardest), with the five tiers as bands;
the line is the difficulty curve of the batch. Below: the six score features,
one column per level: the competitor range of that level's tier (box 25-75 %,
whiskers 10-90 %, line = median) and the level's value as a red dot.

Drawn from model.json and level-params.json only; needs matplotlib.
"""
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
    ('motion', '运动载具（灰柱 = 本档竞品有运动的比例）', lambda r: 100.0 * bool(r['motion']),
     lambda r: 100.0 * bool(r['motion'])),
]
TIER_COLORS = ['#DCEAF7', '#CFE3D4', '#FBEFD0', '#F9DCC4', '#F4C7C7']
CJK_FONTS = ['/System/Library/Fonts/Hiragino Sans GB.ttc', '/System/Library/Fonts/STHeiti Medium.ttc',
             '/System/Library/Fonts/PingFang.ttc', 'C:/Windows/Fonts/msyh.ttc',
             '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']


def _setup():
    if not os.environ.get('MPLCONFIGDIR'):
        # Sandboxed agents often cannot write ~/.matplotlib; keep matplotlib's cache in a writable place.
        import tempfile
        os.environ['MPLCONFIGDIR'] = os.path.join(tempfile.gettempdir(), 'level-params-matplotlib')
        os.makedirs(os.environ['MPLCONFIGDIR'], exist_ok=True)
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
    return 100 * sum(x < v for x in values) / len(values) if values else None


def q(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))]


def positions(model, levels):
    """Rank of every level among all competitor levels and inside its tier (whole percent)."""
    scores = {id(r): tiers.score(model.tiers, tiers.real_features(r)) for r in model.levels}
    all_scores = list(scores.values())
    out = []
    for r in levels:
        out.append({'index': r['index'], 'theme': r['theme'], 'tier': r['tier'], 'score': r['score'],
                    'percentile_overall': round(percentile_in(all_scores, r['score'])),
                    'percentile_in_tier': round(percentile_in([scores[id(x)] for x in model.pool[r['tier']]],
                                                              r['score']))})
    return out


def draw(model, result, out):
    """model: generate.Model; result: a level-params.json dict. Returns the positions."""
    plt = _setup()
    levels = result['levels']
    pos = positions(model, levels)
    n = len(levels)
    xs = list(range(1, n + 1))
    labels = [f"{r['index']}. {r['theme']}·{r['tier']}档" for r in levels]
    width = min(max(11, 0.55 * n + 4), 40)
    fig = plt.figure(figsize=(width, 14), dpi=130)
    grid = fig.add_gridspec(5, 2, height_ratios=[1.3, 0.22, 1, 1, 1], hspace=0.55, wspace=0.15)

    ax = fig.add_subplot(grid[0, :])
    for i in range(5):
        hard = sum(r['difficulty'] != 'Normal' for r in model.pool[i + 1]) / len(model.pool[i + 1])
        ax.axhspan(20 * i, 20 * (i + 1), color=TIER_COLORS[i], zorder=0)
        ax.text(n + 0.62, 20 * i + 10, f'第{i + 1}档\n竞品 Hard/ExHard {100 * hard:.0f}%', va='center', ha='left',
                fontsize=9, color='#333')
    ys = [p['percentile_overall'] for p in pos]
    ax.plot(xs, ys, color='#C0392B', linewidth=1.5, zorder=2)
    ax.scatter(xs, ys, color='#C0392B', s=42, zorder=3)
    for x, p in zip(xs, pos):
        high = p['percentile_overall'] > 90
        ax.annotate(f"{p['percentile_overall']}%", (x, p['percentile_overall']), xytext=(0, -14 if high else 8),
                    textcoords='offset points', ha='center', fontsize=9, color='#C0392B')
    ax.set_xlim(0.4, n + 0.6)
    ax.set_ylim(0, 100)
    ax.set_yticks(range(0, 101, 20), [f'{v}%' for v in range(0, 101, 20)])
    ax.set_xticks(xs, labels, rotation=30 if n > 6 else 0, ha='right' if n > 6 else 'center', fontsize=9)
    ax.set_ylabel(f'难度排位（竞品 {len(model.levels)} 关中\n难度分低于它的比例）')
    ax.set_title(f'这一批 {n} 关的难度（红点 = 每一关在竞品整体中的排位；0% 最简单，100% 最难）', fontsize=13)
    ax.spines[['top', 'right']].set_visible(False)

    for k, (key, label, real_value, row_value) in enumerate(PANELS):
        ax = fig.add_subplot(grid[2 + k // 2, k % 2])
        ref = {t: [real_value(r) for r in model.pool[t]] for t in range(1, 6)}
        for x, r in zip(xs, levels):
            vals = ref[r['tier']]
            if key == 'motion':
                ax.bar(x, statistics.mean(vals), width=0.55, color='#C9D3DD', zorder=1)
            else:
                p10, p25, p50, p75, p90 = (q(vals, p) for p in (0.1, 0.25, 0.5, 0.75, 0.9))
                ax.plot([x, x], [p10, p90], color='#7F8C8D', linewidth=1.1, zorder=1)
                ax.add_patch(plt.Rectangle((x - 0.25, p25), 0.5, p75 - p25, facecolor='#C9D3DD',
                                           edgecolor='#7F8C8D', zorder=2))
                ax.plot([x - 0.25, x + 0.25], [p50, p50], color='#34495E', linewidth=1.4, zorder=3)
            v = row_value(r)
            ax.scatter([x], [v], color='#C0392B', s=30, zorder=5)
            text = ('有' if v else '无') if key == 'motion' else (f'{v:.0f}' if v >= 10 or v == int(v) else f'{v:.1f}')
            ax.annotate(text, (x, v), xytext=(6, 0), textcoords='offset points', fontsize=8, color='#C0392B',
                        va='center')
        ax.set_xlim(0.4, n + 0.6)
        ax.set_xticks(xs, [str(r['index']) for r in levels], fontsize=8)
        ax.set_title(label, fontsize=11)
        ax.spines[['top', 'right']].set_visible(False)
    fig.text(0.5, 0.04, '下方 6 图：横轴是关卡序号。灰色箱体 = 这一关所在档的竞品关卡 25%–75%，竖线 = 10%–90%，'
                        '横线 = 中位数；红点 = 这一关的值', ha='center', fontsize=10, color='#555')
    fig.savefig(out, bbox_inches='tight')
    plt.close(fig)
    return pos
