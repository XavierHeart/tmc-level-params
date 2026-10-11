"""Difficulty tiers learned from the competitor's own difficulty labels.

A logistic model learns, from six level parameters, how likely a real level is
labelled Hard or ExHard. Its score (the log-odds) ranks every real level from
easy to hard; the 20/40/60/80 % quantiles of that score cut the levels into
five tiers of equal size. Generated parameters are scored with the same model,
so "tier 3" means the same thing for real and generated levels.

Standard library only; deterministic.
"""
import collections
import math
import random

FEATURES = [
    # key, label, how it is computed
    ('log_T', '物件总数（取对数）', 'ln(T)'),
    ('U', '物件种类数', 'U'),
    ('target_kinds', '目标种类数', '目标的种类数'),
    ('duration_s', '时长（秒）', '关卡时长'),
    ('stack_share', '叠放占比', '叠在上层的物件数 ÷ T'),
    ('motion', '有运动载具', '有为 1，没有为 0'),
]
KEYS = [f[0] for f in FEATURES]
TIERS = 5
L2 = 1e-3
ITERATIONS = 400
RATE = 0.5
FOLDS = 5
CV_SEED = 7


def real_features(rec):
    """Features of a real level record from model.json."""
    riders = sum(c['sets'] * (c['size'] - 1) for c in rec['stack'])
    return {'log_T': math.log(rec['T']), 'U': rec['U'], 'target_kinds': len(rec['targets']),
            'duration_s': rec['duration_s'], 'stack_share': riders / rec['T'], 'motion': float(bool(rec['motion']))}


def row_features(row):
    """Features of a generated parameter row; same definitions as real_features."""
    s = row['stacking']
    riders = sum(c['sets'] * (s['layers'] - 1) for c in s['combos']) if s['on'] else 0
    return {'log_T': math.log(row['T']), 'U': row['U'], 'target_kinds': len(row['targets']),
            'duration_s': row['duration_s'], 'stack_share': riders / row['T'], 'motion': float(bool(row['motion']))}


def is_hard(label):
    return int(label != 'Normal')


def fit(rows, ys):
    """Class-balanced L2 logistic regression on standardized features, plain gradient descent."""
    mu = {k: sum(x[k] for x in rows) / len(rows) for k in KEYS}
    sd = {k: math.sqrt(sum((x[k] - mu[k]) ** 2 for x in rows) / len(rows)) or 1.0 for k in KEYS}
    z = [[(x[k] - mu[k]) / sd[k] for k in KEYS] for x in rows]
    n, pos = len(ys), sum(ys)
    w_pos, w_neg = n / (2 * pos), n / (2 * (n - pos))
    w, b = [0.0] * len(KEYS), 0.0
    for _ in range(ITERATIONS):
        gw, gb = [0.0] * len(KEYS), 0.0
        for zi, y in zip(z, ys):
            p = 1 / (1 + math.exp(-(b + sum(a * c for a, c in zip(w, zi)))))
            e = (p - y) * (w_pos if y else w_neg)
            gb += e
            for j, c in enumerate(zi):
                gw[j] += e * c
        b -= RATE * gb / n
        w = [a - RATE * (g / n + L2 * a) for a, g in zip(w, gw)]
    return {'features': KEYS, 'mean': mu, 'std': sd, 'weights': dict(zip(KEYS, w)), 'bias': b}


def score(model, feats):
    """Log-odds that a level with these features is labelled Hard or ExHard."""
    return model['bias'] + sum(model['weights'][k] * (feats[k] - model['mean'][k]) / model['std'][k]
                               for k in model['features'])


def tier_of(cuts, value):
    return 1 + sum(value >= c for c in cuts)


def auc(scores, labels):
    """Probability that a random positive scores above a random negative (ties count half)."""
    pairs = sorted(zip(scores, labels))
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg = (i + j + 1) / 2
        rank_sum += avg * sum(lab for _, lab in pairs[i:j])
        i = j
    p = sum(labels)
    q = len(labels) - p
    return (rank_sum - p * (p + 1) / 2) / (p * q) if p and q else None


def cross_validate(feats, labels):
    """Out-of-fold scores, FOLDS folds, fixed shuffle."""
    idx = list(range(len(feats)))
    random.Random(CV_SEED).shuffle(idx)
    out = [None] * len(feats)
    for f in range(FOLDS):
        test = set(idx[f::FOLDS])
        train = [i for i in idx if i not in test]
        m = fit([feats[i] for i in train], [is_hard(labels[i]) for i in train])
        for i in test:
            out[i] = score(m, feats[i])
    return out


def learn(levels):
    """Fit on every real level, cut five equal tiers, and report how well the score separates the labels."""
    feats = [real_features(r) for r in levels]
    labels = [r['difficulty'] for r in levels]
    r4 = lambda v: round(v, 4)
    model = fit(feats, [is_hard(y) for y in labels])
    # Round first: cuts and tiers come from the stored (rounded) model, so generation sees the same tiers.
    model = {'features': model['features'], 'mean': {k: r4(v) for k, v in model['mean'].items()},
             'std': {k: r4(v) for k, v in model['std'].items()},
             'weights': {k: r4(v) for k, v in model['weights'].items()}, 'bias': r4(model['bias'])}
    scores = [score(model, x) for x in feats]
    ordered = sorted(scores)
    cuts = [ordered[int(len(ordered) * q / TIERS)] for q in range(1, TIERS)]
    oof = cross_validate(feats, labels)
    special = [(s, y) for s, y in zip(oof, labels) if y != 'Normal']
    composition = collections.defaultdict(collections.Counter)
    for s, y in zip(scores, labels):
        composition[tier_of(cuts, s)][y] += 1
    return {
        'method': 'class-balanced L2 logistic regression: P(label is Hard or ExHard) from six level parameters; '
                  'score = log-odds; five equal-size tiers by score quantiles',
        'features': model['features'],
        'mean': model['mean'],
        'std': model['std'],
        'weights': model['weights'],
        'bias': model['bias'],
        'cuts': cuts,
        'range': [r4(ordered[0]), r4(ordered[-1])],
        'cross_validation': {
            'folds': FOLDS,
            'auc_hard_or_exhard_vs_normal': r4(auc(oof, [is_hard(y) for y in labels])),
            'auc_exhard_vs_hard': r4(auc([s for s, _ in special], [int(y == 'ExHard') for _, y in special])),
        },
        'composition': {str(t): dict(sorted(composition[t].items())) for t in sorted(composition)},
    }
