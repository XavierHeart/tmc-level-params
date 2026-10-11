#!/usr/bin/env python3
"""Level-parameter harness: deterministic steps around generate.py, driven by the agent.

    level-params-harness init --run <dir> --start 101 --end 150 --normal 35 --hard 10 --exhard 5
    level-params-harness generate --run <dir>        # params + scores + charts + flags
    level-params-harness level --run <dir> --level 120
    level-params-harness reroll --run <dir> --level 120 [--undo]
    level-params-harness weights --run <dir> N=20 K=15 ...
    level-params-harness review-template --run <dir>  # agent fills review.json after viewing the charts
    level-params-harness finalize --run <dir>
    level-params-harness status|check|journal|note --run <dir>

The harness computes, checks and draws; the agent looks at the charts, decides
which flagged levels to keep or re-draw, and records why. Nothing is final until
finalize accepts a review bound to the current parameters.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import charts  # noqa: E402
import generate  # noqa: E402
import pressure  # noqa: E402

RUN_SCHEMA = 'level-params-run.v1'
REVIEW_SCHEMA = 'level-params-review.v1'
JUMP_PERCENTILE = 0.9          # a jump above the competitor P90 jump is flagged
RANGE_PERCENTILES = (0.05, 0.95)  # composite outside the competitor P5-P95 of the same difficulty is flagged
RANGE_WINDOW = 50              # competitor levels within +-50 of the batch, same difficulty
TIER_JUMP_RATIO = 1.25         # mean adjacent jump above 1.25x the competitor's is a batch flag
DIFFICULTIES = generate.DIFFICULTIES

_MODEL = None


def model():
    global _MODEL
    if _MODEL is None:
        m = generate.Model(generate.load_model(os.path.join(HERE, 'model.json')))
        targets = [t['count'] for r in m.levels for t in r['targets'] if t['count']]
        fallback = pressure._median(targets, 9)
        _MODEL = (m, fallback, pressure.baselines(m.levels, fallback))
    return _MODEL


# ---------- run directory ----------

def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def path(run, *parts):
    return os.path.join(run, *parts)


def load(file, default=None):
    if not os.path.exists(file):
        if default is not None:
            return default
        raise SystemExit(f'error: missing {file}')
    with open(file, encoding='utf-8') as f:
        return json.load(f)


def save(file, data):
    os.makedirs(os.path.dirname(file), exist_ok=True)
    tmp = file + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, file)


def journal(run, command, **detail):
    with open(path(run, 'journal.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps({'time': now(), 'command': command, **detail}, ensure_ascii=False) + '\n')


def load_run(run):
    state = load(path(run, 'run.json'))
    if state.get('schema') != RUN_SCHEMA:
        raise SystemExit(f'error: {run}/run.json is not {RUN_SCHEMA}')
    return state


def sha(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ---------- scoring and flags ----------

def reference(start, end):
    m, fallback, _ = model()
    out = {}
    for rec in m.levels:
        if start <= rec['order'] <= end:
            raw, imputed = pressure.real_pressure(rec, fallback)
            out[rec['order']] = {'key': rec['key'], 'difficulty': rec['difficulty'], 'raw': raw,
                                 'target_imputed': imputed}
    return out


def competitor_stats(start, end, weights):
    """Thresholds from real levels: global P90 adjacent jump, per-difficulty composite range near the batch."""
    m, fallback, base = model()
    ordered = sorted(m.levels, key=lambda r: r['order'])
    comp = {r['order']: pressure.composite(pressure.real_pressure(r, fallback)[0], base, weights) for r in ordered}
    all_jumps = pressure.jumps([comp[r['order']] for r in ordered])
    ranges = {}
    for d in DIFFICULTIES:
        near = [comp[r['order']] for r in ordered
                if r['difficulty'] == d and start - RANGE_WINDOW <= r['order'] <= end + RANGE_WINDOW]
        if len([v for v in near if v is not None]) < 10:
            near = [comp[r['order']] for r in ordered if r['difficulty'] == d]
        ranges[d] = [pressure.percentile(near, p) for p in RANGE_PERCENTILES]
    return {'jump_threshold': pressure.percentile(all_jumps, JUMP_PERCENTILE),
            'jump_mean_all': pressure.mean(all_jumps), 'ranges': ranges}


def score_batch(result, weights):
    _, _, base = model()
    rows = result['levels']
    start, end = result['input']['start'], result['input']['end']
    ref = reference(start, end)
    xs = [r['level'] for r in rows]
    gen = [pressure.composite(r['pressure'], base, weights) for r in rows]
    refc = [pressure.composite(ref[x]['raw'], base, weights) if x in ref else None for x in xs]
    stats = competitor_stats(start, end, weights)
    flags = []
    for i, r in enumerate(rows):
        lo, hi = stats['ranges'][r['difficulty']]
        if gen[i] is not None and lo is not None and not lo <= gen[i] <= hi:
            flags.append({'id': f"L{r['level']}-range", 'level': r['level'], 'rule': 'range',
                          'detail': f"composite {gen[i]:.1f} outside competitor {r['difficulty']} P5-P95 "
                                    f"{lo:.1f}-{hi:.1f}"})
        if i and gen[i] is not None and gen[i - 1] is not None and abs(gen[i] - gen[i - 1]) > stats['jump_threshold']:
            flags.append({'id': f"L{r['level']}-jump", 'level': r['level'], 'rule': 'jump',
                          'detail': f"jump {gen[i - 1]:.1f} -> {gen[i]:.1f} from level {rows[i - 1]['level']} "
                                    f"exceeds competitor P90 jump {stats['jump_threshold']:.1f}"})
    tiers = {d: pressure.mean([g for g, r in zip(gen, rows) if r['difficulty'] == d]) for d in DIFFICULTIES}
    present = [tiers[d] for d in DIFFICULTIES if tiers[d] is not None]
    gen_jump = pressure.mean(pressure.jumps(gen))
    ref_jump = pressure.mean(pressure.jumps(refc)) if sum(v is not None for v in refc) > 1 else None
    batch_flags = []
    if any(b <= a for a, b in zip(present, present[1:])):
        batch_flags.append({'id': 'tier-order', 'detail': 'mean composite does not rise Normal < Hard < ExHard: '
                            + ', '.join(f'{d} {tiers[d]:.1f}' for d in DIFFICULTIES if tiers[d] is not None)})
    basis = ref_jump if ref_jump is not None else stats['jump_mean_all']
    if gen_jump is not None and basis and gen_jump > TIER_JUMP_RATIO * basis:
        batch_flags.append({'id': 'jumpy', 'detail': f'mean adjacent jump {gen_jump:.1f} > {TIER_JUMP_RATIO} x '
                            f"competitor {basis:.1f} ({'same level numbers' if ref_jump is not None else 'all levels'})"})
    r1 = lambda v: None if v is None else round(v, 2)
    return {
        'weights': weights,
        'levels': [{'level': x, 'difficulty': r['difficulty'], 'composite': r1(g), 'competitor': r1(c),
                    'competitor_key': ref.get(x, {}).get('key'),
                    'scores': {k: r1(pressure.score(k, r['pressure'][k], base)) for k in pressure.KEYS},
                    'competitor_scores': {k: r1(pressure.score(k, ref[x]['raw'][k], base)) for k in pressure.KEYS}
                    if x in ref else {}}
                   for x, r, g, c in zip(xs, rows, gen, refc)],
        'stats': {'std': r1(pressure.std(gen)), 'competitor_std': r1(pressure.std(refc)),
                  'mean_jump': r1(gen_jump), 'competitor_mean_jump': r1(ref_jump),
                  'max_jump': r1(max((j for j in pressure.jumps(gen) if j is not None), default=None)),
                  'tier_means': {d: r1(v) for d, v in tiers.items()},
                  'thresholds': {'jump': r1(stats['jump_threshold']),
                                 'range': {d: [r1(a), r1(b)] for d, (a, b) in stats['ranges'].items()}}},
        'flags': flags,
        'batch_flags': batch_flags,
    }


def draw(run, result, scores):
    rows, sl = result['levels'], scores['levels']
    xs = [r['level'] for r in rows]
    gen = [s['composite'] for s in sl]
    flagged = {f['level'] for f in scores['flags']}
    title = (f"Levels {result['input']['start']}-{result['input']['end']}  composite difficulty 0-100  "
             f"weights " + ' '.join(f'{k}{v}' for k, v in scores['weights'].items() if v))
    os.makedirs(path(run, 'review'), exist_ok=True)
    charts.curve_png(path(run, 'review', 'curve.png'), xs, gen, [s['competitor'] for s in sl],
                     pressure.rolling_median(gen), [r['difficulty'] for r in rows],
                     [i for i, x in enumerate(xs) if x in flagged], title)
    charts.pressures_png(path(run, 'review', 'pressures.png'), xs, [s['scores'] for s in sl],
                         [s['competitor_scores'] for s in sl], [r['difficulty'] for r in rows], pressure.KEYS)


def summary_md(result, scores):
    st, dist = scores['stats'], result['distribution']
    f1 = lambda v: '—' if v is None else f'{v:.1f}'
    lines = [f"# 关卡 {result['input']['start']}–{result['input']['end']} 参数摘要", '',
             '## 验算', '',
             '批次验算：' + ('全部通过' if all(result['batch_checks'].values()) else
                         '失败 ' + '、'.join(k for k, v in result['batch_checks'].items() if not v)),
             '单关验算失败：' + ('无' if all(all(r['checks'].values()) for r in result['levels']) else
                          '、'.join(str(r['level']) for r in result['levels'] if not all(r['checks'].values()))),
             '', '## 分布', '', '| 难度 | 关数 | 物件数 | 种类数 | 叠放关 | 运动关 |', '|---|---|---|---|---|---|']
    for d, v in dist.items():
        lines.append(f"| {d} | {v['levels']} | {v['T_min']}–{v['T_max']}（中位 {v['T_median']}） | "
                     f"{v['U_min']}–{v['U_max']}（中位 {v['U_median']}） | {v['stacking_levels']} | {v['motion_levels']} |")
    lines += ['', '## 难度曲线（综合分 0–100）', '', '| 指标 | 生成 | 竞品同编号 |', '|---|---|---|',
              f"| 标准差 σ | {f1(st['std'])} | {f1(st['competitor_std'])} |",
              f"| 相邻关平均跳变 | {f1(st['mean_jump'])} | {f1(st['competitor_mean_jump'])} |",
              f"| 最大跳变 | {f1(st['max_jump'])} | |",
              '| Normal / Hard / ExHard 均分 | ' + ' / '.join(f1(st['tier_means'][d]) for d in DIFFICULTIES) + ' | |',
              '', f"权重：{' '.join(f'{k}={v}' for k, v in scores['weights'].items())}", '',
              '## 需要复核的项', '']
    if not scores['flags'] and not scores['batch_flags']:
        lines.append('无。')
    for f in scores['batch_flags']:
        lines.append(f"- 批次 `{f['id']}`：{f['detail']}")
    for f in scores['flags']:
        lines.append(f"- 关卡 {f['level']} `{f['id']}`：{f['detail']}")
    lines += ['', '图：`review/curve.png`（综合曲线）、`review/pressures.png`（8 项分项）。',
              'D、C、M 是按关段预估的，布局后才有实测值。']
    return '\n'.join(lines) + '\n'


def build(run, state):
    """Generate the batch from run.json, then score, draw and flag it. Returns (result, scores)."""
    m = model()[0]
    i = state['input']
    try:
        result = generate.generate(m, i['start'], i['end'], i['counts'], i['seed'], i['overrides'], state['rerolls'])
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f'error: {e}')
    scores = score_batch(result, state['weights'])
    generate.write_outputs(result, run)
    save(path(run, 'scores.json'), scores)
    draw(run, result, scores)
    with open(path(run, 'review', 'summary.md'), 'w', encoding='utf-8') as f:
        f.write(summary_md(result, scores))
    state['params_sha256'] = sha(result['levels'])
    state['generated_at'] = now()
    save(path(run, 'run.json'), state)
    return result, scores


def report(run, result, scores):
    ok = all(result['batch_checks'].values()) and all(all(r['checks'].values()) for r in result['levels'])
    print(f"{len(result['levels'])} levels, checks {'passed' if ok else 'FAILED'}, "
          f"{len(scores['flags'])} level flag(s), {len(scores['batch_flags'])} batch flag(s)")
    for f in scores['batch_flags'] + scores['flags']:
        print(f"  {f['id']}: {f['detail']}")
    print(f"  view: {path(run, 'review', 'curve.png')}")
    print(f"  view: {path(run, 'review', 'pressures.png')}")
    print(f"  read: {path(run, 'review', 'summary.md')}")
    return ok


# ---------- commands ----------

def cmd_init(a):
    if os.path.exists(path(a.run, 'run.json')) and not a.force:
        raise SystemExit(f'error: {a.run} already has run.json (use --force to start over)')
    counts = {'Normal': a.normal, 'Hard': a.hard, 'ExHard': a.exhard}
    if a.end < a.start:
        raise SystemExit('error: --end must be >= --start')
    if sum(counts.values()) != a.end - a.start + 1:
        raise SystemExit(f'error: Normal+Hard+ExHard = {sum(counts.values())}, '
                         f'but {a.start}-{a.end} has {a.end - a.start + 1} levels')
    for k in ('stack_rate', 'motion_rate'):
        v = getattr(a, k)
        if v is not None and not 0 <= v <= 1:
            raise SystemExit(f'error: --{k.replace("_", "-")} must be between 0 and 1')
    seed = a.seed or f'{a.start}-{a.end}-{a.normal}-{a.hard}-{a.exhard}'
    overrides = {k: getattr(a, k) for k in ('stack_rate', 'motion_rate') if getattr(a, k) is not None}
    os.makedirs(a.run, exist_ok=True)
    state = {'schema': RUN_SCHEMA, 'created_at': now(),
             'input': {'start': a.start, 'end': a.end, 'counts': counts, 'seed': seed, 'overrides': overrides},
             'weights': dict(pressure.DEFAULT_WEIGHTS), 'rerolls': {},
             'model_sha256': hashlib.sha256(json.dumps(model()[0].raw, sort_keys=True).encode()).hexdigest()}
    save(path(a.run, 'run.json'), state)
    journal(a.run, 'init', input=state['input'])
    print(f'initialized {a.run}; next: generate')


def cmd_generate(a):
    state = load_run(a.run)
    result, scores = build(a.run, state)
    journal(a.run, 'generate', params_sha256=state['params_sha256'], flags=[f['id'] for f in scores['flags']],
            batch_flags=[f['id'] for f in scores['batch_flags']])
    return 0 if report(a.run, result, scores) else 1


def cmd_reroll(a):
    state = load_run(a.run)
    i = state['input']
    if not i['start'] <= a.level <= i['end']:
        raise SystemExit(f"error: level {a.level} is outside {i['start']}-{i['end']}")
    before = load(path(a.run, 'scores.json'), {}).get('levels', [])
    old = next((s['composite'] for s in before if s['level'] == a.level), None)
    key = str(a.level)
    n = state['rerolls'].get(key, 0) + (-1 if a.undo else 1)
    if n < 0:
        raise SystemExit(f'error: level {a.level} has no re-draw to undo')
    if n:
        state['rerolls'][key] = n
    else:
        state['rerolls'].pop(key, None)
    result, scores = build(a.run, state)
    new = next(s['composite'] for s in scores['levels'] if s['level'] == a.level)
    journal(a.run, 'reroll', level=a.level, draw=n, undo=a.undo, composite_before=old, composite_after=new,
            reason=a.reason or '')
    print(f"level {a.level}: draw {n}, composite {old if old is None else round(old, 1)} -> {round(new, 1)}")
    return 0 if report(a.run, result, scores) else 1


def cmd_weights(a):
    state = load_run(a.run)
    if a.reset:
        state['weights'] = dict(pressure.DEFAULT_WEIGHTS)
    for item in a.pairs:
        k, _, v = item.partition('=')
        if k not in pressure.KEYS or not v.isdigit():
            raise SystemExit(f"error: '{item}': use KEY=integer with KEY in {' '.join(pressure.KEYS)}")
        state['weights'][k] = int(v)
    if not any(state['weights'].values()):
        raise SystemExit('error: at least one weight must be above 0')
    result, scores = build(a.run, state)
    journal(a.run, 'weights', weights=state['weights'], reason=a.reason or '')
    return 0 if report(a.run, result, scores) else 1


def cmd_level(a):
    state = load_run(a.run)
    result = load(path(a.run, 'level-params.json'))
    scores = load(path(a.run, 'scores.json'))
    row = next((r for r in result['levels'] if r['level'] == a.level), None)
    if row is None:
        raise SystemExit(f'error: level {a.level} is not in this batch')
    s = next(x for x in scores['levels'] if x['level'] == a.level)
    t = dict(zip(generate.COLUMNS, generate.table_row(row)))
    b = row['basis']
    print(f"level {row['level']}  {row['difficulty']}  form {row['form']} ({row['form_description']})"
          f"  draw {state['rerolls'].get(str(a.level), 0)}")
    print(f"  objects {row['T']}, kinds {row['U']}; {t['各类物件明细']}")
    print(f"  targets {len(row['targets'])} kinds, {t['目标总数量']} in total: {t['目标种类明细']}")
    print(f"  stacking {t['叠放类型']}: {t['叠放种类套数明细']}; moving vehicles {t['是否包含运动载具']}; "
          f"duration {t['时长']}")
    print(f"  basis: T/U from {b['TU_source']} ({b['T_base']}/{b['U_base']}, change {t['变异量T/U']}); "
          f"split from {b['split_source']}" + (f"; stacking from {b['stack_source']}" if b.get('stack_source') else ''))
    print(f"  checks: {'all passed' if all(row['checks'].values()) else 'FAILED ' + ', '.join(k for k, v in row['checks'].items() if not v)}")
    comp = f" competitor {s['competitor_key']} {s['competitor']}" if s['competitor'] is not None else ''
    print(f"  composite {s['composite']}{comp}")
    for k, label, unit, _, _ in pressure.DIMENSIONS:
        est = ' (est.)' if k in ('M', 'D', 'C') else ''
        cs = s['competitor_scores'].get(k)
        print(f"    {k} {label}{est}: raw {row['pressure'][k]} {unit}, score {s['scores'][k]}"
              + (f", competitor score {cs}" if s['competitor_scores'] else ''))
    for f in scores['flags']:
        if f['level'] == a.level:
            print(f"  FLAG {f['id']}: {f['detail']}")


def cmd_review_template(a):
    state = load_run(a.run)
    scores = load(path(a.run, 'scores.json'))
    file = path(a.run, 'review.json')
    old = load(file, {})
    if old.get('params_sha256') == state['params_sha256'] and not a.force:
        raise SystemExit(f'error: {file} already matches the current parameters (use --force to reset it)')
    keep = {f['id']: f for f in old.get('flags', []) + old.get('batch_flags', [])}

    def entry(f):
        prev = keep.get(f['id'])     # carry a decision over only when the finding is unchanged
        same = prev and prev.get('detail') == f['detail']
        return {**f, 'decision': prev['decision'] if same else None, 'reason': prev['reason'] if same else ''}
    review = {'schema': REVIEW_SCHEMA, 'params_sha256': state['params_sha256'], 'weights': scores['weights'],
              'charts_viewed': False,
              'flags': [entry(f) for f in scores['flags']],
              'batch_flags': [entry(f) for f in scores['batch_flags']],
              'notes': old.get('notes', '')}
    save(file, review)
    journal(a.run, 'review-template', params_sha256=state['params_sha256'])
    print(f"wrote {file}: {len(review['flags'])} level flag(s), {len(review['batch_flags'])} batch flag(s). "
          "View the charts, set charts_viewed=true, and give each flag decision 'keep' with a reason; "
          "re-draw a level with reroll instead, then run review-template again.")


def review_problems(run, state):
    file = path(run, 'review.json')
    if not os.path.exists(file):
        return ['review.json is missing: run review-template']
    review = load(file)
    problems = []
    if review.get('schema') != REVIEW_SCHEMA:
        problems.append(f'review.json is not {REVIEW_SCHEMA}')
    if review.get('params_sha256') != state.get('params_sha256'):
        problems.append('review.json belongs to other parameters: run review-template again')
    if review.get('weights') != state['weights']:
        problems.append('review.json was written with other weights: run review-template again')
    if review.get('charts_viewed') is not True:
        problems.append('charts_viewed is not true')
    scores = load(path(run, 'scores.json'))
    current = {f['id'] for f in scores['flags'] + scores['batch_flags']}
    listed = {f['id'] for f in review.get('flags', []) + review.get('batch_flags', [])}
    for missing in sorted(current - listed):
        problems.append(f'flag {missing} is not in review.json')
    for f in review.get('flags', []) + review.get('batch_flags', []):
        if f['id'] not in current:
            continue
        if f.get('decision') != 'keep':
            problems.append(f"flag {f['id']}: decision must be 'keep' (or re-draw the level with reroll)")
        elif not str(f.get('reason', '')).strip():
            problems.append(f"flag {f['id']}: reason is empty")
    return problems


def cmd_finalize(a):
    state = load_run(a.run)
    result = load(path(a.run, 'level-params.json'))
    problems = []
    if sha(result['levels']) != state.get('params_sha256'):
        problems.append('level-params.json changed after generate: run generate again')
    problems += [f'level check failed: {x}' for x in generate.recheck(path(a.run, 'level-params.json'))]
    problems += review_problems(a.run, state)
    if problems:
        for p in problems:
            print(f'  {p}')
        print(f'not finalized: {len(problems)} problem(s)')
        journal(a.run, 'finalize', ok=False, problems=problems)
        return 1
    review = load(path(a.run, 'review.json'))
    final = path(a.run, 'final')
    if os.path.exists(final):
        shutil.rmtree(final)
    result['harness'] = {'weights': state['weights'], 'rerolls': state['rerolls'],
                         'review': {'flags': review['flags'], 'batch_flags': review['batch_flags'],
                                    'notes': review['notes']},
                         'finalized_at': now()}
    paths = generate.write_outputs(result, final)
    for name in ('curve.png', 'pressures.png', 'summary.md'):
        shutil.copy(path(a.run, 'review', name), path(final, name))
    save(path(a.run, 'finalize.json'), {'ok': True, 'params_sha256': state['params_sha256'],
                                        'time': result['harness']['finalized_at'], 'files': sorted(os.listdir(final))})
    journal(a.run, 'finalize', ok=True, params_sha256=state['params_sha256'])
    print(f'finalized: {final}')
    for k, p in paths.items():
        print(f'  {k}: {p}')
    return 0


def cmd_check(a):
    failures = generate.recheck(a.params or path(a.run, 'level-params.json'))
    for f in failures:
        print(f)
    print('ok' if not failures else f'{len(failures)} failure(s)')
    return 1 if failures else 0


def cmd_status(a):
    state = load_run(a.run)
    i = state['input']
    print(f"levels {i['start']}-{i['end']} {i['counts']} seed {i['seed']} overrides {i['overrides'] or 'none'}")
    print(f"weights {state['weights']}; re-drawn levels {state['rerolls'] or 'none'}")
    if 'params_sha256' not in state:
        print('next: generate')
        return 0
    fin = load(path(a.run, 'finalize.json'), {})
    if fin.get('ok') and fin.get('params_sha256') == state['params_sha256']:
        print(f"finalized at {fin['time']}: {path(a.run, 'final')}")
        return 0
    problems = review_problems(a.run, state)
    print('next: ' + ('finalize' if not problems else 'review: ' + '; '.join(problems)))
    return 0


def cmd_note(a):
    load_run(a.run)
    journal(a.run, 'note', text=a.text)
    print('noted')


def cmd_journal(a):
    file = path(a.run, 'journal.jsonl')
    if os.path.exists(file):
        with open(file, encoding='utf-8') as f:
            for line in f.readlines()[-a.tail:]:
                print(line.rstrip())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='command', required=True)

    def add(name, fn, help_, run=True):
        p = sub.add_parser(name, help=help_)
        if run:
            p.add_argument('--run', required=True, help='run directory')
        p.set_defaults(fn=fn)
        return p

    p = add('init', cmd_init, 'create a run directory from the designer input')
    for k in ('start', 'end', 'normal', 'hard', 'exhard'):
        p.add_argument(f'--{k}', type=int, required=True)
    p.add_argument('--seed', help='default: derived from the input')
    p.add_argument('--stack-rate', type=float, help='override: probability that a level stacks (0-1)')
    p.add_argument('--motion-rate', type=float, help='override: probability of moving vehicles (0-1)')
    p.add_argument('--force', action='store_true', help='overwrite an existing run.json')
    add('generate', cmd_generate, 'generate the batch, score it, draw the charts and list the flags')
    p = add('level', cmd_level, 'print one level: parameters, basis, checks, scores, flags')
    p.add_argument('--level', type=int, required=True)
    p = add('reroll', cmd_reroll, 'draw one level again (other levels keep theirs) and regenerate')
    p.add_argument('--level', type=int, required=True)
    p.add_argument('--undo', action='store_true', help='go back one draw')
    p.add_argument('--reason')
    p = add('weights', cmd_weights, 'change composite weights (KEY=int) and regenerate the scores and charts')
    p.add_argument('pairs', nargs='*', metavar='KEY=W')
    p.add_argument('--reset', action='store_true', help='back to the default weights first')
    p.add_argument('--reason')
    p = add('review-template', cmd_review_template, 'write review.json for the current parameters and flags')
    p.add_argument('--force', action='store_true')
    add('finalize', cmd_finalize, 'check everything and copy the accepted batch to <run>/final')
    p = add('check', cmd_check, 'recheck a level-params.json')
    p.add_argument('--params', help='default: <run>/level-params.json')
    add('status', cmd_status, 'show the run state and the next step')
    p = add('note', cmd_note, 'record a decision or observation in the journal')
    p.add_argument('text')
    p = add('journal', cmd_journal, 'read back the journal')
    p.add_argument('--tail', type=int, default=30)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == '__main__':
    sys.exit(main())
