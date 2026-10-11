import copy, json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import generate  # noqa: E402
import learn  # noqa: E402
import pressure  # noqa: E402

MODEL = generate.Model(generate.load_model(HERE / 'model.json'))
BATCH = {'Normal': 35, 'Hard': 10, 'ExHard': 5}


def run(start=101, end=150, counts=BATCH, seed=20261009, rerolls=None, **overrides):
    return generate.generate(MODEL, start, end, dict(counts), seed, overrides, rerolls)


class LevelParamsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run()

    def test_reference_batch_passes_every_check(self):
        self.assertTrue(all(self.result['batch_checks'].values()), self.result['batch_checks'])
        seq = [r['difficulty'] for r in self.result['levels']]
        self.assertEqual([seq.count(d) for d in generate.DIFFICULTIES], [35, 10, 5])
        self.assertEqual([r['level'] for r in self.result['levels']], list(range(101, 151)))

    def test_same_seed_same_result(self):
        self.assertEqual(json.dumps(run(), sort_keys=True), json.dumps(self.result, sort_keys=True))
        self.assertNotEqual(json.dumps(run(seed=7), sort_keys=True), json.dumps(self.result, sort_keys=True))

    def test_count_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            run(start=100)

    def test_other_ranges(self):
        for start, end, counts in [(1, 20, {'Normal': 18, 'Hard': 2, 'ExHard': 0}),
                                   (1001, 1100, {'Normal': 70, 'Hard': 20, 'ExHard': 10}),
                                   (3001, 3030, {'Normal': 20, 'Hard': 6, 'ExHard': 4})]:
            res = run(start, end, counts, seed='range')
            self.assertTrue(all(res['batch_checks'].values()), (start, res['batch_checks']))

    def test_level_rules(self):
        for row in self.result['levels']:
            self.assertEqual(sum(c['kinds'] for c in row['categories'].values()), row['U'])
            self.assertEqual(sum(c['qty'] for c in row['categories'].values()), row['T'])
            self.assertTrue(all(v % 3 == 0 for v in row['per_kind'].values()))
            for t in row['targets']:
                self.assertEqual(row['per_kind'][t['kind']], t['count'])
            if row['motion']:
                self.assertGreaterEqual(row['categories']['vehicle']['kinds'], 1)
            for c in row['stacking']['combos']:
                self.assertEqual(c['sets'] % 3, 0)
                self.assertEqual(len(c['kinds']), row['stacking']['layers'])

    def test_checks_catch_broken_rows(self):
        row = self.result['levels'][0]
        broken = copy.deepcopy(row); k = next(iter(broken['per_kind'])); broken['per_kind'][k] += 1
        self.assertFalse(generate.check_level(broken)['数量3倍数'])
        broken = copy.deepcopy(row); broken['targets'][0]['count'] += 3
        self.assertFalse(generate.check_level(broken)['目标明细'])
        broken = copy.deepcopy(row); broken['motion'] = True; broken['categories']['vehicle']['kinds'] = 0
        self.assertFalse(generate.check_level(broken)['运动前提'])
        broken = copy.deepcopy(row)
        broken['stacking'] = {'on': True, 'layers': 2, 'combos': [{'kinds': [k, k], 'sets': 4}]}
        self.assertFalse(generate.check_level(broken)['叠放3倍数'])

    def test_no_adjacent_exhard(self):
        for seed in range(10):
            seq = generate.difficulty_sequence(1, 50, BATCH, generate.random.Random(seed))
            self.assertFalse(any(a == b == 'ExHard' for a, b in zip(seq, seq[1:])))

    def test_mechanism_overrides(self):
        res = run(seed='ovr', stack_rate=0.0, motion_rate=1.0)
        self.assertFalse(any(r['stacking']['on'] for r in res['levels']))
        self.assertTrue(all(r['motion'] for r in res['levels']))

    def test_reroll_changes_only_that_level(self):
        res = run(rerolls={'120': 1})
        self.assertEqual(res['input']['rerolls'], {'120': 1})
        changed = [a['level'] for a, b in zip(self.result['levels'], res['levels'])
                   if generate.signature(a) != generate.signature(b)]
        self.assertIn(120, changed)
        self.assertLessEqual(len(changed), 3, changed)  # later levels move only if the form-run rule fires

    def test_pressure_fields(self):
        for row in self.result['levels']:
            self.assertEqual(set(row['pressure']), set(pressure.KEYS))
            self.assertEqual(row['pressure']['N'], row['T'])
            self.assertEqual(row['pressure']['M'] == 0, not row['motion'] or row['pressure']['M'] == 0)
        base = pressure.baselines(MODEL.levels, 9)
        self.assertTrue(all(base[k]['p90'] > base[k]['p10'] for k in pressure.KEYS))

    def test_cli_writes_and_rechecks(self):
        with tempfile.TemporaryDirectory() as out:
            cmd = [sys.executable, str(HERE / 'generate.py'), '--start', '101', '--end', '150', '--normal', '35',
                   '--hard', '10', '--exhard', '5', '--seed', '1', '--out', out]
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            for name in ('level-params.json', 'level-params.csv'):
                self.assertTrue((Path(out) / name).is_file())
            check = subprocess.run([sys.executable, str(HERE / 'generate.py'), 'check', '--params',
                                    str(Path(out) / 'level-params.json')], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout)

    def test_categorize(self):
        cases = {'building_viking_hay': 'building', 'Buildings_Toy_TreeSimple_B': 'building',
                 'human_alice_alice_v3_CO': 'character', 'animal_chicken': 'character',
                 'animals0012_color_brown_CO': 'character', 'viking_vehicles_cart': 'vehicle',
                 'pirate_ship_green_CO': 'vehicle', 'db_candy_creamwhip_01': 'prop',
                 'nc_cocoween_TreePine_01': 'decor', 'frame0007': None, '': None}
        for name, cat in cases.items():
            self.assertEqual(learn.categorize(name), cat, name)


if __name__ == '__main__':
    unittest.main(verbosity=1)
