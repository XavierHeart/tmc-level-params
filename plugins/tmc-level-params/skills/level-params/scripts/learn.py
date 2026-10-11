#!/usr/bin/env python3
"""Learn level-parameter statistics from the competitor mainline archive.

Reads the CSV export under `tmc资源包/数据` and writes one compact model file
(`model.json`) that `generate.py` samples from. Run once per data refresh:

    python3 scripts/learn.py --data <tmc资源包/数据> --out scripts/model.json

Per level in the official order it records: difficulty, duration, T (all
placed rows, decor included, same as the rules workbook), U (distinct items),
targets (kinds from stageGroupIds, counts where the name resolves), stacking
sets (carrier row followed by isNested rows), motion (any hasWaypoint row) and,
when every item resolves to a category, the four-category split. Fully
resolved levels are clustered into joint forms F1-F5.
"""
import argparse
import collections
import csv
import hashlib
import json
import math
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tiers  # noqa: E402

CATEGORIES = ('building', 'prop', 'vehicle', 'character')
CATEGORY_ZH = {'building': '建筑', 'prop': '道具', 'vehicle': '载具', 'character': '角色'}

# Asset-name prefix -> category. 'decor' rows count toward T but are not one of
# the four collectable categories (their per-level counts are mostly not
# multiples of 3). Animals count as characters (product rule).
PREFIX_CATEGORY = {
    'building': 'building', 'buildings': 'building', 'buildinga': 'building', 'build': 'building',
    'prop': 'prop', 'props': 'prop', 'db': 'prop',
    'vehicle': 'vehicle', 'vehicles': 'vehicle', 'balloon': 'vehicle', 'helicopter': 'vehicle',
    'sail': 'vehicle', 'ship': 'vehicle', 'plane': 'vehicle', 'pirate': 'vehicle',
    'human': 'character', 'characters': 'character', 'character': 'character', 'classic': 'character',
    'animal': 'character', 'animals': 'character', 'western': 'character',
    'nc': 'decor', 'robot': 'decor', 'underwater': 'decor', 'cloud': 'decor', 'mountain': 'decor',
    'wharf': 'decor', 'whatf': 'decor', 'mini': 'decor', 'parkin': 'decor', 'pro': 'decor',
    'puddle': 'decor', 'sidewalk': 'decor', 'seapaving': 'decor', 'purpleseaweed': 'decor',
    'greenseaweedset': 'decor', 'redseaweed': 'decor', 'pinkseaweed': 'decor',
}

DIFFICULTIES = ('Normal', 'Hard', 'ExHard')
FORM_COUNT = 5


def categorize(name):
    """Return building/prop/vehicle/character/decor, or None when unknown."""
    if not name:
        return None
    low = name.strip().strip("'").lower()
    if low.startswith('viking_vehicles'):
        return 'vehicle'
    if low.startswith('animals0'):
        return 'character'
    return PREFIX_CATEGORY.get(re.split(r'[_ ]', low)[0])


def parse_duration(text):
    try:
        h, m, s = (int(x) for x in text.split(':'))
        return h * 3600 + m * 60 + s
    except (ValueError, AttributeError):
        return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def read_csv(path):
    with open(path, newline='', encoding='utf-8') as f:
        yield from csv.DictReader(f)


CROWD_RADIUS = 1.0  # world units; a neighbour this close counts as crowding


