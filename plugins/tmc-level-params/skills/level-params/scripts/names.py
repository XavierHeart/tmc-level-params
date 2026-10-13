"""Theme asset names for the placeholder slots (建筑1, 道具3 ...).

The harness lists every slot of every theme with what it does in this batch
(how many copies per level, target, stacking base or rider, levels with moving
vehicles); the agent writes a Chinese name and an English id for each slot that
fit the theme; validate() checks the file mechanically; mapping() is what the
outputs use. Names are judgement, so they are written by the agent and recorded
in names.json, never invented by this code.
"""
import re

SCHEMA = 'level-params-names.v1'
CATEGORY_ZH = {'building': '建筑', 'prop': '道具', 'vehicle': '载具', 'character': '角色'}
CATEGORY_HINT = {
    'building': '建筑：固定在地面上的房屋、塔、站点等结构',
    'prop': '道具：摆在场景里的物件、设施、装饰',
    'vehicle': '载具：能开、能飞或能航行的交通工具',
    'character': '角色：人物或拟人角色',
}
PLACEHOLDER = re.compile(r'^(建筑|道具|载具|角色)\d+$')
ID_RE = re.compile(r'^[a-z][a-z0-9_]{1,39}$')
NAME_LEN = (2, 16)


def slot_category(slot):
    for c, zh in CATEGORY_ZH.items():
        if slot.startswith(zh) and slot[len(zh):].isdigit():
            return c
    raise ValueError(f'not a placeholder slot: {slot}')


def slots(result):
    """{theme: [slot dicts in category order]} with the role of each slot in this batch."""
    out = {}
    for theme, assets in result['assets'].items():
        levels = [r for r in result['levels'] if r['theme'] == theme]
        rows = []
        for slot in assets['names']:
            uses = []
            for r in levels:
                n = r['per_kind'].get(slot)
                if not n:
                    continue
                parts = [f"第{r['index']}关（{r['tier']}档）{n}个"]
                if any(t['kind'] == slot for t in r['targets']):
                    parts.append('目标')
                for combo in r['stacking']['combos']:
                    kinds = combo['kinds']
                    if kinds[0] == slot:
                        parts.append(f"叠放底座，上面放 {'+'.join(kinds[1:])}")
                    elif slot in kinds:
                        parts.append(f"叠在 {kinds[0]} 上")
                if r['motion'] and slot_category(slot) == 'vehicle':
                    parts.append('该关有运动载具')
                uses.append('，'.join(parts))
            rows.append({'slot': slot, 'category': slot_category(slot), 'uses': uses})
        out[theme] = rows
    return out


def template(result, params_sha256, prior=None):
    """A names.json to fill; prior = {theme: {slot: {'name', 'id'}}} pre-fills slots already named."""
    prior = prior or {}
    themes = {}
    for theme, rows in slots(result).items():
        keep = prior.get(theme, {})
        themes[theme] = {'slots': [{**r, 'name': keep.get(r['slot'], {}).get('name', ''),
                                    'id': keep.get(r['slot'], {}).get('id', '')} for r in rows]}
    return {
        'schema': SCHEMA,
        'params_sha256': params_sha256,
        'how_to_fill': [
            '每个 slot 填 name（中文名）和 id（英文小写、数字、下划线，字母开头，2-40 个字符）。',
            '名字必须和主题强相关、具体、能画出来；同一主题内名字和 id 都不能重复。',
            *CATEGORY_HINT.values(),
            'uses 说明这个 slot 在这一批里的作用：目标要一眼能认出；叠放底座要能让上层物件站上去或放上去；'
            '叠在别的物件上的要小于底座；有运动载具的关卡里，载具要是会动的交通工具。',
        ],
        'themes': themes,
    }


def mapping(doc):
    return {theme: {s['slot']: {'name': s['name'].strip(), 'id': s['id'].strip(), 'category': s['category']}
                    for s in t['slots']}
            for theme, t in doc['themes'].items()}


def check_mapping(result, names):
    """Mechanical checks of {theme: {slot: {name, id}}} against the batch; returns problems."""
    problems = []
    for theme, assets in result['assets'].items():
        given = names.get(theme)
        if given is None:
            problems.append(f'{theme}: no names')
            continue
        missing = [s for s in assets['names'] if s not in given]
        extra = [s for s in given if s not in assets['names']]
        problems += [f'{theme} {s}: not named' for s in missing]
        problems += [f'{theme} {s}: not a slot of this batch' for s in extra]
        seen_name, seen_id = {}, {}
        for slot in assets['names']:
            if slot not in given:
                continue
            name, ident = given[slot].get('name', '').strip(), given[slot].get('id', '').strip()
            if not name:
                problems.append(f'{theme} {slot}: name is empty')
            elif PLACEHOLDER.match(name):
                problems.append(f'{theme} {slot}: name {name!r} is still a placeholder')
            elif name == theme:
                problems.append(f'{theme} {slot}: name is the theme itself')
            elif not NAME_LEN[0] <= len(name) <= NAME_LEN[1]:
                problems.append(f'{theme} {slot}: name {name!r} must be {NAME_LEN[0]}-{NAME_LEN[1]} characters')
            elif name in seen_name:
                problems.append(f'{theme} {slot}: name {name!r} repeats {seen_name[name]}')
            else:
                seen_name[name] = slot
            if not ID_RE.match(ident):
                problems.append(f'{theme} {slot}: id {ident!r} must be lowercase letters, digits or _ '
                                f'(letter first, 2-40)')
            elif ident in seen_id:
                problems.append(f'{theme} {slot}: id {ident!r} repeats {seen_id[ident]}')
            else:
                seen_id[ident] = slot
    for theme in names:
        if theme not in result['assets']:
            problems.append(f'{theme}: not a theme of this batch')
    return problems


def validate(doc, result, params_sha256):
    """Problems of a filled names.json for the current parameters (empty list = ok)."""
    if doc.get('schema') != SCHEMA:
        return [f'names.json is not {SCHEMA}']
    if doc.get('params_sha256') != params_sha256:
        return ['names.json belongs to other parameters: run names-template again (it keeps the names you wrote)']
    problems = []
    expected = slots(result)
    for theme, t in doc.get('themes', {}).items():
        want = {r['slot']: r['category'] for r in expected.get(theme, [])}
        for s in t.get('slots', []):
            if s.get('slot') in want and s.get('category') != want[s['slot']]:
                problems.append(f"{theme} {s['slot']}: category changed")
    return problems + check_mapping(result, mapping(doc))
