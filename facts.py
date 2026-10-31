"""Conservative atomic facts. Vector similarity is never evidence of truth."""
import re
import uuid


def normalized(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold().rstrip('。.!！')


def infer(text):
    text = re.sub(r'^(?:更正一下|更正|纠正|记住|以后|现在)[：:,，\s]*', '', text.strip())
    for pattern, predicate in [
        (r'^I (?:now |currently )?live in ([^.\n]{1,80})\.?$', 'home_city'),
        (r'^(?:My name is|Call me) ([^.\n]{1,80})\.?$', 'name'),
        (r'^(?:Please )?(?:reply|respond|answer) in (English|Chinese|Japanese)\.?$', 'reply_language'),
    ]:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match[1].strip()
            value = {'english': '英语', 'chinese': '中文', 'japanese': '日语'}.get(value.lower(), value)
            return {'subject': 'user', 'predicate': predicate, 'value': value}
    preference = re.fullmatch(r'我(不再喜欢|不喜欢|喜欢)([^，。；\n]{1,60})[。！!]?', text)
    if preference:
        return {'subject': 'user', 'predicate': 'likes:' + preference[2].rstrip('。！!'),
                'value': 'true' if preference[1] == '喜欢' else 'false'}
    for pattern, predicate in [
        (r'^(?:我(?:现在)?住在|我(?:已经)?搬到|我的(?:常住城市|居住地)是)([^，。；\n]{1,40})[。！!]?$', 'home_city'),
        (r'^(?:我叫|我的名字是)([^，。；\n]{1,40})[。！!]?$', 'name'),
        (r'^(?:请)?(?:用|使用)(中文|英文|英语|汉语|日语)(?:回复|回答|交流)[。！!]?$', 'reply_language'),
    ]:
        match = re.match(pattern, text)
        if match:
            value = match.group(1).rstrip('。！!')
            value = {'英文': '英语', '汉语': '中文'}.get(value, value)
            return {'subject': 'user', 'predicate': predicate, 'value': value}
    match = re.fullmatch(r'([^，。；\n]{1,30})的([^，。；\n]{1,30}?)(?:改为|是|为)([^，。；\n]{1,100})[。！!]?', text)
    if match:
        return {'subject': match[1], 'predicate': match[2], 'value': match[3].rstrip('。！!')}
    return None


def key(item):
    fact = item.get('fact')
    if not isinstance(fact, dict):
        return None
    return (item.get('scope', 'default'), normalized(fact.get('subject')), normalized(fact.get('predicate')))


def decide(store, layer, candidate):
    """Return superseded rows or a pending conflict; do not silently merge guesses."""
    identity = key(candidate)
    if not identity or layer not in ('rules', 'profile', 'preferences'):
        return [], None
    previous = [row for name in ('rules', 'profile', 'preferences') for row in store['layers'][name]['items']
                if key(row) == identity and normalized(row['fact'].get('value')) != normalized(candidate['fact'].get('value'))]
    if not previous:
        return [], None
    trusted_user = candidate.get('source') == 'user' and candidate.get('confirmed') is True
    rule_ids = {id(row) for row in store['layers']['rules']['items']}
    stronger_evidence = (layer != 'rules' and not any(id(row) in rule_ids for row in previous) and candidate.get('source') == 'observation'
                        and candidate.get('confidence', 0) >= 0.9
                        and all(not row.get('confirmed') and row.get('source', 'agent') == 'agent' for row in previous))
    if trusted_user or stronger_evidence:
        return previous, None
    for conflict in store.setdefault('conflicts', []):
        if conflict.get('status') == 'pending' and key(conflict.get('incoming', {})) == identity \
                and normalized(conflict['incoming']['fact'].get('value')) == normalized(candidate['fact'].get('value')):
            return [], conflict
    conflict = {'id': str(uuid.uuid4()), 'status': 'pending', 'layer': layer,
                'currentIds': [row['id'] for row in previous], 'incoming': candidate,
                'reason': 'Contradictory fact without confirmed user correction or stronger observed evidence'}
    store['conflicts'].append(conflict)
    return [], conflict
