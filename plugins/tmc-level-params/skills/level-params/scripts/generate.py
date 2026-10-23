#!/usr/bin/env python3
"""Generate level parameters for a batch of (theme, tier) inputs, one level each.

    python3 scripts/generate.py --item 月球基地:3 --item 冰雪小镇:5 --out <dir>
    python3 scripts/generate.py --batch batch.csv --out <dir>      # columns 主题,档位
    python3 scripts/generate.py check --params <dir>/level-params.json

Tiers come from model.json (see tiers.py): five equal-size tiers of real
levels, ranked by a score learned from the competitor's Hard/ExHard labels.
For one input: joint form, T/U, four-category split, per-kind counts, targets,
stacking, motion and duration are drawn from real levels of that tier, then
checked; the drawn parameters must score inside the tier and T must lie in
the tier's T band (25-75 % of the real T of the tier, times COUNT_SCALE). The
draw depends only on (theme, tier, seed), so the same input always gives the
same level, unless the batch rule moves it: in one batch a higher tier must
have T >= every lower tier; when the first draws break this, the fewest
levels are redrawn from their own seeded attempts.
Items are placeholders (建筑1, 道具3); levels of one theme share the names, so
one asset list per theme (the largest count per category) serves them all.
"""
import argparse
import collections
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tiers  # noqa: E402

SCHEMA = 'tmc-level-params.v3'
MODEL_SCHEMA = 'tmc-level-params-model.v2'
CATEGORIES = ('building', 'prop', 'vehicle', 'character')
ZH = {'building': '建筑', 'prop': '道具', 'vehicle': '载具', 'character': '角色'}
MULTIPLE = 3
T_JITTER = (-6, -3, 0, 3, 6)
U_JITTER = (-1, 0, 1)
MAX_TARGET_KINDS = 6
ATTEMPTS = 200
# Our sprites cover 1.4-1.9x the competitor's area on the same 40.96 canvas, so a
# competitor-sized level is too crowded (and too hard). T, U and stacking sets are
# drawn from the competitor tier, then multiplied by this; scores and ranges are
# checked on the competitor-equivalent values (count / COUNT_SCALE).
COUNT_SCALE = 0.5


class Retry(Exception):
    """This attempt for one tier cannot satisfy a rule; draw again."""


def load_model(path):
    with open(path, encoding='utf-8') as f:
        model = json.load(f)
    if model.get('schema') != MODEL_SCHEMA:
        sys.exit(f'{path}: not a {MODEL_SCHEMA} file (run learn.py)')
    if 'T_band' not in model.get('tiers', {}):
        sys.exit(f'{path}: no tiers.T_band (run learn.py)')
    return model


class Model:
    def __init__(self, raw):
        self.raw = raw
        self.levels = raw['levels']
        self.forms = {f['id']: f for f in raw['forms']}
        self.tiers = raw['tiers']
        self.sha256 = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        for r in self.levels:
            r['_tier'] = tiers.tier_of(self.tiers['cuts'], tiers.score(self.tiers, tiers.real_features(r)))
        self.pool = {t: [r for r in self.levels if r['_tier'] == t] for t in range(1, tiers.TIERS + 1)}
        self.samples = [r for r in self.levels if 'split' in r]
        self.form_weights = {t: collections.Counter(r['form'] for r in self.samples if r['_tier'] == t)
                             for t in self.pool}
        self.target_categories = {t: collections.Counter(
            x['category'] for r in self.pool[t] for x in r['targets'] if x['category']) for t in self.pool}
        self.combo_categories = collections.defaultdict(collections.Counter)
        for r in self.levels:
            for c in r['stack']:
                if c['categories'] and c['size'] in (2, 3):
                    self.combo_categories[c['size']][tuple(c['categories'])] += 1

    def bounds(self, tier):
        cuts = self.tiers['cuts']
        return (cuts[tier - 2] if tier > 1 else None, cuts[tier - 1] if tier <= len(cuts) else None)


