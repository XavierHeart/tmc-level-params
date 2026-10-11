import contextlib, copy, io, json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import generate  # noqa: E402
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


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {t: generate.generate(MODEL, t) for t in THEMES}

    def test_every_theme_passes_every_check(self):
        for theme, r in self.results.items():
            self.assertTrue(all(r['checks'].values()), (theme, r['checks']))
            for row in r['tiers']:
                self.assertTrue(all(row['checks'].values()), (theme, row['tier'], row['checks']))

    def test_one_set_per_tier_and_scores_inside_their_tier(self):
        for r in self.results.values():
            self.assertEqual([x['tier'] for x in r['tiers']], [1, 2, 3, 4, 5])
            for row in r['tiers']:
                lo, hi = MODEL.bounds(row['tier'])
                self.assertTrue((lo is None or row['score'] >= lo) and (hi is None or row['score'] < hi))

    def test_same_theme_same_result_other_theme_differs(self):
        self.assertEqual(generate.generate(MODEL, '月球基地'), self.results['月球基地'])
        self.assertNotEqual(self.results['月球基地']['tiers'], self.results['巨木森林']['tiers'])
        self.assertNotEqual(generate.generate(MODEL, '月球基地', seed='x')['tiers'], self.results['月球基地']['tiers'])

    def test_asset_list_covers_every_tier(self):
        for r in self.results.values():
            a = r['assets']
            self.assertEqual(a['total'], len(a['names']))
            for row in r['tiers']:
                for c in generate.CATEGORIES:
                    self.assertLessEqual(row['categories'][c]['kinds'], a['kinds'][c])
                    names = [k for k in row['per_kind'] if k.startswith(generate.ZH[c])]
                    self.assertEqual(sorted(names), sorted(f'{generate.ZH[c]}{i}' for i in range(1, len(names) + 1)))

    def test_level_rules(self):
        for r in self.results.values():
            for row in r['tiers']:
                self.assertEqual(row['T'] % 3, 0)
                self.assertEqual(sum(row['per_kind'].values()), row['T'])
                self.assertEqual(len(row['per_kind']), row['U'])
                for t in row['targets']:
                    self.assertEqual(row['per_kind'][t['kind']], t['count'])
                if row['motion']:
                    self.assertGreaterEqual(row['categories']['vehicle']['kinds'], 1)

    def test_checks_catch_broken_rows(self):
        tm = self.results['月球基地']['tier_model']
        row = copy.deepcopy(self.results['月球基地']['tiers'][2])
        row['T'] += 1
        self.assertFalse(generate.check_tier(row, tm)['数量3倍数'])
        row = copy.deepcopy(self.results['月球基地']['tiers'][2])
        row['tier'] = 5
        self.assertFalse(generate.check_tier(row, tm)['难度分在档内'])
        row = copy.deepcopy(self.results['月球基地']['tiers'][2])
        row['duration_s'] += 300           # changes the score, so the stored score no longer matches
        self.assertFalse(generate.check_tier(row, tm)['难度分在档内'])

    def test_empty_theme_is_rejected(self):
        with self.assertRaises(ValueError):
            generate.generate(MODEL, '  ')

    def test_cli_writes_and_rechecks(self):
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run([sys.executable, str(HERE / 'generate.py'), '--theme', '月球基地', '--out', d],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            check = subprocess.run([sys.executable, str(HERE / 'generate.py'), 'check', '--params',
                                    str(Path(d) / 'level-params.json')], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout)
            data = json.loads((Path(d) / 'level-params.json').read_text())
            data['tiers'][0]['per_kind']['建筑1'] += 3
            (Path(d) / 'bad.json').write_text(json.dumps(data, ensure_ascii=False))
            self.assertNotEqual(generate.recheck(str(Path(d) / 'bad.json')), [])


class HarnessTests(unittest.TestCase):
    def run_h(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = params_harness.main([str(a) for a in argv])
        return code, out.getvalue()

    def test_init_generate_tier_check(self):
        r = Path(tempfile.mkdtemp()) / 'run'
        self.assertEqual(self.run_h('init', '--run', r, '--theme', '月球基地')[0], 0)
        code, out = self.run_h('generate', '--run', r)
        self.assertEqual(code, 0, out)
        self.assertIn('assets:', out)
        self.assertIn('tier 5', self.run_h('tier', '--run', r, '--tier', 5)[1])
        self.assertEqual(self.run_h('check', '--run', r)[0], 0)
        with self.assertRaises(SystemExit):
            self.run_h('init', '--run', r, '--theme', '巨木森林')       # no silent overwrite
        with self.assertRaises(SystemExit):
            self.run_h('init', '--run', Path(tempfile.mkdtemp()) / 'x', '--theme', ' ')


if __name__ == '__main__':
    unittest.main()
