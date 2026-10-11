"""Figures for the tier write-up, drawn from model.json (real competitor data only)."""
import json, math, statistics as st, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

HERE = Path(__file__).resolve().parent
S = HERE.parents[1] / 'plugins/tmc-level-params/skills/level-params/scripts'
sys.path.insert(0, str(S))
import tiers  # noqa: E402

for f in ('/System/Library/Fonts/Hiragino Sans GB.ttc', '/System/Library/Fonts/STHeiti Medium.ttc'):
    if Path(f).exists():
        font_manager.fontManager.addfont(f)
        plt.rcParams['font.family'] = font_manager.FontProperties(fname=f).get_name()
        break
plt.rcParams['axes.unicode_minus'] = False
m = json.loads((S / 'model.json').read_text())
T = m['tiers']
L = m['levels']
for r in L:
    r['_s'] = tiers.score(T, tiers.real_features(r))
    r['_t'] = tiers.tier_of(T['cuts'], r['_s'])
COL = {'Normal': '#8FB3D9', 'Hard': '#F2A65A', 'ExHard': '#D64545'}
LAB = ('Normal', 'Hard', 'ExHard')

# 1. score distribution by label, with the tier cuts
fig, ax = plt.subplots(figsize=(10, 4.2), dpi=150)
bins = [x / 4 for x in range(-24, 21)]
for lab in LAB:
    v = [r['_s'] for r in L if r['difficulty'] == lab]
    ax.hist(v, bins=bins, density=True, histtype='stepfilled', alpha=0.45, color=COL[lab], label=f'{lab}（{len(v)} 关）')
    ax.hist(v, bins=bins, density=True, histtype='step', color=COL[lab], linewidth=1.5)
for c in T['cuts']:
    ax.axvline(c, color='#333', linestyle='--', linewidth=1)
edges = [bins[0]] + T['cuts'] + [bins[-1]]
for i in range(5):
    ax.text((edges[i] + edges[i + 1]) / 2, ax.get_ylim()[1] * 0.93, f'第{i + 1}档', ha='center', fontsize=11, color='#333')
ax.set_xlabel('难度分（越高越像竞品的 Hard/ExHard）')
ax.set_ylabel('占本标签关卡的比例')
ax.set_title('竞品 2998 关的难度分分布，虚线是 5 档的切点')
ax.legend(loc='upper left', frameon=False)
fig.tight_layout(); fig.savefig(HERE / 'fig1-score-distribution.png'); plt.close(fig)

# 2. composition of each tier
fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
bottom = [0] * 5
for lab in LAB:
    cnt = [sum(1 for r in L if r['_t'] == t and r['difficulty'] == lab) for t in range(1, 6)]
    tot = [sum(1 for r in L if r['_t'] == t) for t in range(1, 6)]
    pct = [100 * c / n for c, n in zip(cnt, tot)]
    ax.bar(range(1, 6), pct, bottom=bottom, color=COL[lab], label=lab, width=0.6)
    for i, (p, c) in enumerate(zip(pct, cnt)):
        if p >= 4:
            ax.text(i + 1, bottom[i] + p / 2, f'{c}', ha='center', va='center', fontsize=9)
    bottom = [b + p for b, p in zip(bottom, pct)]
ax.set_xticks(range(1, 6), [f'第{t}档' for t in range(1, 6)])
ax.set_ylabel('占本档关卡的比例（%）')
ax.set_title('每档约 600 关，竞品标签的构成（柱内数字是关数）')
ax.legend(loc='upper left', bbox_to_anchor=(1, 1), frameon=False)
fig.tight_layout(); fig.savefig(HERE / 'fig2-tier-composition.png'); plt.close(fig)

# 3. weights
names = {k: lab for k, lab, _ in tiers.FEATURES}
w = sorted(T['weights'].items(), key=lambda kv: kv[1])
fig, ax = plt.subplots(figsize=(8, 3.6), dpi=150)
ax.barh([names[k] for k, _ in w], [v for _, v in w], color=['#D64545' if v > 0 else '#5B8DB8' for _, v in w])
for i, (_, v) in enumerate(w):
    ax.text(v + (0.02 if v > 0 else -0.02), i, f'{v:+.2f}', va='center', ha='left' if v > 0 else 'right', fontsize=9)
ax.axvline(0, color='#333', linewidth=0.8)
ax.set_xlim(-1.1, 1.1)
ax.set_xlabel('权重（特征已标准化；正数：越大越难，负数：越大越简单）')
ax.set_title('难度分里各参数的权重')
fig.tight_layout(); fig.savefig(HERE / 'fig3-weights.png'); plt.close(fig)

# 4. parameters by tier
def med(f, t):
    return st.median(f(r) for r in L if r['_t'] == t)
def mean(f, t):
    return st.mean(f(r) for r in L if r['_t'] == t)
panels = [('物件总数 T（中位数）', lambda t: med(lambda r: r['T'], t), '{:.0f}'),
          ('物件种类数 U（中位数）', lambda t: med(lambda r: r['U'], t), '{:.0f}'),
          ('目标种类数（平均）', lambda t: mean(lambda r: len(r['targets']), t), '{:.1f}'),
          ('时长 秒（中位数）', lambda t: med(lambda r: r['duration_s'], t), '{:.0f}'),
          ('有叠放的关卡（%）', lambda t: 100 * mean(lambda r: bool(r['stack']), t), '{:.0f}'),
          ('有运动载具的关卡（%）', lambda t: 100 * mean(lambda r: bool(r['motion']), t), '{:.0f}')]
fig, axes = plt.subplots(2, 3, figsize=(11, 5.6), dpi=150)
for ax, (title, f, fmt) in zip(axes.flat, panels):
    v = [f(t) for t in range(1, 6)]
    ax.bar(range(1, 6), v, color='#7A9CC6', width=0.6)
    for i, x in enumerate(v):
        ax.text(i + 1, x, fmt.format(x), ha='center', va='bottom', fontsize=9)
    ax.set_xticks(range(1, 6), [str(t) for t in range(1, 6)])
    ax.set_title(title, fontsize=11)
    ax.set_ylim(0, max(v) * 1.18)
    ax.spines[['top', 'right']].set_visible(False)
fig.supxlabel('档位（1 最简单，5 最难）')
fig.suptitle('各档竞品关卡的参数', fontsize=13)
fig.tight_layout(); fig.savefig(HERE / 'fig4-params-by-tier.png'); plt.close(fig)

summary = {t: {'n': sum(1 for r in L if r['_t'] == t)} | {p[0]: round(p[1](t), 2) for p in panels} for t in range(1, 6)}
print(json.dumps(summary, ensure_ascii=False, indent=0))
