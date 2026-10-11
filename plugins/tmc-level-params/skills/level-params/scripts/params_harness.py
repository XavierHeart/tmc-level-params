#!/usr/bin/env python3
"""Level-parameter harness: deterministic steps around generate.py, driven by the agent.

    level-params-harness init --run <dir> --theme 月球基地
    level-params-harness generate --run <dir>
    level-params-harness tier --run <dir> --tier 3
    level-params-harness overview --run <dir>       # picture: where the 5 sets sit in the competitor range
    level-params-harness check|status|journal|note --run <dir>

The harness generates and checks one parameter set per difficulty tier; the
agent collects the theme name, runs the steps and reports the result.
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

RUN_SCHEMA = 'level-params-run.v1'

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


def load(file, default=None):
    if not os.path.exists(file):
        if default is not None:
            return default
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
        raise SystemExit(f'error: {run}/run.json is not {RUN_SCHEMA}')
    return state


def sha(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ---------- commands ----------

def cmd_init(a):
    if os.path.exists(path(a.run, 'run.json')) and not a.force:
        raise SystemExit(f'error: {a.run} already has run.json (use --force to start over)')
    theme = a.theme.strip()
    if not theme:
        raise SystemExit('error: --theme must not be empty')
    os.makedirs(a.run, exist_ok=True)
    state = {'schema': RUN_SCHEMA, 'created_at': now(),
             'input': {'theme': theme, 'seed': a.seed or theme},
             'model_sha256': model().sha256}
    save(path(a.run, 'run.json'), state)
    journal(a.run, 'init', input=state['input'])
    print(f'initialized {a.run}; next: generate')


def cmd_generate(a):
    state = load_run(a.run)
    i = state['input']
    try:
        result = generate.generate(model(), i['theme'], i['seed'])
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f'error: {e}')
    paths = generate.write_outputs(result, a.run)
    state['params_sha256'] = sha(result['tiers'])
    state['generated_at'] = now()
    save(path(a.run, 'run.json'), state)
    failed = [k for k, v in result['checks'].items() if not v]
    journal(a.run, 'generate', params_sha256=state['params_sha256'], failed=failed)
    print(f"{result['theme']}: {len(result['tiers'])} tiers, checks: "
          f"{'all passed' if not failed else 'FAILED ' + ', '.join(failed)}")
    for r in result['tiers']:
        print(f"  tier {r['tier']}: score {r['score']}, T {r['T']}, U {r['U']}, targets {len(r['targets'])}, "
              f"stacking {'yes' if r['stacking']['on'] else 'no'}, motion {'yes' if r['motion'] else 'no'}, "
              f"duration {r['duration_s']}s")
    k = result['assets']['kinds']
    print(f"  assets: {result['assets']['total']} kinds = " + ', '.join(f'{c} {n}' for c, n in k.items()))
    for name, p in paths.items():
        print(f'  {name}: {p}')
    return 1 if failed else 0


def cmd_tier(a):
    result = load(path(a.run, 'level-params.json'))
    row = next((r for r in result['tiers'] if r['tier'] == a.tier), None)
    if row is None:
        raise SystemExit(f'error: tier {a.tier} is not in this result')
    t = dict(zip(generate.COLUMNS, generate.table_row(row)))
    b = row['basis']
    print(f"tier {row['tier']}  score {row['score']}  form {row['form']} ({row['form_description']})")
    print(f"  objects {row['T']}, kinds {row['U']}; {t['各类物件明细']}")
    print(f"  per kind: {'、'.join(f'{k}×{v}' for k, v in row['per_kind'].items())}")
    print(f"  targets {len(row['targets'])} kinds, {t['目标总数量']} in total: {t['目标种类明细']}")
    print(f"  stacking {t['叠放类型']}: {t['叠放种类套数明细']}; moving vehicles {t['是否包含运动载具']}; "
          f"duration {t['时长']}")
    print(f"  basis: T/U from {b['TU_source']} ({b['T_base']}/{b['U_base']}, change {t['变异量T/U']}); "
          f"split from {b['split_source']}" + (f"; stacking from {b['stack_source']}" if b.get('stack_source') else ''))
    failed = [k for k, v in row['checks'].items() if not v]
    print(f"  checks: {'all passed' if not failed else 'FAILED ' + ', '.join(failed)}")


def cmd_overview(a):
    import overview
    result = load(path(a.run, 'level-params.json'))
    if result.get('model_sha256') != model().sha256:
        raise SystemExit('error: level-params.json was made with another model.json: run generate again')
    out = path(a.run, 'overview.png')
    positions = overview.draw(model(), result, out)
    journal(a.run, 'overview', positions=positions)
    for p in positions:
        print(f"  tier {p['tier']}: score {p['score']:+.2f}, above {p['percentile_overall']}% of all competitor "
              f"levels and {p['percentile_in_tier']}% of its tier")
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
    print(f"theme {i['theme']} seed {i['seed']}")
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

    p = add('init', cmd_init, 'create a run directory for one theme')
    p.add_argument('--theme', required=True, help='theme name')
    p.add_argument('--seed', help='default: the theme name')
    p.add_argument('--force', action='store_true', help='overwrite an existing run.json')
    add('generate', cmd_generate, 'generate and check one parameter set per tier, write level-params.json/csv/xlsx')
    p = add('tier', cmd_tier, 'print one tier: parameters, basis, checks')
    p.add_argument('--tier', type=int, required=True)
    add('overview', cmd_overview, 'draw overview.png: the 5 sets against the competitor tiers (needs matplotlib)')
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