def weighted(rng, counter):
    items = sorted(counter.items(), key=lambda kv: str(kv[0]))
    total = sum(w for _, w in items)
    pick = rng.random() * total
    for value, w in items:
        pick -= w
        if pick < 0:
            return value
    return items[-1][0]


def largest_remainder(total, weights, minimum):
    """Split `total` integer units by weights, each key getting at least minimum[key]."""
    base = dict(minimum)
    rest = total - sum(base.values())
    if rest < 0:
        raise Retry('minimums exceed total')
    wsum = sum(weights.values())
    if wsum <= 0:
        raise Retry('no weight')
    exact = {k: rest * w / wsum for k, w in weights.items()}
    for k in exact:
        base[k] = base.get(k, 0) + int(exact[k])
    left = total - sum(base.values())
    for k in sorted(exact, key=lambda k: (-(exact[k] - int(exact[k])), str(k)))[:left]:
        base[k] += 1
    return base


def round3(x):
    return max(MULTIPLE, MULTIPLE * round(x / MULTIPLE))


def t_band(tier_model, tier, count_scale=COUNT_SCALE):
    """(lo, hi) of T for this tier at our scale; None when the tier model has no band (older files)."""
    band = tier_model.get('T_band', {}).get(str(tier))
    if band is None:
        return None
    return round(band[0] * count_scale), round(band[1] * count_scale)


# ---------- one tier ----------