def layout_metrics(rows, level_row):
    """Placement pressure of a real layout: density in the camera view, spacing, crowding."""
    try:
        cam_w, cam_h = float(level_row['cameraWidth']), float(level_row['cameraHeight'])
    except (TypeError, KeyError, ValueError):
        cam_w = cam_h = 40.96
    area = cam_w * cam_h
    pts = [(float(r['x']), float(r['y'])) for r in rows if r['x'] and r['y']]
    nearest = []
    if len(pts) > 1:
        cell = {}
        for i, (x, y) in enumerate(pts):  # grid buckets keep this near-linear
            cell.setdefault((int(x // 2), int(y // 2)), []).append(i)
        for i, (x, y) in enumerate(pts):
            best = math.inf
            for ring in range(1, 40):
                cx, cy = int(x // 2), int(y // 2)
                for gx in range(cx - ring, cx + ring + 1):
                    for gy in range(cy - ring, cy + ring + 1):
                        for j in cell.get((gx, gy), ()):
                            if j != i:
                                best = min(best, math.hypot(x - pts[j][0], y - pts[j][1]))
                if best <= 2 * ring:  # anything outside the ring is at least 2*ring away
                    break
            nearest.append(best)
    nearest.sort()
    return {
        'camera_area': round(area, 2),
        'density': round(len(rows) / area * 100, 3),  # items per 100 square units
        'nn_median': round(nearest[len(nearest) // 2], 3) if nearest else None,
        'crowd': round(sum(d <= CROWD_RADIUS for d in nearest) / len(nearest), 4) if nearest else None,
    }


def level_record(key, meta, level_row, rows, item_name):
    counts = collections.Counter(r['itemGuid'] for r in rows)
    record = {
        'key': key,
        'order': int(meta['Order']),
        'difficulty': meta['LevelType'],
        'duration_s': parse_duration(meta['Duration']),
        'T': len(rows),
        'U': len(counts),
    }
    # Targets: names in stageGroupIds; the count is the placed instances of the
    # matching item (all matched counts are multiples of 3 in the archive).
    by_name = collections.Counter()
    for guid, n in counts.items():
        if item_name.get(guid):
            by_name[item_name[guid].lower()] += n
    targets = []
    for name in filter(None, (level_row or {}).get('stageGroupIds', '').split('|')):
        cat = categorize(name)
        targets.append({'category': cat if cat in CATEGORIES else None, 'count': by_name.get(name.lower()) or None})
    record['targets'] = targets

    # Stacking: an isNested row belongs to the nearest earlier non-nested row.
    sets = collections.Counter()
    carrier, riders = None, []

    def close():
        if carrier is not None and riders:
            sets[(carrier, tuple(riders))] += 1

    for r in rows:
        if 'isNested' in r['flagNames']:
            if carrier is not None:
                riders.append(r['itemGuid'])
        else:
            close()
            carrier, riders = r['itemGuid'], []
    close()
    combos = []
    for (c, rs), n in sorted(sets.items(), key=lambda kv: -kv[1]):
        cats = [categorize(item_name.get(g, '')) for g in (c,) + rs]
        combos.append({'size': 1 + len(rs), 'sets': n,
                       'categories': cats if all(x in CATEGORIES for x in cats) else None})
    record['stack'] = combos
    record['motion'] = any('hasWaypoint' in r['flagNames'] for r in rows)
    record['moving'] = sum('hasWaypoint' in r['flagNames'] for r in rows)
    record['layout'] = layout_metrics(rows, level_row)

    # Four-category split, only when every item kind resolves.
    kinds = {c: [] for c in CATEGORIES}
    decor_rows = 0
    resolved = True
    for guid, n in counts.items():
        cat = categorize(item_name.get(guid, ''))
        if cat is None and n % 3:
            cat = 'decor'  # unresolved, but a non-multiple of 3 cannot be a match-3 collectable
        if cat is None:
            resolved = False
            break
        if cat == 'decor':
            decor_rows += n
        else:
            kinds[cat].append(n)
    if resolved and any(kinds.values()):
        record['split'] = {c: sorted(kinds[c], reverse=True) for c in CATEGORIES}
        record['decor_rows'] = decor_rows
    return record


def features(rec):
    split = rec['split']
    qty = {c: sum(split[c]) for c in CATEGORIES}
    total = sum(qty.values()) or 1
    target_total = sum(t['count'] or 0 for t in rec['targets'])
    return [qty[c] / total for c in CATEGORIES] + [math.log(rec['T']), rec['U'], target_total]


def kmeans(points, k, seed, restarts=20, iterations=100):
    rng = random.Random(seed)
    best = None
    for _ in range(restarts):
        centers = [list(rng.choice(points))]
        while len(centers) < k:  # k-means++
            d2 = [min(sum((a - b) ** 2 for a, b in zip(p, c)) for c in centers) for p in points]
            pick, acc = rng.random() * sum(d2), 0.0
            for p, w in zip(points, d2):
                acc += w
                if acc >= pick:
                    centers.append(list(p))
                    break
        labels = [0] * len(points)
        for _ in range(iterations):
            new = [min(range(k), key=lambda j: sum((a - b) ** 2 for a, b in zip(p, centers[j]))) for p in points]
            if new == labels and _:
                break
            labels = new
            for j in range(k):
                members = [p for p, l in zip(points, labels) if l == j]
                if members:
                    centers[j] = [sum(col) / len(members) for col in zip(*members)]
        inertia = sum(sum((a - b) ** 2 for a, b in zip(p, centers[l])) for p, l in zip(points, labels))
        if best is None or inertia < best[0]:
            best = (inertia, labels, centers)
    return best[1], best[2]


def describe_form(center, means, stds):
    """Name a cluster from its standardized center (same feature order as features())."""
    z = [(c - m) / s if s else 0.0 for c, m, s in zip(center, means, stds)]
    shares = z[:4]
    lead = max(range(4), key=lambda i: shares[i])
    parts = []
    if z[6] > 0.7:
        parts.append('高目标')
    if max(shares) - sorted(shares)[-2] < 0.3 and max(shares) < 0.6:
        parts.append('四类均衡')
    else:
        parts.append(CATEGORY_ZH[CATEGORIES[lead]] + '偏重')
    parts.append('低种类' if z[5] < -0.5 else '高种类' if z[5] > 0.5 else '中种类')
    parts.append('大体量' if z[4] > 0.5 else '小体量' if z[4] < -0.5 else '中体量')
    return '·'.join(parts)


def learn(data_dir):
    files = {name: os.path.join(data_dir, name) for name in (
        'mainline_official_order.csv', 'mainline_levels.csv', 'mainline_collectables.csv',
        'mainline_item_image_map.csv')}
    for path in files.values():
        if not os.path.isfile(path):
            sys.exit(f'missing data file: {path}')
    order = {r['Key']: r for r in read_csv(files['mainline_official_order.csv']) if r['LevelType'] in DIFFICULTIES}
    levels = {r['levelKey']: r for r in read_csv(files['mainline_levels.csv'])}
    item_name = {r['itemGuid']: r['resolvedAssetName'] for r in read_csv(files['mainline_item_image_map.csv'])}
    rows = collections.defaultdict(list)
    for r in read_csv(files['mainline_collectables.csv']):
        if r['levelKey'] in order:
            rows[r['levelKey']].append(r)
    for key in rows:
        rows[key].sort(key=lambda r: (int(r['layoutIndex']), int(r['index'])))

    records = [level_record(k, order[k], levels.get(k), rows[k], item_name) for k in rows]
    records.sort(key=lambda r: r['order'])

    samples = [r for r in records if 'split' in r]
    points = [features(r) for r in samples]
    means = [sum(col) / len(col) for col in zip(*points)]
    stds = [math.sqrt(sum((x - m) ** 2 for x in col) / len(col)) for col, m in zip(zip(*points), means)]
    z = [[(x - m) / s if s else 0.0 for x, m, s in zip(p, means, stds)] for p in points]
    labels, centers = kmeans(z, FORM_COUNT, seed=20261010)
    # Stable ids: F1 is the largest cluster.
    sizes = collections.Counter(labels)
    rank = {old: i for i, (old, _) in enumerate(sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0])))}
    forms = []
    for old in sorted(rank, key=rank.get):
        raw_center = [c * s + m for c, m, s in zip(centers[old], means, stds)]
        forms.append({'id': f'F{rank[old] + 1}', 'description': describe_form(raw_center, means, stds),
                      'samples': sizes[old],
                      'center': dict(zip(['building_share', 'prop_share', 'vehicle_share', 'character_share',
                                          'log_T', 'U', 'target_total'], [round(x, 4) for x in raw_center]))})
    for rec, label in zip(samples, labels):
        rec['form'] = f'F{rank[label] + 1}'

    return {
        'schema': 'tmc-level-params-model.v2',
        'source': {name: {'sha256': sha256(path)} for name, path in files.items()},
        'definitions': {
            'T': 'all placed rows of the level, decor included (matches 关卡参数生成规则.xlsx)',
            'U': 'distinct itemGuid',
            'targets': 'stageGroupIds; count = placed instances of the same-named item, null when the name does not resolve',
            'stack': 'carrier row followed by isNested rows; size = carrier + riders; sets = repeats of the same item tuple',
            'motion': 'level has any hasWaypoint row; moving = number of hasWaypoint rows',
            'layout': 'density = T per 100 square units of camera view; nn_median = median nearest-neighbour '
                      'distance (units); crowd = share of items with a neighbour within 1.0 unit',
            'split': 'per-category item counts, only when every itemGuid resolves (unresolved non-multiples of 3 count as decor)',
        },
        'category_prefixes': PREFIX_CATEGORY,
        'forms': forms,
        'tiers': tiers.learn(records),
        'levels': records,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--data', required=True, help='directory with mainline_*.csv')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model.json'))
    args = ap.parse_args()
    model = learn(args.data)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(model, f, ensure_ascii=False, separators=(',', ':'))
    samples = sum('split' in r for r in model['levels'])
    print(f"levels: {len(model['levels'])}, split samples: {samples}, wrote {args.out}")
    for form in model['forms']:
        print(f"  {form['id']} {form['description']} ({form['samples']})")
    cv = model['tiers']['cross_validation']
    print(f"  tiers: cuts {model['tiers']['cuts']}, AUC Hard/ExHard vs Normal {cv['auc_hard_or_exhard_vs_normal']}, "
          f"ExHard vs Hard {cv['auc_exhard_vs_hard']}")


if __name__ == '__main__':
    main()
