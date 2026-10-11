#!/usr/bin/env python3
"""Local web page for the level-parameter generator.

    python3 scripts/viewer.py [--port 8770] [--out-root <dir>]

Open http://127.0.0.1:8770 . The page generates a batch, draws per-pressure
difficulty curves next to the competitor levels with the same numbers, lets
you re-weight the composite curve live, re-draw single levels, and export
the batch (json/csv/xlsx). Standard library only; binds to 127.0.0.1.
"""
import argparse
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import generate  # noqa: E402
import pressure  # noqa: E402

MODEL = generate.Model(generate.load_model(os.path.join(HERE, 'model.json')))
ALL_TARGETS = [t['count'] for r in MODEL.levels for t in r['targets'] if t['count']]
FALLBACK_TARGET = pressure._median(ALL_TARGETS, 9)
BASELINES = pressure.baselines(MODEL.levels, FALLBACK_TARGET)


def meta():
    orders = [r['order'] for r in MODEL.levels]
    return {
        'dimensions': [dict(zip(('key', 'label', 'unit', 'weight', 'help'), d)) for d in pressure.DIMENSIONS],
        'baselines': BASELINES,
        'forms': MODEL.raw['forms'],
        'data_range': [min(orders), max(orders)],
        'levels': len(MODEL.levels),
        'split_samples': len(MODEL.samples),
    }


def reference(start, end):
    out = []
    for rec in MODEL.levels:
        if start <= rec['order'] <= end:
            raw, imputed = pressure.real_pressure(rec, FALLBACK_TARGET)
            out.append({'level': rec['order'], 'key': rec['key'], 'difficulty': rec['difficulty'],
                        'T': rec['T'], 'U': rec['U'], 'duration_s': rec['duration_s'],
                        'targets': len(rec['targets']), 'stack_sets': sum(c['sets'] for c in rec['stack']),
                        'motion': rec['motion'], 'form': rec.get('form'),
                        'raw': raw, 'target_imputed': imputed})
    return out


def run_generate(body):
    start, end = int(body['start']), int(body['end'])
    counts = {'Normal': int(body['normal']), 'Hard': int(body['hard']), 'ExHard': int(body['exhard'])}
    overrides = {k: float(body[k]) for k in ('stack_rate', 'motion_rate') if body.get(k) not in (None, '')}
    seed = str(body.get('seed') or f'{start}-{end}-{counts["Normal"]}-{counts["Hard"]}-{counts["ExHard"]}')
    result = generate.generate(MODEL, start, end, counts, seed, overrides, body.get('rerolls') or {})
    for row in result['levels']:
        row['text'] = dict(zip(generate.COLUMNS, generate.table_row(row)))
    return result


class Handler(BaseHTTPRequestHandler):
    out_root = None

    def log_message(self, fmt, *args):
        pass

    def send(self, code, payload, ctype='application/json; charset=utf-8'):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            with open(os.path.join(HERE, 'viewer.html'), 'rb') as f:
                return self.send(200, f.read(), 'text/html; charset=utf-8')
        if self.path == '/api/meta':
            return self.send(200, meta())
        self.send(404, {'error': 'not found'})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            if self.path == '/api/generate':
                result = run_generate(body)
                return self.send(200, {'result': result,
                                       'reference': reference(result['input']['start'], result['input']['end'])})
            if self.path == '/api/export':
                result = run_generate(body)
                name = f"level-params-{result['input']['start']}-{result['input']['end']}"
                paths = generate.write_outputs(result, os.path.join(self.out_root, name))
                return self.send(200, {'paths': paths})
            self.send(404, {'error': 'not found'})
        except (ValueError, KeyError, RuntimeError) as e:
            self.send(400, {'error': str(e)})
        except Exception as e:  # report, keep serving
            traceback.print_exc()
            self.send(500, {'error': f'{type(e).__name__}: {e}'})


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--port', type=int, default=8770)
    ap.add_argument('--out-root', default=os.path.join(os.getcwd(), 'level-params-out'),
                    help='export directory root')
    args = ap.parse_args()
    Handler.out_root = os.path.abspath(args.out_root)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'level params viewer: http://127.0.0.1:{args.port}  (export -> {Handler.out_root})')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
