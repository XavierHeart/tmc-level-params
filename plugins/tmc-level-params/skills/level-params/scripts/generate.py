#!/usr/bin/env python3
"""Generate Stage 1 level parameters for a batch of levels.

    python3 scripts/generate.py --start 101 --end 150 \
        --normal 35 --hard 10 --exhard 5 --seed 20261009 --out <dir>
    python3 scripts/generate.py check --params <dir>/level-params.json

Implements 关卡参数生成规则.xlsx (统一生成规则): batch check -> difficulty
positions -> per level: joint form, T/U, four-category split, targets,
stacking, motion -> hard checks -> regenerate only failed levels -> batch
checks. Every statistic comes from model.json (see learn.py). Items are
placeholders (建筑1, 道具3); binding them to a theme is a later step.
"""
import argparse
import collections
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CATEGORIES = ('building', 'prop', 'vehicle', 'character')
ZH = {'building': '建筑', 'prop': '道具', 'vehicle': '载具', 'character': '角色'}
DIFFICULTIES = ('Normal', 'Hard', 'ExHard')
MULTIPLE = 3
T_JITTER = (-6, -3, 0, 3, 6)
U_JITTER = (-1, 0, 1)
MAX_TARGET_KINDS = 6
MAX_FORM_RUN = 3
POOL_MIN = 8
ATTEMPTS = 200


class Retry(Exception):
    """This attempt for one level cannot satisfy a rule; draw again."""


def load_model(path):
    with open(path, encoding='utf-8') as f:
        model = json.load(f)
    if model.get('schema') != 'tmc-level-params-model.v1':
        sys.exit(f'{path}: not a tmc-level-params-model.v1 file')
    return model


class Model:
    def __init__(self, raw):
        self.raw = raw
        self.levels = raw['levels']
        self.forms = {f['id']: f for f in raw['forms']}
        self.samples = [r for r in self.levels if 'split' in r]
        self._pools = {}
        # Form x difficulty weights (form samples are mostly late levels; see 证据与限制).
        self.form_weights = {d: collections.Counter(r['form'] for r in self.samples if r['difficulty'] == d)
                             for d in DIFFICULTIES}
        self.target_categories = {d: collections.Counter(
            t['category'] for r in self.levels if r['difficulty'] == d for t in r['targets'] if t['category'])
            for d in DIFFICULTIES}
        self.combo_categories = collections.defaultdict(collections.Counter)
        for r in self.levels:
            for c in r['stack']:
                if c['categories'] and c['size'] in (2, 3):
                    self.combo_categories[c['size']][tuple(c['categories'])] += 1

    def pool(self, difficulty, position):
        """Same-difficulty levels near `position` in the official order, widened until >= POOL_MIN."""
        key = (difficulty, position)
        if key not in self._pools:
            same = [r for r in self.levels if r['difficulty'] == difficulty]
            width = 25
            while True:
                found = [r for r in same if abs(r['order'] - position) <= width]
                if len(found) >= POOL_MIN or width > 4000:
                    break
                width *= 2
            self._pools[key] = (found, width)
        return self._pools[key]


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


# ---------- batch level: difficulty positions ----------

def difficulty_sequence(start, end, counts, rng):
    n = end - start + 1
    specials = counts['Hard'] + counts['ExHard']
    best = None
    for _ in range(500):
        seq = ['Normal'] * n
        slots = []
        for i in range(specials):  # stratified: one special per bucket
            lo, hi = i * n // specials, (i + 1) * n // specials - 1
            slots.append(rng.randint(lo, max(lo, hi)))
        ex = set()
        for i in range(counts['ExHard']):  # ExHard spread across the specials
            lo, hi = i * specials // counts['ExHard'], (i + 1) * specials // counts['ExHard'] - 1
            ex.add(slots[rng.randint(lo, max(lo, hi))])
        for s in slots:
            seq[s] = 'ExHard' if s in ex else 'Hard'
        hard_fail = any(seq[i] == seq[i + 1] == 'ExHard' for i in range(n - 1))
        soft = sum(seq[i] == 'ExHard' and seq[i + 1] != 'Normal' for i in range(n - 1))
        score = (hard_fail, soft)
        if best is None or score < best[0]:
            best = (score, seq)
        if score == (False, 0):
            break
    return best[1]


