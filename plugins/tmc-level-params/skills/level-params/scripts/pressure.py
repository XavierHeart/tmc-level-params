"""Pressure dimensions for real and generated levels, on one scale.

Each dimension is a raw value; the viewer turns it into a 0-100 score with the
P10/P90 of all real levels (`baselines`). Generated levels have no layout yet,
so placement dimensions (density, crowd) and moving vehicles are estimates from
the same-segment pool and are flagged in `estimated`.
"""
import statistics

DIMENSIONS = [
    # key, label, unit, default weight, help
    ('N', '物件数量', '个', 20, '物件总数 T'),
    ('K', '物件种类', '种', 15, '种类总数 U'),
    ('G', '目标数量', '个', 15, '全部目标的收集总数'),
    ('P', '时间压力', '个/分', 15, '目标总数 ÷ 时长（分钟）'),
    ('S', '叠放遮挡', '%', 10, '叠在上层的物件占 T 的比例'),
    ('M', '运动载具', '个', 5, '沿路径运动的物件数'),
    ('D', '摆放密度', '个/100u²', 10, '镜头范围内每 100 平方单位的物件数'),
    ('C', '拥挤度', '%', 10, '1 个单位内有近邻的物件占比'),
]
KEYS = [d[0] for d in DIMENSIONS]


def _median(values, default=None):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else default


def real_pressure(rec, fallback_target):
    """Raw pressures of one real level from model.json."""
    known = [t['count'] for t in rec['targets'] if t['count']]
    fill = _median(known, fallback_target)
    target_total = sum(t['count'] or fill for t in rec['targets'])
    riders = sum(c['sets'] * (c['size'] - 1) for c in rec['stack'])
    lay = rec.get('layout') or {}
    minutes = (rec['duration_s'] or 0) / 60
    return {
        'N': rec['T'], 'K': rec['U'], 'G': target_total,
        'P': round(target_total / minutes, 2) if minutes else None,
        'S': round(100 * riders / rec['T'], 2) if rec['T'] else 0,
        'M': rec.get('moving', 0),
        'D': lay.get('density'), 'C': round(100 * lay['crowd'], 2) if lay.get('crowd') is not None else None,
    }, len(known) < len(rec['targets'])


def generated_pressure(row, pool):
    """Raw pressures of one generated level; D, C and M are pool-based estimates."""
    T = row['T']
    target_total = sum(t['count'] for t in row['targets'])
    s = row['stacking']
    riders = sum(c['sets'] * (s['layers'] - 1) for c in s['combos']) if s['on'] else 0
    minutes = (row['duration_s'] or 0) / 60
    area = _median([r['layout']['camera_area'] for r in pool if r.get('layout')], 40.96 * 40.96)
    density = T / area * 100
    near = sorted((r for r in pool if r.get('layout') and r['layout']['crowd'] is not None),
                  key=lambda r: abs(r['layout']['density'] - density))[:5]
    crowd = _median([r['layout']['crowd'] for r in near], 0.0)
    moving = 0
    if row['motion']:
        moving = _median([r['moving'] for r in pool if r['motion']], 0)
        moving = min(moving, row['categories']['vehicle']['qty'])
    return {
        'N': T, 'K': row['U'], 'G': target_total,
        'P': round(target_total / minutes, 2) if minutes else None,
        'S': round(100 * riders / T, 2) if T else 0,
        'M': moving, 'D': round(density, 3), 'C': round(100 * crowd, 2),
    }


def baselines(levels, fallback_target):
    """P10/P90 per dimension over every real level."""
    columns = {k: [] for k in KEYS}
    for rec in levels:
        raw, _ = real_pressure(rec, fallback_target)
        for k in KEYS:
            if raw[k] is not None:
                columns[k].append(raw[k])
    out = {}
    for k, values in columns.items():
        values.sort()
        p10, p90 = values[int(len(values) * 0.1)], values[int(len(values) * 0.9)]
        out[k] = {'p10': p10, 'p90': p90 if p90 > p10 else p10 + 1}
    return out
