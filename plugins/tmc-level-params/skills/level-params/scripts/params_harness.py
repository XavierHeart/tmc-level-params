#!/usr/bin/env python3
"""Level-parameter harness: deterministic steps around generate.py, driven by the agent.

    level-params-harness init --run <dir> --item 月球基地:3 --item 冰雪小镇:5   # or --batch batch.csv
    level-params-harness generate --run <dir>
    level-params-harness show --run <dir> [--index 2]    # every parameter of every level
    level-params-harness overview --run <dir>             # picture: the batch's difficulty
    level-params-harness check|status|journal|note --run <dir>

One input line is one level: a theme and a difficulty tier (1 easiest .. 5
hardest). The harness generates and checks the parameters; the agent collects
the input, runs the steps and reports the result.
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import generate  # noqa: E402

RUN_SCHEMA = 'level-params-run.v2'

_MODEL = None


def model():
    global _MODEL
    if _MODEL is None:
        _MODEL = generate.Model(generate.load_model(os.path.join(HERE, 'model.json')))
    return _MODEL


# ---------- run directory ----------

def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def path(run, *parts):
    return os.path.join(run, *parts)


def load(file):
    if not os.path.exists(file):
        raise SystemExit(f'error: missing {file}')
    with open(file, encoding='utf-8') as f:
        return json.load(f)


def save(file, data):
    os.makedirs(os.path.dirname(file) or '.', exist_ok=True)
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
        raise SystemExit(f'error: {run}/run.json is not {RUN_SCHEMA} (start a new run directory)')
    return state


def load_result(run):
    result = load(path(run, 'level-params.json'))
    if result.get('model_sha256') != model().sha256:
        raise SystemExit('error: level-params.json was made with another model.json: run generate again')
    return result


def sha(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ---------- commands ----------

def cmd_init(a):
    if os.path.exists(path(a.run, 'run.json')) and not a.force:
        raise SystemExit(f'error: {a.run} already has run.json (use --force to start over)')
    try:
        items = (generate.read_batch(a.batch) if a.batch else []) + [generate.parse_item(x) for x in a.item]
        generate.check_items(items)
    except (ValueError, OSError) as e:
        raise SystemExit(f'error: {e}')
    os.makedirs(a.run, exist_ok=True)
    state = {'schema': RUN_SCHEMA, 'created_at': now(), 'input': {'items': items, 'seed': a.seed},
             'model_sha256': model().sha256}
    save(path(a.run, 'run.json'), state)
    journal(a.run, 'init', input=state['input'])
    print(f"initialized {a.run}: {len(items)} level(s): " + ', '.join(f"{i['theme']}:{i['tier']}" for i in items))
    print('next: generate')


def cmd_generate(a):
    state = load_run(a.run)
    try:
        result = generate.generate(model(), state['input']['items'], state['input']['seed'])
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f'error: {e}')
    paths = generate.write_outputs(result, a.run)
    state['params_sha256'] = sha(result['levels'])
    state['generated_at'] = now()
    save(path(a.run, 'run.json'), state)
    failed = [k for k, v in result['checks'].items() if not v]
    journal(a.run, 'generate', params_sha256=state['params_sha256'], failed=failed)
    print(f"{len(result['levels'])} level(s), checks: {'all passed' if not failed else 'FAILED ' + ', '.join(failed)}")
    for r in result['levels']:
        print(f"  {r['index']}. {r['theme']} tier {r['tier']}: score {r['score']:+.2f}, T {r['T']}, U {r['U']}, "
              f"targets {len(r['targets'])}, stacking {'yes' if r['stacking']['on'] else 'no'}, "
              f"motion {'yes' if r['motion'] else 'no'}, duration {r['duration_s']}s")
    for theme, x in result['assets'].items():
        print(f"  assets {theme} (tiers {', '.join(map(str, x['tiers']))}): {x['total']} kinds = "
              + ', '.join(f'{c} {n}' for c, n in x['kinds'].items()))
    for name, p in paths.items():
        print(f'  {name}: {p}')
    return 1 if failed else 0


def show_level(r, p):
    t = dict(zip(generate.COLUMNS, generate.table_row(r)))
    b = r['basis']
    failed = [k for k, v in r['checks'].items() if not v]
    lines = [
        f"【第 {r['index']} 关】{r['theme']} · 第 {r['tier']} 档",
        f"  难度分      {r['score']:+.4f}（竞品全体排位 {p['percentile_overall']}%，本档排位 {p['percentile_in_tier']}%）",
        f"  联合形态    {r['form']} {r['form_description']}",
        f"  物件        {r['T']} 个，{r['U']} 种",
        f"  四类        {t['各类物件明细']}",
        f"  每种数量    {t['每种物件数量']}",
        f"  目标        {len(r['targets'])} 种，共 {t['目标总数量']} 个：{t['目标种类明细']}",
        f"  叠放        {t['叠放类型']}" + (f"，{t['叠放套数总计']} 套：{t['叠放种类套数明细']}" if r['stacking']['on'] else ''),
        f"  运动载具    {t['是否包含运动载具']}",
        f"  时长        {t['时长']}（{r['duration_s']} 秒）",
        f"  依据        T/U 取自 {b['TU_source']}（{b['T_base']}/{b['U_base']}，变化 {t['变异量T/U']}）；"
        f"四类拆分取自 {b['split_source']}" + (f"；叠放结构取自 {b['stack_source']}" if b.get('stack_source') else ''),
        f"  验算        {'全部通过' if not failed else '失败：' + '、'.join(failed)}",
    ]
    return '\n'.join(lines)


def cmd_show(a):
    import overview
    result = load_result(a.run)
    levels = result['levels']
    if a.index:
        wanted = set(a.index)
        missing = wanted - {r['index'] for r in levels}
        if missing:
            raise SystemExit(f"error: no level {sorted(missing)}; this run has 1-{len(levels)}")
        levels = [r for r in levels if r['index'] in wanted]
    pos = {p['index']: p for p in overview.positions(model(), levels)}
    print('\n\n'.join(show_level(r, pos[r['index']]) for r in levels))
    if not a.index:
        print('\n【素材清单】（同一主题的各关共用占位名）')
        for theme, x in result['assets'].items():
            print(f"  {theme}（第 {'、'.join(map(str, x['tiers']))} 档）：建筑 {x['kinds']['building']}、"
                  f"道具 {x['kinds']['prop']}、载具 {x['kinds']['vehicle']}、角色 {x['kinds']['character']}，"
                  f"共 {x['total']} 种")
        failed = [k for k, v in result['checks'].items() if not v]
        print(f"\n【整体验算】{'全部通过' if not failed else '失败：' + '、'.join(failed)}")


def cmd_overview(a):
    import overview
    result = load_result(a.run)
    out = path(a.run, 'overview.png')
    pos = overview.draw(model(), result, out)
    journal(a.run, 'overview', positions=pos)
    for p in pos:
        print(f"  {p['index']}. {p['theme']} tier {p['tier']}: score {p['score']:+.2f}, above "
              f"{p['percentile_overall']}% of all competitor levels and {p['percentile_in_tier']}% of its tier")
    print(f'  picture: {out}')


def cmd_check(a):
    failures = generate.recheck(a.params or path(a.run, 'level-params.json'))
    for f in failures:
        print(f)
    print('ok' if not failures else f'{len(failures)} failure(s)')
    return 1 if failures else 0


def cmd_status(a):
    state = load_run(a.run)
    i = state['input']
    print(f"{len(i['items'])} level(s): " + ', '.join(f"{x['theme']}:{x['tier']}" for x in i['items'])
          + (f"; seed {i['seed']}" if i['seed'] else ''))
    print(f"generated at {state['generated_at']}" if 'generated_at' in state else 'next: generate')


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

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument('--run', required=True, help='run directory')
        p.set_defaults(fn=fn)
        return p

    p = add('init', cmd_init, 'create a run directory for a batch of theme:tier inputs')
    p.add_argument('--item', action='append', default=[], metavar='THEME:TIER', help='one level; repeatable')
    p.add_argument('--batch', help='CSV with a header row and columns 主题,档位 (or theme,tier)')
    p.add_argument('--seed', help='optional; changes every draw of this batch (default: the theme name decides)')
    p.add_argument('--force', action='store_true', help='overwrite an existing run.json')
    add('generate', cmd_generate, 'generate and check every level, write level-params.json/csv/xlsx')
    p = add('show', cmd_show, 'print every parameter of every level (or of --index N)')
    p.add_argument('--index', type=int, action='append', help='level number in the batch; repeatable')
    add('overview', cmd_overview, "draw overview.png: the batch's difficulty against the competitor (needs matplotlib)")
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