def generate_tier(model, tier, rng):
    pool = model.pool[tier]
    basis = {'pool_size': len(pool)}
    motion = rng.random() < sum(r['motion'] for r in pool) / len(pool)
    stacking = rng.random() < sum(bool(r['stack']) for r in pool) / len(pool)

    form = weighted(rng, model.form_weights[tier])

    # T/U: one real pair from the tier, small variation, then scaled to our sprite size.
    base = rng.choice(pool)
    T_eq = round3(base['T'] + rng.choice(T_JITTER))
    u_jitter = rng.choice(U_JITTER)
    T = round3(T_eq * COUNT_SCALE)
    U = int((base['U'] + u_jitter) * COUNT_SCALE + 0.5)
    basis.update(T_base=base['T'], U_base=base['U'], TU_source=base['key'])

    # Four-category split from one real sample of the same form (joint, never per field).
    candidates = [r for r in model.samples if r['form'] == form]
    same_tier = [r for r in candidates if r['_tier'] == tier]
    if len(same_tier) >= 3:
        candidates = same_tier
    if motion:
        candidates = [r for r in candidates if r['split']['vehicle']]
    if not candidates:
        raise Retry('no split sample')
    sample = rng.choice(candidates)
    basis['split_source'] = sample['key']
    split = sample['split']
    kind_share = {c: len(split[c]) for c in CATEGORIES if split[c]}
    if U < len(kind_share):
        raise Retry('U below category count')
    kinds = largest_remainder(U, kind_share, {c: 1 for c in kind_share})
    triples = T // MULTIPLE
    qty_share = {c: sum(split[c]) for c in kind_share}
    cat_triples = largest_remainder(triples, qty_share, {c: kinds[c] for c in kind_share})

    # Targets: kind count and per-target counts from the tier.
    n_targets = min(rng.choice([len(r['targets']) for r in pool if r['targets']] or [3]), MAX_TARGET_KINDS, U)
    known = [x['count'] for r in pool for x in r['targets'] if x['count']]
    per_cat_targets = collections.Counter()
    targets = []
    for _ in range(n_targets):
        allowed = collections.Counter({c: w for c, w in model.target_categories[tier].items()
                                       if kinds.get(c, 0) > per_cat_targets[c]})
        if not allowed:
            break
        cat = weighted(rng, allowed)
        per_cat_targets[cat] += 1
        targets.append({'category': cat, 'kind': f'{ZH[cat]}{per_cat_targets[cat]}',
                        'count': round3(rng.choice(known))})
    # A target kind's count is all its placed copies; fit them inside the category quantity.
    for cat in per_cat_targets:
        cat_t = [x for x in targets if x['category'] == cat]
        room = cat_triples[cat] - (kinds[cat] - len(cat_t))
        while sum(x['count'] for x in cat_t) // MULTIPLE > room:
            big = max(cat_t, key=lambda x: x['count'])
            if big['count'] == MULTIPLE:
                raise Retry('targets do not fit category')
            big['count'] -= MULTIPLE

    # Per-kind counts: targets fixed, other kinds follow the sample's kind-size profile.
    per_kind = {}
    for cat in kind_share:
        fixed = {x['kind']: x['count'] // MULTIPLE for x in targets if x['category'] == cat}
        others = [f'{ZH[cat]}{i}' for i in range(len(fixed) + 1, kinds[cat] + 1)]
        left = cat_triples[cat] - sum(fixed.values())
        units = {}
        if others:
            profile = split[cat]
            w = {k: profile[i % len(profile)] * rng.uniform(0.75, 1.25) for i, k in enumerate(others)}
            units = largest_remainder(left, w, {k: 1 for k in others})
        elif left:
            raise Retry('category quantity left without kinds')
        for k, v in list(fixed.items()) + list(units.items()):
            per_kind[k] = v * MULTIPLE

    # Stacking: one real stacking structure from the tier, sets rounded to multiples of 3.
    stack = {'on': False, 'layers': 0, 'combos': []}
    if stacking:
        src = rng.choice([r for r in pool if r['stack']])
        basis['stack_source'] = src['key']
        layers = 3 if any(c['size'] >= 3 for c in src['stack']) else 2
        wanted = []
        for c in src['stack']:
            sets = MULTIPLE * round(c['sets'] * COUNT_SCALE / MULTIPLE)
            if sets:
                cats = tuple(c['categories']) if c['categories'] and c['size'] == layers else None
                wanted.append((cats, sets))
        if not wanted:
            wanted = [(None, MULTIPLE)]
        used = collections.Counter()
        combos = []
        options = collections.Counter({k: v for k, v in model.combo_categories[layers].items()
                                       if all(c in kinds for c in k)})
        for source_cats, sets in wanted:
            for attempt in range(40):
                # Keep the source categories first; when they do not fit, draw other learned ones.
                if source_cats and attempt < 10 and all(c in kinds for c in source_cats):
                    cats = source_cats
                elif options:
                    cats = weighted(rng, options)
                else:
                    break
                chosen = []
                for c in cats:
                    pool_k = [k for k in per_kind if k.startswith(ZH[c]) and k not in chosen
                              and per_kind[k] - used[k] >= sets]
                    if not pool_k:
                        break
                    chosen.append(rng.choice(sorted(pool_k)))
                if len(chosen) == layers and chosen not in [x['kinds'] for x in combos]:
                    for k in chosen:
                        used[k] += sets
                    combos.append({'kinds': chosen, 'sets': sets})
                    break
        if not combos:
            raise Retry('no stacking combo fits')
        basis['stack_wanted'] = {'combos': len(wanted), 'sets': sum(s for _, s in wanted)}
        basis['stack_dropped'] = len(wanted) - len(combos)
        stack = {'on': True, 'layers': layers, 'combos': combos}

    row = {
        'tier': tier, 'form': form, 'form_description': model.forms[form]['description'],
        'U': U, 'T': T, 'count_scale': COUNT_SCALE,
        'categories': {c: {'kinds': kinds.get(c, 0), 'qty': cat_triples.get(c, 0) * MULTIPLE} for c in CATEGORIES},
        'per_kind': per_kind,
        'targets': targets,
        'stacking': stack,
        'motion': motion,
        'duration_s': rng.choice([r['duration_s'] for r in pool]),
        'variation': {'T': T_eq - base['T'], 'U': u_jitter},
        'basis': basis,
    }
    row['score'] = round(tiers.score(model.tiers, tiers.row_features(row)), 4)
    row['checks'] = check_tier(row, model.tiers, pool)
    if not all(row['checks'].values()):
        raise Retry('checks failed')
    return row


def check_tier(row, tier_model, pool=None):
    cats = row['categories']
    pk = row['per_kind']
    kinds_ok = sum(c['kinds'] for c in cats.values()) == row['U'] and len(pk) == row['U']
    qty_ok = sum(c['qty'] for c in cats.values()) == row['T'] == sum(pk.values())
    per_cat_ok = all(
        sum(1 for k in pk if k.startswith(ZH[c])) == cats[c]['kinds']
        and sum(v for k, v in pk.items() if k.startswith(ZH[c])) == cats[c]['qty']
        and (cats[c]['kinds'] == 0) == (cats[c]['qty'] == 0) for c in CATEGORIES)
    mult_ok = all(v % MULTIPLE == 0 and v >= MULTIPLE for v in pk.values()) and row['T'] % MULTIPLE == 0
    ratio_ok = row['U'] * MULTIPLE <= row['T']
    t = row['targets']
    target_ok = (1 <= len(t) <= MAX_TARGET_KINDS and len({x['kind'] for x in t}) == len(t)
                 and all(x['count'] % MULTIPLE == 0 and x['count'] >= MULTIPLE and pk.get(x['kind']) == x['count']
                         for x in t))
    s = row['stacking']
    if s['on']:
        use = collections.Counter()
        for c in s['combos']:
            for k in c['kinds']:
                use[k] += c['sets']
        stack_ok = (s['layers'] in (2, 3) and bool(s['combos'])
                    and all(len(c['kinds']) == s['layers'] == len(set(c['kinds'])) and c['sets'] % MULTIPLE == 0
                            and c['sets'] >= MULTIPLE for c in s['combos'])
                    and all(use[k] <= pk.get(k, 0) for k in use)
                    and len({tuple(c['kinds']) for c in s['combos']}) == len(s['combos']))
    else:
        stack_ok = s['layers'] == 0 and not s['combos']
    motion_ok = not row['motion'] or cats['vehicle']['kinds'] >= 1
    score = tiers.score(tier_model, tiers.row_features(row))
    in_tier = tiers.tier_of(tier_model['cuts'], score) == row['tier'] and abs(score - row['score']) < 1e-3
    checks = {'种类加总': kinds_ok and per_cat_ok, '数量加总': qty_ok and per_cat_ok, '数量3倍数': mult_ok,
              'U不超过T/3': ratio_ok, '目标明细': target_ok, '叠放3倍数': stack_ok, '运动前提': motion_ok,
              '难度分在档内': in_tier}
    band = t_band(tier_model, row['tier'], row.get('count_scale', 1.0))
    if band is not None:
        checks['T在档内范围'] = band[0] <= row['T'] <= band[1]
    if pool is not None:
        # Compare competitor-equivalent counts; tolerance = jitter + rounding after scaling.
        k = row.get('count_scale', 1.0)
        lo_t, hi_t = min(r['T'] for r in pool), max(r['T'] for r in pool)
        lo_u, hi_u = min(r['U'] for r in pool), max(r['U'] for r in pool)
        tol_t, tol_u = max(T_JITTER) + MULTIPLE / 2 / k, max(U_JITTER) + 0.5 / k
        checks['经验范围'] = (lo_t - tol_t <= row['T'] / k <= hi_t + tol_t
                          and lo_u - tol_u <= row['U'] / k <= hi_u + tol_u)
    return checks


def signature(row):
    return json.dumps([row['form'], row['T'], row['U'], row['categories'], row['per_kind'], row['targets'],
                       row['stacking'], row['motion'], row['duration_s']], sort_keys=True, ensure_ascii=False)


# ---------- batch ----------

def parse_item(text):
    """'主题:档位' (the last ':' or '：' separates them)."""
    text = text.strip().replace('：', ':')
    theme, sep, tier = text.rpartition(':')
    if not sep or not theme.strip():
        raise ValueError(f"'{text}': write theme:tier, e.g. 月球基地:3")
    try:
        tier = int(tier)
    except ValueError:
        raise ValueError(f"'{text}': tier must be a number 1-{tiers.TIERS}")
    return {'theme': theme.strip(), 'tier': tier}


def read_batch(path):
    """CSV with a header; theme column 主题/theme, tier column 档位/tier."""
    import csv
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    items = []
    for n, r in enumerate(rows, 2):
        r = {(k or '').strip(): (v or '').strip() for k, v in r.items()}
        theme, tier = r.get('主题') or r.get('theme'), r.get('档位') or r.get('tier')
        if not theme and not tier:
            continue
        if not theme or not tier:
            raise ValueError(f'{path} line {n}: needs both 主题 and 档位')
        items.append(parse_item(f'{theme}:{tier}'))
    return items


def check_items(items):
    if not items:
        raise ValueError('no input: give at least one theme:tier')
    seen = set()
    for it in items:
        if not 1 <= it['tier'] <= tiers.TIERS:
            raise ValueError(f"{it['theme']}:{it['tier']}: tier must be 1-{tiers.TIERS}")
        key = (it['theme'], it['tier'])
        if key in seen:
            raise ValueError(f"{it['theme']}:{it['tier']} appears twice; the same input always gives the same level")
        seen.add(key)


def item_seed(theme, seed=None):
    return f'{theme}:{seed}' if seed else theme


def level_draws(model, theme, tier, seed=None):
    """Every valid draw among the ATTEMPTS seeded attempts, in attempt order."""
    s = item_seed(theme, seed)
    for attempt in range(ATTEMPTS):
        rng = random.Random(f'{s}:tier{tier}:{attempt}')
        try:
            row = generate_tier(model, tier, rng)
        except Retry:
            continue
        row['attempts'] = attempt + 1
        yield row


def generate_level(model, theme, tier, seed=None):
    for row in level_draws(model, theme, tier, seed):
        return row
    raise RuntimeError(f'{theme}:{tier}: no valid parameters after {ATTEMPTS} attempts')


def order_checks(levels):
    """Per level: T >= T of every lower-tier level and <= T of every higher-tier level in the batch."""
    return [all(r['T'] >= o['T'] for o in levels if o['tier'] < r['tier'])
            and all(r['T'] <= o['T'] for o in levels if o['tier'] > r['tier']) for r in levels]


def order_batch(model, items, rows, seed=None):
    """2026-10-23: in one batch a higher tier must have T >= every lower tier.

    When the first draws break this, choose, per tier, a T interval [cut below, cut above]
    (cuts non-decreasing with the tier) so that the fewest levels change; a changed level
    takes its earliest valid seeded attempt inside its interval. Deterministic; at most
    ATTEMPTS attempts per level. Raises RuntimeError when no choice works."""
    if all(order_checks(rows)):
        return rows
    draws = [list(level_draws(model, it['theme'], it['tier'], seed)) for it in items]
    present = sorted({it['tier'] for it in items})
    members = {t: [i for i, it in enumerate(items) if it['tier'] == t] for t in present}
    values = sorted({d['T'] for ds in draws for d in ds})
    lo_inf, hi_inf = float('-inf'), float('inf')

    def pick(i, a, b):
        """(changed, attempt) of the draw level i takes inside [a, b], or None."""
        if a <= rows[i]['T'] <= b:
            return (0, rows[i]['attempts'])
        for d in draws[i]:
            if a <= d['T'] <= b:
                return (1, d['attempts'])
        return None

    def cost(t, a, b):
        total = (0, 0)
        for i in members[t]:
            c = pick(i, a, b)
            if c is None:
                return None
            total = (total[0] + c[0], total[1] + c[1])
        return total

    def add(x, y):
        return None if x is None or y is None else (x[0] + y[0], x[1] + y[1])

    # best[c] = (cost, cuts) for the tiers so far, with the top cut at value c.
    best = {c: (cost(present[0], lo_inf, c), [c]) for c in values}
    best = {c: v for c, v in best.items() if v[0] is not None}
    for t in present[1:-1]:
        nxt = {}
        for c in values:
            options = [(add(v[0], cost(t, prev, c)), v[1] + [c]) for prev, v in best.items() if prev <= c]
            options = [o for o in options if o[0] is not None]
            if options:
                nxt[c] = min(options, key=lambda o: (o[0], o[1]))
        best = nxt
    final = [(add(v[0], cost(present[-1], prev, hi_inf)), v[1]) for prev, v in best.items()]
    final = [o for o in final if o[0] is not None]
    if not final:
        raise RuntimeError(
            'batch order: no draw (at most %d attempts per level) gives every higher tier a T >= every '
            'lower tier; tiers in this batch: %s' % (ATTEMPTS, ', '.join(map(str, present))))
    _, cuts = min(final, key=lambda o: (o[0], o[1]))
    bounds = [lo_inf] + cuts + [hi_inf]
    out = list(rows)
    for j, t in enumerate(present):
        a, b = bounds[j], bounds[j + 1]
        for i in members[t]:
            if not a <= rows[i]['T'] <= b:
                out[i] = next(d for d in draws[i] if a <= d['T'] <= b)
                out[i]['basis']['order_redrawn'] = True
    return out


def slot_category(slot):
    for c in CATEGORIES:
        if slot.startswith(ZH[c]) and slot[len(ZH[c]):].isdigit():
            return c
    raise ValueError(f'not a placeholder slot: {slot}')


def asset_lists(levels):
    """Per theme: largest kind count per category over that theme's levels."""
    out = {}
    for theme in dict.fromkeys(r['theme'] for r in levels):
        rows = [r for r in levels if r['theme'] == theme]
        kinds = {c: max(r['categories'][c]['kinds'] for r in rows) for c in CATEGORIES}
        out[theme] = {'kinds': kinds, 'total': sum(kinds.values()),
                      'names': [f'{ZH[c]}{i}' for c in CATEGORIES for i in range(1, kinds[c] + 1)],
                      'tiers': sorted(r['tier'] for r in rows)}
    return out


def check_batch(levels, assets):
    keys = [(r['theme'], r['tier']) for r in levels]
    return {
        '每关验算通过': all(all(r['checks'].values()) for r in levels),
        '没有重复的输入': len(set(keys)) == len(keys),
        '序号连续': [r['index'] for r in levels] == list(range(1, len(levels) + 1)),
        '物件都在本主题素材清单内': all(set(r['per_kind']) <= set(assets[r['theme']]['names']) for r in levels),
    }


def tier_model_summary(model):
    keep = ('method', 'features', 'mean', 'std', 'weights', 'bias', 'cuts', 'T_band_quantiles', 'T_band',
            'cross_validation')
    out = {k: model.tiers[k] for k in keep}
    out['T_band_scaled'] = {str(t): list(t_band(model.tiers, t)) for t in range(1, tiers.TIERS + 1)}
    return out


def generate(model, items, seed=None):
    items = [{'theme': it['theme'].strip(), 'tier': int(it['tier'])} for it in items]
    check_items(items)
    rows = order_batch(model, items, [generate_level(model, it['theme'], it['tier'], seed) for it in items], seed)
    for row, ok in zip(rows, order_checks(rows)):
        row['checks']['高档不少于低档'] = ok
    levels = [{'index': n, 'theme': it['theme'], **row} for n, (it, row) in enumerate(zip(items, rows), 1)]
    assets = asset_lists(levels)
    return {
        'schema': SCHEMA,
        'input': {'items': items, 'seed': seed},
        'model_sha256': model.sha256,
        'tier_model': tier_model_summary(model),
        'levels': levels,
        'assets': assets,
        'checks': check_batch(levels, assets),
    }


# ---------- text and files ----------

def fmt_duration(sec):
    return f'{sec // 60}:{sec % 60:02d}' if sec else ''


def label(kind, nm=None):
    """'环形居住舱（建筑1）' when the slot has a name, else the placeholder."""
    return f"{nm[kind]['name']}（{kind}）" if nm and kind in nm else kind


def text_targets(row, nm=None):
    by = collections.defaultdict(list)
    for x in row['targets']:
        by[x['category']].append(x)
    return '；'.join(f"{ZH[c]}{len(by[c])}种（{'、'.join(f'{label(x['kind'], nm)}×{x['count']}' for x in by[c])}）"
                    for c in CATEGORIES if by[c])


def text_categories(row):
    return '；'.join(f"{ZH[c]}{row['categories'][c]['kinds']}种×{row['categories'][c]['qty']}" for c in CATEGORIES)


def text_stack(row, nm=None):
    return '；'.join(f"{'+'.join(label(k, nm) for k in c['kinds'])}：{c['sets']}套"
                    for c in row['stacking']['combos']) or '无'


def text_per_kind(row, nm=None):
    return '、'.join(f'{label(k, nm)}×{v}' for k, v in row['per_kind'].items())


CHECK_KEYS = ['种类加总', '数量加总', '数量3倍数', 'U不超过T/3', '目标明细', '叠放3倍数', '运动前提', '难度分在档内',
              'T在档内范围', '高档不少于低档', '经验范围']
COLUMNS = ['序号', '主题', '档位', '难度分', '联合形态', '形态说明', '物件种类总数', '物件数量总数', '目标种类数', '目标总数量',
           '目标种类明细', '建筑种类', '建筑数量', '道具种类', '道具数量', '载具种类', '载具数量', '角色种类', '角色数量',
           '各类物件明细', '每种物件数量', '是否叠放', '叠放类型', '叠放种类数', '叠放套数总计', '叠放种类套数明细',
           '是否包含运动载具', '时长', '竞品基准T/U', '变异量T/U', 'T/U来源关卡', '四类来源关卡', '叠放来源关卡',
           *CHECK_KEYS, '总验收']


def table_row(row, nm=None):
    c, s, b, ck = row['categories'], row['stacking'], row['basis'], row['checks']
    yn = lambda v: '是' if v else '否'
    ok = lambda v: '通过' if v else '失败'
    return [row['index'], row['theme'], row['tier'], row['score'], row['form'], row['form_description'], row['U'], row['T'],
            len(row['targets']), sum(x['count'] for x in row['targets']), text_targets(row, nm),
            c['building']['kinds'], c['building']['qty'], c['prop']['kinds'], c['prop']['qty'],
            c['vehicle']['kinds'], c['vehicle']['qty'], c['character']['kinds'], c['character']['qty'],
            text_categories(row), text_per_kind(row, nm), yn(s['on']), f"{s['layers']}层" if s['on'] else '无',
            len(s['combos']), sum(x['sets'] for x in s['combos']), text_stack(row, nm), yn(row['motion']),
            fmt_duration(row['duration_s']), f"{b['T_base']}/{b['U_base']}",
            f"{row['variation']['T']:+d}/{row['variation']['U']:+d}", b['TU_source'], b['split_source'],
            b.get('stack_source', ''), *[ok(ck.get(k, True)) for k in CHECK_KEYS], ok(all(ck.values()))]


def write_outputs(result, out_dir):
    import csv
    os.makedirs(out_dir, exist_ok=True)
    paths = {'json': os.path.join(out_dir, 'level-params.json'), 'csv': os.path.join(out_dir, 'level-params.csv')}
    with open(paths['json'], 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    names = result.get('names') or {}
    with open(paths['csv'], 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for row in result['levels']:
            w.writerow(table_row(row, names.get(row['theme'])))
    try:
        import xlsxwriter
    except ImportError:
        print('xlsxwriter not installed: skipped level-params.xlsx', file=sys.stderr)
        return paths
    paths['xlsx'] = os.path.join(out_dir, 'level-params.xlsx')
    wb = xlsxwriter.Workbook(paths['xlsx'])
    head = wb.add_format({'bold': True, 'bg_color': '#DDEBF7', 'border': 1, 'text_wrap': True})
    ws = wb.add_worksheet('关卡参数')
    ws.write_row(0, 0, COLUMNS, head)
    for i, row in enumerate(result['levels'], 1):
        ws.write_row(i, 0, table_row(row, names.get(row['theme'])))
    ws.freeze_panes(1, 3)
    ws.set_column(0, len(COLUMNS) - 1, 12)
    for col in (10, 19, 20, 25):
        ws.set_column(col, col, 44)
    ws2 = wb.add_worksheet('素材清单')
    ws2.write_row(0, 0, ['主题', '用到的档位', '建筑', '道具', '载具', '角色', '合计'], head)
    for i, (theme, a) in enumerate(result['assets'].items(), 1):
        ws2.write_row(i, 0, [theme, '、'.join(map(str, a['tiers'])), *[a['kinds'][c] for c in CATEGORIES], a['total']])
    ws2.set_column(0, 6, 12)
    ws4 = wb.add_worksheet('素材名称')
    ws4.write_row(0, 0, ['主题', '占位名', '类别', '名称', 'id'], head)
    i = 1
    for theme, a in result['assets'].items():
        for slot in a['names']:
            nm = names.get(theme, {}).get(slot, {})
            ws4.write_row(i, 0, [theme, slot, ZH[slot_category(slot)], nm.get('name', '（未命名）'), nm.get('id', '')])
            i += 1
    ws4.set_column(0, 2, 12)
    ws4.set_column(3, 4, 24)
    ws3 = wb.add_worksheet('验算')
    ws3.write_row(0, 0, ['检查项', '结果'], head)
    for i, (k, v) in enumerate(result['checks'].items(), 1):
        ws3.write_row(i, 0, [k, '通过' if v else '失败'])
    r0 = len(result['checks']) + 2
    for i, (k, v) in enumerate([('输入', '；'.join(f"{it['theme']}:{it['tier']}" for it in result['input']['items'])),
                                ('随机种子', str(result['input']['seed'] or '（主题名）')),
                                ('model sha256', result['model_sha256'])], r0):
        ws3.write_row(i, 0, [k, v])
    ws3.set_column(0, 1, 30)
    wb.close()
    return paths


def recheck(path):
    """Re-run every check on a written file; uses the tier model stored in the file."""
    with open(path, encoding='utf-8') as f:
        result = json.load(f)
    if result.get('schema') != SCHEMA:
        return [f'not a {SCHEMA} file']
    failures = []
    for row, ordered in zip(result['levels'], order_checks(result['levels'])):
        checks = check_tier(row, result['tier_model'])
        if '高档不少于低档' in row['checks']:  # files written before 2026-10-23 have no batch order rule
            checks['高档不少于低档'] = ordered
        failures += [f"level {row['index']} {row['theme']}:{row['tier']}: {k}" for k, v in checks.items() if not v]
    assets = asset_lists(result['levels'])
    failures += [f'batch: {k}' for k, v in check_batch(result['levels'], assets).items() if not v]
    if assets != result['assets']:
        failures.append('batch: assets do not match the levels')
    if result.get('names'):
        import names
        failures += [f'names: {p}' for p in names.check_mapping(result, result['names'])]
    return failures


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['check']:
        ap = argparse.ArgumentParser(prog='generate.py check')
        ap.add_argument('--params', required=True)
        args = ap.parse_args(argv[1:])
        failures = recheck(args.params)
        for f in failures:
            print(f)
        print('ok' if not failures else f'{len(failures)} failure(s)')
        return 1 if failures else 0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--item', action='append', default=[], help='theme:tier, repeatable')
    ap.add_argument('--batch', help='CSV with columns 主题,档位')
    ap.add_argument('--seed', help='default: none (the theme name alone decides the draw)')
    ap.add_argument('--model', default=os.path.join(HERE, 'model.json'))
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    try:
        items = (read_batch(args.batch) if args.batch else []) + [parse_item(x) for x in args.item]
        result = generate(Model(load_model(args.model)), items, args.seed)
    except (ValueError, RuntimeError, OSError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    paths = write_outputs(result, args.out)
    bad = [k for k, v in result['checks'].items() if not v]
    print(f"{len(result['levels'])} levels, checks: {'all passed' if not bad else 'FAILED ' + ', '.join(bad)}")
    for k, p in paths.items():
        print(f'  {k}: {p}')
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