# ---------- one level ----------

def round3(x):
    return max(MULTIPLE, MULTIPLE * round(x / MULTIPLE))


def generate_level(model, level, difficulty, prev_forms, rng, overrides):
    pool, width = model.pool(difficulty, level)
    basis = {'pool_size': len(pool), 'pool_window': width}

    motion_rate = overrides.get('motion_rate')
    if motion_rate is None:
        motion_rate = sum(r['motion'] for r in pool) / len(pool)
    stack_rate = overrides.get('stack_rate')
    if stack_rate is None:
        stack_rate = sum(bool(r['stack']) for r in pool) / len(pool)
    motion = rng.random() < motion_rate
    stacking = rng.random() < stack_rate

    # Joint form; no run longer than MAX_FORM_RUN.
    weights = collections.Counter(model.form_weights[difficulty])
    if len(prev_forms) >= MAX_FORM_RUN and len(set(prev_forms[-MAX_FORM_RUN:])) == 1:
        weights.pop(prev_forms[-1], None)
    form = weighted(rng, weights)

    # T/U: one real pair from the level-segment pool, small variation.
    base = rng.choice(pool)
    t_jitter, u_jitter = rng.choice(T_JITTER), rng.choice(U_JITTER)
    T = round3(base['T'] + t_jitter)
    U = base['U'] + u_jitter
    basis.update(T_base=base['T'], U_base=base['U'], TU_source=base['key'])

    # Four-category split from one real sample of the same form (joint, never per field).
    candidates = [r for r in model.samples if r['form'] == form]
    same_diff = [r for r in candidates if r['difficulty'] == difficulty]
    if len(same_diff) >= 3:
        candidates = same_diff
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

    # Targets: kind count and per-target counts from the pool, categories by difficulty.
    n_targets = min(rng.choice([len(r['targets']) for r in pool if r['targets']] or [3]), MAX_TARGET_KINDS, U)
    known = [t['count'] for r in pool for t in r['targets'] if t['count']]
    if len(known) < 10:
        known = [t['count'] for r in model.levels if r['difficulty'] == difficulty for t in r['targets'] if t['count']]
    per_cat_targets = collections.Counter()
    targets = []
    cat_weights = model.target_categories[difficulty]
    for _ in range(n_targets):
        allowed = collections.Counter({c: w for c, w in cat_weights.items()
                                       if kinds.get(c, 0) > per_cat_targets[c]})
        if not allowed:
            break
        cat = weighted(rng, allowed)
        per_cat_targets[cat] += 1
        targets.append({'category': cat, 'kind': f'{ZH[cat]}{per_cat_targets[cat]}',
                        'count': round3(rng.choice(known))})
    # A target kind's count is all its placed copies; fit them inside the category quantity.
    for cat in per_cat_targets:
        cat_t = [t for t in targets if t['category'] == cat]
        room = cat_triples[cat] - (kinds[cat] - len(cat_t))
        while sum(t['count'] for t in cat_t) // MULTIPLE > room:
            big = max(cat_t, key=lambda t: t['count'])
            if big['count'] == MULTIPLE:
                raise Retry('targets do not fit category')
            big['count'] -= MULTIPLE

    # Per-kind counts: targets fixed, other kinds follow the sample's kind-size profile.
    per_kind = {}
    for cat in kind_share:
        fixed = {t['kind']: t['count'] // MULTIPLE for t in targets if t['category'] == cat}
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

    # Stacking: one real stacking structure from the pool, sets rounded to multiples of 3.
    stack = {'on': False, 'layers': 0, 'combos': []}
    if stacking:
        stacked = [r for r in pool if r['stack']]
        if not stacked:
            stacked = [r for r in model.levels if r['difficulty'] == difficulty and r['stack']]
        src = rng.choice(stacked)
        basis['stack_source'] = src['key']
        layers = 3 if any(c['size'] >= 3 for c in src['stack']) else 2
        wanted = []
        for c in src['stack']:
            sets = MULTIPLE * round(c['sets'] / MULTIPLE)
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

    durations = [r['duration_s'] for r in pool if r['duration_s']]
    row = {
        'level': level, 'difficulty': difficulty, 'form': form,
        'form_description': model.forms[form]['description'],
        'U': U, 'T': T,
        'categories': {c: {'kinds': kinds.get(c, 0), 'qty': cat_triples.get(c, 0) * MULTIPLE}
                       for c in CATEGORIES},
        'per_kind': per_kind,
        'targets': targets,
        'stacking': stack,
        'motion': motion,
        'duration_s': rng.choice(durations) if durations else None,
        'variation': {'T': T - base['T'], 'U': u_jitter},
        'basis': basis,
    }
    row['checks'] = check_level(row, pool)
    if not all(row['checks'].values()):
        raise Retry('checks failed: ' + ', '.join(k for k, v in row['checks'].items() if not v))
    return row


# ---------- checks ----------

def check_level(row, pool=None):
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
    checks = {'种类加总': kinds_ok and per_cat_ok, '数量加总': qty_ok and per_cat_ok, '数量3倍数': mult_ok,
              'U不超过T/3': ratio_ok, '目标明细': target_ok, '叠放3倍数': stack_ok, '运动前提': motion_ok}
    if pool is not None:
        lo_t, hi_t = min(r['T'] for r in pool), max(r['T'] for r in pool)
        lo_u, hi_u = min(r['U'] for r in pool), max(r['U'] for r in pool)
        checks['经验范围'] = (lo_t - max(T_JITTER) <= row['T'] <= hi_t + max(T_JITTER)
                          and lo_u - max(U_JITTER) <= row['U'] <= hi_u + max(U_JITTER))
    return checks


def signature(row):
    return json.dumps([row['difficulty'], row['form'], row['T'], row['U'], row['categories'],
                       row['targets'], row['stacking'], row['motion']], sort_keys=True, ensure_ascii=False)


def check_batch(spec, rows):
    seq = [r['difficulty'] for r in rows]
    n = spec['end'] - spec['start'] + 1
    run, longest = 1, 1
    for a, b in zip(rows, rows[1:]):
        run = run + 1 if a['form'] == b['form'] else 1
        longest = max(longest, run)
    return {
        f'总关数={n}': len(rows) == n and [r['level'] for r in rows] == list(range(spec['start'], spec['end'] + 1)),
        **{f'{d}={spec["counts"][d]}': seq.count(d) == spec['counts'][d] for d in DIFFICULTIES},
        '无相邻ExHard': not any(a == b == 'ExHard' for a, b in zip(seq, seq[1:])),
        '全部单关验算通过': all(all(r['checks'].values()) for r in rows),
        '无完全重复参数行': len({signature(r) for r in rows}) == len(rows),
        f'同形态连续不超过{MAX_FORM_RUN}关': longest <= MAX_FORM_RUN,
    }


# ---------- batch ----------

def generate(model, start, end, counts, seed, overrides=None):
    overrides = overrides or {}
    if end < start:
        raise ValueError('end must be >= start')
    if sum(counts.values()) != end - start + 1:
        raise ValueError(f'Normal+Hard+ExHard = {sum(counts.values())}, but {start}-{end} has {end - start + 1} levels')
    seq = difficulty_sequence(start, end, counts, random.Random(f'{seed}:sequence'))
    rows, seen, prev_forms, attempts = [], set(), [], {}
    for i, difficulty in enumerate(seq):
        level = start + i
        for attempt in range(ATTEMPTS):
            rng = random.Random(f'{seed}:{level}:{attempt}')
            try:
                row = generate_level(model, level, difficulty, prev_forms, rng, overrides)
            except Retry:
                continue
            if signature(row) in seen:
                continue
            break
        else:
            raise RuntimeError(f'level {level}: no valid parameters after {ATTEMPTS} attempts')
        attempts[level] = attempt + 1
        row['attempts'] = attempt + 1
        seen.add(signature(row))
        prev_forms.append(row['form'])
        rows.append(row)
    spec = {'start': start, 'end': end, 'counts': counts, 'seed': seed, 'overrides': overrides}
    return {
        'schema': 'tmc-level-params.v1',
        'input': spec,
        'model_sha256': hashlib.sha256(json.dumps(model.raw, sort_keys=True).encode()).hexdigest(),
        'levels': rows,
        'batch_checks': check_batch(spec, rows),
        'distribution': distribution(rows),
    }


def distribution(rows):
    out = {}
    for d in DIFFICULTIES:
        rs = [r for r in rows if r['difficulty'] == d]
        if not rs:
            continue
        T = sorted(r['T'] for r in rs)
        U = sorted(r['U'] for r in rs)
        out[d] = {'levels': len(rs), 'T_min': T[0], 'T_median': T[len(T) // 2], 'T_max': T[-1],
                  'U_min': U[0], 'U_median': U[len(U) // 2], 'U_max': U[-1],
                  'stacking_levels': sum(r['stacking']['on'] for r in rs),
                  'motion_levels': sum(r['motion'] for r in rs),
                  'forms': dict(sorted(collections.Counter(r['form'] for r in rs).items()))}
    return out


# ---------- text and files ----------

def fmt_duration(sec):
    return f'{sec // 60}:{sec % 60:02d}' if sec else ''


def text_targets(row):
    by_cat = collections.defaultdict(list)
    for t in row['targets']:
        by_cat[t['category']].append(f"{t['kind']}×{t['count']}")
    return '；'.join(f"{ZH[c]}{len(v)}种（{'、'.join(v)}）" for c in CATEGORIES for v in [by_cat.get(c)] if v)


def text_categories(row):
    return '；'.join(f"{ZH[c]}{row['categories'][c]['kinds']}种×{row['categories'][c]['qty']}" for c in CATEGORIES)


def text_stack(row):
    return '；'.join(f"{'+'.join(c['kinds'])}：{c['sets']}套" for c in row['stacking']['combos']) or '无'


COLUMNS = ['关卡', '难度', '联合形态', '形态说明', '物件种类总数', '物件数量总数', '目标种类数', '目标总数量', '目标种类明细',
           '建筑种类', '建筑数量', '道具种类', '道具数量', '载具种类', '载具数量', '角色种类', '角色数量', '各类物件明细',
           '是否叠放', '叠放类型', '叠放种类数', '叠放套数总计', '叠放种类套数明细', '是否包含运动载具', '时长',
           '竞品基准T/U', '变异量T/U', 'T/U来源关卡', '四类来源关卡', '叠放来源关卡',
           '种类加总', '数量加总', '数量3倍数', 'U不超过T/3', '目标明细', '叠放3倍数', '运动前提', '经验范围', '总验收']


def table_row(row):
    c, s, b, ck = row['categories'], row['stacking'], row['basis'], row['checks']
    yn = lambda v: '是' if v else '否'
    ok = lambda v: '通过' if v else '失败'
    return [row['level'], row['difficulty'], row['form'], row['form_description'], row['U'], row['T'],
            len(row['targets']), sum(t['count'] for t in row['targets']), text_targets(row),
            c['building']['kinds'], c['building']['qty'], c['prop']['kinds'], c['prop']['qty'],
            c['vehicle']['kinds'], c['vehicle']['qty'], c['character']['kinds'], c['character']['qty'],
            text_categories(row), yn(s['on']), f"{s['layers']}层" if s['on'] else '无', len(s['combos']),
            sum(x['sets'] for x in s['combos']), text_stack(row), yn(row['motion']), fmt_duration(row['duration_s']),
            f"{b['T_base']}/{b['U_base']}", f"{row['variation']['T']:+d}/{row['variation']['U']:+d}",
            b['TU_source'], b['split_source'], b.get('stack_source', ''),
            *[ok(ck.get(k, True)) for k in COLUMNS[30:38]], ok(all(ck.values()))]


def write_outputs(result, out_dir):
    import csv
    os.makedirs(out_dir, exist_ok=True)
    paths = {'json': os.path.join(out_dir, 'level-params.json'), 'csv': os.path.join(out_dir, 'level-params.csv')}
    with open(paths['json'], 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    with open(paths['csv'], 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for row in result['levels']:
            w.writerow(table_row(row))
    try:
        import xlsxwriter
    except ImportError:
        print('xlsxwriter not installed: skipped level-params.xlsx', file=sys.stderr)
        return paths
    paths['xlsx'] = os.path.join(out_dir, 'level-params.xlsx')
    wb = xlsxwriter.Workbook(paths['xlsx'])
    head = wb.add_format({'bold': True, 'bg_color': '#DDEBF7', 'border': 1, 'text_wrap': True})
    ws = wb.add_worksheet('试生成参数')
    ws.write_row(0, 0, COLUMNS, head)
    for i, row in enumerate(result['levels'], 1):
        ws.write_row(i, 0, table_row(row))
    ws.freeze_panes(1, 2)
    ws.set_column(0, len(COLUMNS) - 1, 12)
    for col in (8, 17, 22):
        ws.set_column(col, col, 44)
    ws2 = wb.add_worksheet('批次验收')
    ws2.write_row(0, 0, ['检查项', '结果'], head)
    for i, (k, v) in enumerate(result['batch_checks'].items(), 1):
        ws2.write_row(i, 0, [k, '通过' if v else '失败'])
    r0 = len(result['batch_checks']) + 2
    keys = ['levels', 'T_min', 'T_median', 'T_max', 'U_min', 'U_median', 'U_max', 'stacking_levels', 'motion_levels', 'forms']
    ws2.write_row(r0, 0, ['难度', '关数', '物件数量最小', '物件数量中位', '物件数量最大', '物件种类最小', '物件种类中位',
                          '物件种类最大', '叠放关数', '运动载具关数', '形态分布'], head)
    for i, (d, v) in enumerate(result['distribution'].items(), r0 + 1):
        ws2.write_row(i, 0, [d] + [json.dumps(v[k], ensure_ascii=False) if k == 'forms' else v[k] for k in keys])
    ws2.set_column(0, 10, 16)
    ws3 = wb.add_worksheet('输入')
    for i, (k, v) in enumerate([('关卡范围', f"{result['input']['start']}–{result['input']['end']}"),
                                *[(d, result['input']['counts'][d]) for d in DIFFICULTIES],
                                ('随机种子', str(result['input']['seed'])),
                                ('机制覆盖', json.dumps(result['input']['overrides'], ensure_ascii=False)),
                                ('model sha256', result['model_sha256'])]):
        ws3.write_row(i, 0, [k, v])
    ws3.set_column(0, 1, 30)
    wb.close()
    return paths


def recheck(path):
    with open(path, encoding='utf-8') as f:
        result = json.load(f)
    failures = []
    for row in result['levels']:
        checks = check_level(row)
        failures += [f"level {row['level']}: {k}" for k, v in checks.items() if not v]
    batch = check_batch(result['input'], result['levels'])
    failures += [f'batch: {k}' for k, v in batch.items() if not v]
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
    ap.add_argument('--start', type=int, required=True)
    ap.add_argument('--end', type=int, required=True)
    ap.add_argument('--normal', type=int, required=True)
    ap.add_argument('--hard', type=int, required=True)
    ap.add_argument('--exhard', type=int, required=True)
    ap.add_argument('--seed', default='auto', help="'auto' derives one from the input")
    ap.add_argument('--stack-rate', type=float, help='override: probability a level stacks')
    ap.add_argument('--motion-rate', type=float, help='override: probability a level has moving vehicles')
    ap.add_argument('--model', default=os.path.join(HERE, 'model.json'))
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    counts = {'Normal': args.normal, 'Hard': args.hard, 'ExHard': args.exhard}
    seed = args.seed
    if seed == 'auto':
        seed = f'{args.start}-{args.end}-{args.normal}-{args.hard}-{args.exhard}'
    overrides = {k: v for k, v in (('stack_rate', args.stack_rate), ('motion_rate', args.motion_rate)) if v is not None}
    try:
        result = generate(Model(load_model(args.model)), args.start, args.end, counts, seed, overrides)
    except (ValueError, RuntimeError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    paths = write_outputs(result, args.out)
    bad = [k for k, v in result['batch_checks'].items() if not v]
    print(f"{len(result['levels'])} levels, batch checks: {'all passed' if not bad else 'FAILED ' + ', '.join(bad)}")
    for k, p in paths.items():
        print(f'  {k}: {p}')
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
