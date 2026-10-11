import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import params_harness as h  # noqa: E402
import pressure  # noqa: E402


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = h.main([str(a) for a in argv])
    return code, out.getvalue()


def fill_review(r, decision='keep', reason='checked against the competitor curve'):
    p = r / 'review.json'
    review = json.loads(p.read_text())
    review['charts_viewed'] = True
    for f in review['flags'] + review['batch_flags']:
        f['decision'], f['reason'] = decision, reason
    p.write_text(json.dumps(review, ensure_ascii=False))


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.r = Path(tempfile.mkdtemp()) / 'run'
        run('init', '--run', self.r, '--start', 101, '--end', 130, '--normal', 21, '--hard', 6, '--exhard', 3)

    def test_init_rejects_wrong_counts(self):
        with self.assertRaises(SystemExit) as e:
            run('init', '--run', Path(tempfile.mkdtemp()) / 'x', '--start', 1, '--end', 10,
                '--normal', 5, '--hard', 3, '--exhard', 1)
        self.assertIn('has 10 levels', str(e.exception))

    def test_generate_writes_params_scores_and_charts(self):
        code, out = run('generate', '--run', self.r)
        self.assertEqual(code, 0, out)
        for name in ('level-params.json', 'level-params.csv', 'scores.json', 'review/curve.png',
                     'review/pressures.png', 'review/summary.md', 'journal.jsonl'):
            self.assertTrue((self.r / name).exists(), name)
        scores = json.loads((self.r / 'scores.json').read_text())
        self.assertEqual(len(scores['levels']), 30)
        for f in scores['flags']:
            self.assertIn(f['rule'], ('range', 'jump'))

    def test_composite_matches_the_old_page_formula(self):
        base = {'N': {'p10': 100, 'p90': 300}}
        self.assertEqual(pressure.score('N', 50, base), 0)
        self.assertEqual(pressure.score('N', 200, base), 50)
        self.assertEqual(pressure.score('N', 900, base), 100)
        base2 = {k: {'p10': 0, 'p90': 100} for k in pressure.KEYS}
        raw = {k: None for k in pressure.KEYS} | {'N': 40, 'K': 80}
        self.assertAlmostEqual(pressure.composite(raw, base2, {'N': 20, 'K': 10}), (40 * 20 + 80 * 10) / 30)

    def test_reroll_changes_one_level_and_undo_restores(self):
        run('generate', '--run', self.r)
        before = json.loads((self.r / 'level-params.json').read_text())['levels']
        sha0 = json.loads((self.r / 'run.json').read_text())['params_sha256']
        run('reroll', '--run', self.r, '--level', 110, '--reason', 'test')
        after = json.loads((self.r / 'level-params.json').read_text())['levels']
        changed = [a['level'] for a, b in zip(before, after) if a != b]
        self.assertIn(110, changed)
        self.assertTrue(all(lv >= 110 for lv in changed))   # later levels may change: forms run in sequence
        run('reroll', '--run', self.r, '--level', 110, '--undo')
        self.assertEqual(json.loads((self.r / 'run.json').read_text())['params_sha256'], sha0)
        with self.assertRaises(SystemExit):
            run('reroll', '--run', self.r, '--level', 110, '--undo')

    def test_finalize_needs_a_complete_review_of_the_current_params(self):
        run('generate', '--run', self.r)
        code, out = run('finalize', '--run', self.r)
        self.assertEqual(code, 1)
        self.assertIn('review.json is missing', out)
        run('review-template', '--run', self.r)
        fill_review(self.r, reason='')
        code, out = run('finalize', '--run', self.r)
        scores = json.loads((self.r / 'scores.json').read_text())
        if scores['flags'] or scores['batch_flags']:
            self.assertEqual(code, 1)
            self.assertIn('reason is empty', out)
        fill_review(self.r)
        code, out = run('finalize', '--run', self.r)
        self.assertEqual(code, 0, out)
        final = json.loads((self.r / 'final' / 'level-params.json').read_text())
        self.assertEqual(final['schema'], 'tmc-level-params.v1')
        self.assertEqual(set(final['harness']), {'weights', 'rerolls', 'review', 'finalized_at'})
        self.assertTrue((self.r / 'final' / 'curve.png').exists())

    def test_reroll_or_weights_make_the_review_stale(self):
        run('generate', '--run', self.r)
        run('review-template', '--run', self.r)
        fill_review(self.r)
        run('weights', '--run', self.r, 'N=40')
        code, out = run('finalize', '--run', self.r)
        self.assertEqual(code, 1)
        self.assertIn('other weights', out)
        run('weights', '--run', self.r, '--reset')
        run('reroll', '--run', self.r, '--level', 105)
        code, out = run('finalize', '--run', self.r)
        self.assertEqual(code, 1)
        self.assertIn('other parameters', out)

    def test_bad_weight_is_rejected(self):
        run('generate', '--run', self.r)
        with self.assertRaises(SystemExit):
            run('weights', '--run', self.r, 'X=5')


if __name__ == '__main__':
    unittest.main()
