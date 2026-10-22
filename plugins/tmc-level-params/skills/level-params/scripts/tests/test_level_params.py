import contextlib, copy, io, json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import generate  # noqa: E402
import names  # noqa: E402
import params_harness  # noqa: E402
import tiers  # noqa: E402

MODEL = generate.Model(generate.load_model(HERE / 'model.json'))
THEMES = ['月球基地', '巨木森林', '海底城市', '冰雪小镇', 'Candy City']


class TierModelTests(unittest.TestCase):
    def test_five_equal_tiers_ranked_by_learned_score(self):
        t = MODEL.tiers
        self.assertEqual(len(t['cuts']), tiers.TIERS - 1)
        self.assertEqual(t['cuts'], sorted(t['cuts']))
        sizes = [len(MODEL.pool[k]) for k in range(1, tiers.TIERS + 1)]
        self.assertEqual(sum(sizes), len(MODEL.levels))
        self.assertLessEqual(max(sizes) - min(sizes), 5, sizes)

    def test_learned_score_separates_competitor_labels(self):
        cv = MODEL.tiers['cross_validation']
        self.assertGreater(cv['auc_hard_or_exhard_vs_normal'], 0.75)
        self.assertGreater(cv['auc_exhard_vs_hard'], 0.5)
        share = [sum(r['difficulty'] != 'Normal' for r in MODEL.pool[k]) / len(MODEL.pool[k])
                 for k in range(1, tiers.TIERS + 1)]
        self.assertEqual(share, sorted(share), share)      # more Hard/ExHard in every higher tier

    def test_stored_composition_matches_generation_tiers(self):
        import collections
        comp = {str(t): dict(sorted(collections.Counter(r['difficulty'] for r in MODEL.pool[t]).items()))
                for t in MODEL.pool}
        self.assertEqual(comp, MODEL.tiers['composition'])

    def test_learning_is_deterministic(self):
        self.assertEqual(tiers.learn(MODEL.levels[:600]), tiers.learn(MODEL.levels[:600]))

    def test_auc(self):
        self.assertEqual(tiers.auc([1, 2, 3, 4], [0, 0, 1, 1]), 1.0)
        self.assertEqual(tiers.auc([1, 1], [0, 1]), 0.5)


ITEMS = [{'theme': t, 'tier': k} for t in ['月球基地', '巨木森林', '海底城市', '冰雪小镇', 'Candy City'] for k in range(1, 6)]


class BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = generate.generate(MODEL, ITEMS)
        cls.levels = cls.result['levels']

    def test_every_level_passes_every_check(self):
        self.assertTrue(all(self.result['checks'].values()), self.result['checks'])
        for row in self.levels:
            self.assertTrue(all(row['checks'].values()), (row['theme'], row['tier'], row['checks']))

    def test_one_level_per_input_in_order_and_inside_its_tier(self):
        self.assertEqual([(r['theme'], r['tier']) for r in self.levels], [(i['theme'], i['tier']) for i in ITEMS])
        self.assertEqual([r['index'] for r in self.levels], list(range(1, len(ITEMS) + 1)))
        for row in self.levels:
            lo, hi = MODEL.bounds(row['tier'])
            self.assertTrue((lo is None or row['score'] >= lo) and (hi is None or row['score'] < hi))

    def test_a_level_depends_only_on_theme_tier_and_seed(self):
        alone = generate.generate(MODEL, [{'theme': '月球基地', 'tier': 3}])['levels'][0]
        in_batch = next(r for r in self.levels if (r['theme'], r['tier']) == ('月球基地', 3))
        self.assertEqual({k: v for k, v in alone.items() if k != 'index'},
                         {k: v for k, v in in_batch.items() if k != 'index'})
        other = generate.generate(MODEL, [{'theme': '月球基地', 'tier': 3}], seed='x')['levels'][0]
        self.assertNotEqual(other['per_kind'], alone['per_kind'])

    def test_tiers_of_one_theme_never_repeat_parameters(self):
        for theme in {i['theme'] for i in ITEMS}:
            rows = [r for r in self.levels if r['theme'] == theme]
            self.assertEqual(len({generate.signature(r) for r in rows}), len(rows))

    def test_asset_list_per_theme_covers_its_levels(self):
        for theme, a in self.result['assets'].items():
            self.assertEqual(a['total'], len(a['names']))
            rows = [r for r in self.levels if r['theme'] == theme]
            self.assertEqual(a['tiers'], sorted(r['tier'] for r in rows))
            for row in rows:
                for c in generate.CATEGORIES:
                    self.assertLessEqual(row['categories'][c]['kinds'], a['kinds'][c])
                    names = [k for k in row['per_kind'] if k.startswith(generate.ZH[c])]
                    self.assertEqual(sorted(names), sorted(f'{generate.ZH[c]}{i}' for i in range(1, len(names) + 1)))

    def test_counts_are_half_of_the_competitor_tier(self):
        # 2026-10-22: our sprites are bigger, so T, U and stacking sets are halved; the score uses T/U ÷ 0.5.
        import statistics
        for row in self.levels:
            self.assertEqual(row['count_scale'], generate.COUNT_SCALE)
            pool = MODEL.pool[row['tier']]
            self.assertLessEqual(row['T'], max(r['T'] for r in pool) * generate.COUNT_SCALE + 4.5)
            self.assertLessEqual(row['U'], max(r['U'] for r in pool) * generate.COUNT_SCALE + 1)
            feats = tiers.row_features(row)
            self.assertAlmostEqual(feats['U'], row['U'] / generate.COUNT_SCALE)
        for tier in range(1, 6):
            rows = [generate.generate_level(MODEL, f'主题{i}', tier) for i in range(15)]
            median_t = statistics.median(r['T'] for r in rows)
            pool_t = statistics.median(r['T'] for r in MODEL.pool[tier])
            self.assertLess(abs(median_t - pool_t * generate.COUNT_SCALE), pool_t * 0.15, (tier, median_t, pool_t))

    def test_old_files_without_count_scale_still_check(self):
        row = copy.deepcopy(self.levels[0])
        old = copy.deepcopy(row)
        del old['count_scale']
        self.assertNotEqual(tiers.row_features(old)['U'], tiers.row_features(row)['U'])
        self.assertEqual(tiers.row_features(old)['U'], row['U'])

    def test_level_rules(self):
        for row in self.levels:
            self.assertEqual(row['T'] % 3, 0)
            self.assertEqual(sum(row['per_kind'].values()), row['T'])
            self.assertEqual(len(row['per_kind']), row['U'])
            for t in row['targets']:
                self.assertEqual(row['per_kind'][t['kind']], t['count'])
            if row['motion']:
                self.assertGreaterEqual(row['categories']['vehicle']['kinds'], 1)

    def test_checks_catch_broken_rows(self):
        tm = self.result['tier_model']
        row = copy.deepcopy(self.levels[2])
        row['T'] += 1
        self.assertFalse(generate.check_tier(row, tm)['数量3倍数'])
        row = copy.deepcopy(self.levels[2])
        row['tier'] = 5 if row['tier'] != 5 else 1
        self.assertFalse(generate.check_tier(row, tm)['难度分在档内'])
        row = copy.deepcopy(self.levels[2])
        row['duration_s'] += 300           # changes the score, so the stored score no longer matches
        self.assertFalse(generate.check_tier(row, tm)['难度分在档内'])

    def test_bad_input_is_rejected(self):
        for bad in ([], [{'theme': '月球基地', 'tier': 6}], [{'theme': '月球基地', 'tier': 0}],
                    [{'theme': '月球基地', 'tier': 2}, {'theme': '月球基地', 'tier': 2}]):
            with self.assertRaises(ValueError):
                generate.generate(MODEL, bad)
        for text in ('月球基地', ':3', '月球基地:三'):
            with self.assertRaises(ValueError):
                generate.parse_item(text)
        self.assertEqual(generate.parse_item(' 冰雪：小镇：4 '), {'theme': '冰雪:小镇', 'tier': 4})

    def test_batch_csv(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'b.csv'
            p.write_text('主题,档位\n月球基地,3\n\n冰雪小镇,5\n', encoding='utf-8-sig')
            self.assertEqual(generate.read_batch(str(p)), [{'theme': '月球基地', 'tier': 3}, {'theme': '冰雪小镇', 'tier': 5}])
            p.write_text('theme,tier\n月球基地,\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                generate.read_batch(str(p))

    def test_cli_writes_and_rechecks(self):
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run([sys.executable, str(HERE / 'generate.py'), '--item', '月球基地:3', '--item',
                                  '冰雪小镇:5', '--out', d], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            check = subprocess.run([sys.executable, str(HERE / 'generate.py'), 'check', '--params',
                                    str(Path(d) / 'level-params.json')], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout)
            data = json.loads((Path(d) / 'level-params.json').read_text())
            data['levels'][0]['per_kind']['建筑1'] += 3
            (Path(d) / 'bad.json').write_text(json.dumps(data, ensure_ascii=False))
            self.assertNotEqual(generate.recheck(str(Path(d) / 'bad.json')), [])


class HarnessTests(unittest.TestCase):
    def run_h(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = params_harness.main([str(a) for a in argv])
        return code, out.getvalue()

    def test_init_generate_show_overview_check(self):
        r = Path(tempfile.mkdtemp()) / 'run'
        self.assertEqual(self.run_h('init', '--run', r, '--item', '月球基地:3', '--item', '冰雪小镇:5')[0], 0)
        code, out = self.run_h('generate', '--run', r)
        self.assertEqual(code, 0, out)
        self.assertIn('assets 月球基地', out)
        out = self.run_h('show', '--run', r)[1]
        self.assertEqual(out.count('【第 '), 2)
        for word in ('每种数量', '目标', '叠放', '依据', '竞品全体排位', '【素材清单】', '【整体验算】全部通过'):
            self.assertIn(word, out)
        self.assertEqual(self.run_h('show', '--run', r, '--index', 2)[1].count('【第 '), 1)
        with self.assertRaises(SystemExit):
            self.run_h('show', '--run', r, '--index', 3)
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            matplotlib = None
        if matplotlib:
            code, out = self.run_h('overview', '--run', r)
            self.assertEqual(code, 0, out)
            self.assertTrue((r / 'overview.png').stat().st_size > 50_000)
            self.assertEqual(out.count('tier '), 2)
        self.assertEqual(self.run_h('check', '--run', r)[0], 0)
        with self.assertRaises(SystemExit):
            self.run_h('init', '--run', r, '--item', '巨木森林:1')       # no silent overwrite
        with self.assertRaises(SystemExit):
            self.run_h('init', '--run', Path(tempfile.mkdtemp()) / 'x', '--item', '月球基地:9')


def fill(path, prefix='名'):
    doc = json.loads(path.read_text())
    for theme, t in doc['themes'].items():
        for i, x in enumerate(t['slots'], 1):
            if not x['name']:
                x['name'], x['id'] = f'{prefix}{theme[:2]}{i}号', f"{x['category']}_{abs(hash(theme)) % 1000}_{i}"
    path.write_text(json.dumps(doc, ensure_ascii=False))
    return doc


class NamesTests(unittest.TestCase):
    def run_h(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = params_harness.main([str(a) for a in argv])
        return code, out.getvalue()

    def setUp(self):
        self.r = Path(tempfile.mkdtemp()) / 'run'
        self.run_h('init', '--run', self.r, '--item', '月球基地:1', '--item', '冰雪小镇:2', '--item', '月球基地:4')
        self.run_h('generate', '--run', self.r)

    def test_template_lists_every_slot_with_its_role(self):
        self.run_h('names-template', '--run', self.r)
        doc = json.loads((self.r / 'names.json').read_text())
        result = json.loads((self.r / 'level-params.json').read_text())
        for theme, a in result['assets'].items():
            self.assertEqual([x['slot'] for x in doc['themes'][theme]['slots']], a['names'])
        uses = ' '.join(u for t in doc['themes'].values() for x in t['slots'] for u in x['uses'])
        self.assertIn('目标', uses)
        stacked = [r for r in result['levels'] if r['stacking']['on']]
        if stacked:
            self.assertIn('叠放底座', uses)

    def test_apply_rejects_bad_names_and_writes_good_ones(self):
        self.run_h('names-template', '--run', self.r)
        p = self.r / 'names.json'
        doc = json.loads(p.read_text())
        slots = doc['themes']['月球基地']['slots']
        slots[0].update(name='建筑1', id='Bad-Id')
        slots[1].update(name='月面穹顶', id='lunar_dome')
        slots[2].update(name='月面穹顶', id='lunar_dome')
        p.write_text(json.dumps(doc, ensure_ascii=False))
        code, out = self.run_h('names-apply', '--run', self.r)
        self.assertEqual(code, 1)
        for word in ('still a placeholder', 'must be lowercase', 'repeats 建筑2', 'name is empty'):
            self.assertIn(word, out)
        slots[0].update(name='', id='')
        slots[2].update(name='', id='')
        p.write_text(json.dumps(doc, ensure_ascii=False))
        fill(p)
        code, out = self.run_h('names-apply', '--run', self.r)
        self.assertEqual(code, 0, out)
        result = json.loads((self.r / 'level-params.json').read_text())
        self.assertEqual(result['names']['月球基地']['建筑2']['name'], '月面穹顶')
        self.assertIn('月面穹顶（建筑2）', (self.r / 'level-params.csv').read_text(encoding='utf-8-sig'))
        self.assertIn('月面穹顶（建筑2）', self.run_h('show', '--run', self.r)[1])
        self.assertEqual(generate.recheck(str(self.r / 'level-params.json')), [])
        result['names']['月球基地']['建筑3']['name'] = '月面穹顶'
        (self.r / 'bad.json').write_text(json.dumps(result, ensure_ascii=False))
        self.assertTrue(any('repeats' in f for f in generate.recheck(str(self.r / 'bad.json'))))

    def test_regenerate_makes_names_stale_but_template_keeps_them(self):
        self.run_h('names-template', '--run', self.r)
        fill(self.r / 'names.json')
        self.run_h('names-apply', '--run', self.r)
        state = json.loads((self.r / 'run.json').read_text())
        state['input']['seed'] = 'other'
        (self.r / 'run.json').write_text(json.dumps(state, ensure_ascii=False))
        self.run_h('generate', '--run', self.r)
        code, out = self.run_h('names-apply', '--run', self.r)
        self.assertEqual(code, 1)
        self.assertIn('other parameters', out)
        self.run_h('names-template', '--run', self.r)
        doc = json.loads((self.r / 'names.json').read_text())
        kept = [x for t in doc['themes'].values() for x in t['slots'] if x['name']]
        self.assertTrue(kept)
        self.assertTrue(all(x['name'].startswith('名') for x in kept))

    def test_from_reuses_names_of_another_run(self):
        self.run_h('names-template', '--run', self.r)
        fill(self.r / 'names.json', prefix='旧')
        other = Path(tempfile.mkdtemp()) / 'run2'
        self.run_h('init', '--run', other, '--item', '月球基地:1')
        self.run_h('generate', '--run', other)
        self.run_h('names-template', '--run', other, '--from', self.r / 'names.json')
        doc = json.loads((other / 'names.json').read_text())
        self.assertTrue(all(x['name'].startswith('旧') for x in doc['themes']['月球基地']['slots']))


if __name__ == '__main__':
    unittest.main()
