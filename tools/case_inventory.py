"""Parse declared test lists and human result tables, excluding incidental logs."""
import re

IDENTIFIER = re.compile(
    r'\b\w+,[\w.-]+,test_[\w\[\].=,+:/-]+|'
    r'[\w./\\-]+(?:::)(?:[A-Z][\w]*::|[A-Z][\w]*\.)?test_[\w\[\].=,+:/-]+|'
    r'(?<![\w.])(?:[A-Z]\w*\.)test_[\w\[\].=,+:/-]+'
)
LIST_HEADING = re.compile(r'^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:(?:affected|failing|reported|test|full|per-test)\s+)*(?:test_cases|cases?|tests?|failures)(?:\*\*)?\s*:?\s*$', re.I)
STOP_HEADING = re.compile(r'^\s*(?:#{1,6}\s*)?(?:error\s*log|traceback|versions?|repro\w*|root cause|environment|platform|summary|notes?|expected\b|actual\b|analysis|parent issue|scope)\b', re.I)
PARTIAL = re.compile(r'representative|partial list|examples? (?:only|of)|not enumerated|not exhaustive|部分用例', re.I)


def case_identity(value):
    value = value.strip(' ~`*|,;')
    if re.match(r'^\w+,[\w.-]+,test_', value):
        _, module, name = value.split(',', 2)
        module, klass = module.rsplit('.', 1)
    elif '::' in value:
        parts = value.replace('\\', '/').split('::')
        module = parts[0].removesuffix('.py')
        if len(parts) >= 3:
            klass, name = parts[-2:]
        elif re.match(r'^[A-Z]\w*\.test_', parts[-1]):
            klass, name = parts[-1].split('.', 1)
        else:
            klass, name = '', parts[-1]
    elif re.match(r'^[A-Z]\w*\.test_', value):
        klass, name = value.split('.', 1)
        module = ''
    else:
        module, klass, name = '', '', value
    if not re.fullmatch(r'test_[\w\[\].=,+:/-]+', name) or name.endswith('.py'):
        return None
    module = module.replace('\\', '/').replace('/', '.')
    module = re.sub(r'^.*?(?:third_party\.torch-xpu-ops\.test\.xpu\.|test\.xpu\.)', '', module)
    module = re.sub(r'^test\.', '', module)
    module = re.sub(r'_xpu$', '', module)
    return module, klass, name


def identifiers(line, allow_bare=False):
    values = IDENTIFIER.findall(line)
    if not values and allow_bare:
        # Bare module/file summary rows are not individual test cases.
        if re.search(r'\.py\b|[—–]\s*\d+\s+(?:failures|cases)|module breakdown', line, re.I):
            return []
        cell = re.sub(r'^\s*(?:[-*]\s+|\d+[.)]\s+|\[[ xX]\]\s*)', '', line).strip(' ~`|')
        if re.fullmatch(r'test_[\w\[\].=,+:/-]+', cell):
            values = [cell]
    return [(value, case_identity(value)) for value in values if case_identity(value)]


def _merge(cases, value, key, line, origin):
    crossed = bool(re.search(r'~~[^\n]*' + re.escape(value) + r'[^\n]*~~', line))
    item = {'name': value, 'key': key, 'crossed': crossed, 'line': line.strip(),
            'status': 'unknown', 'evidence': [], 'inventory_source': origin}
    if key in cases:
        cases[key]['crossed'] &= crossed
    else:
        # Match a qualified alias with an existing unqualified declaration only
        # if that name is unique. Never merge two different modules or classes.
        aliases = [k for k in cases if k[2] == key[2] and (not k[0] or not key[0]) and (not k[1] or not key[1] or k[1] == key[1])]
        if len(aliases) == 1:
            previous = cases.pop(aliases[0])
            if not key[0]:
                item = previous
                key = previous['key']
            item['crossed'] &= previous['crossed']
        cases[key] = item


def inventory(markdown):
    cases, unparsed = {}, 0
    active = False
    declared = False
    partial = False
    stopped = False
    for line in markdown.splitlines():
        if LIST_HEADING.match(line):
            active = declared = True
            stopped = False
            continue
        if STOP_HEADING.match(line):
            active = False
            stopped = True
        # A CSV test list is a declaration even in a body without a heading.
        csv_line = re.match(r'^\s*(?:[-*]\s+)?(?:~~)?\w+,[\w.-]+,test_', line)
        if csv_line and not stopped:
            active = declared = True
        if not active:
            continue
        partial |= bool(PARTIAL.search(line))
        parsed = identifiers(line, allow_bare=True)
        for value, key in parsed:
            _merge(cases, value, key, line, 'Issue 正文清单')
        if not parsed and re.search(r'\btest_\w+\b', line) and not re.search(r'\.py\b|module breakdown|[—–]\s*\d+', line, re.I):
            if re.match(r'^\s*(?:[-*]\s+|`|\w+[,.:])', line):
                unparsed += 1
    return list(cases.values()), declared and not partial and not unparsed, unparsed


def result_table_rows(markdown):
    """Emit test/result rows from an explicit human Markdown results table."""
    header = None
    rows = []
    for line in markdown.splitlines():
        if not line.lstrip().startswith('|'):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if any(re.fullmatch(r':?-{2,}:?', c) for c in cells):
            continue
        lower = [c.lower().strip(' `*') for c in cells]
        if any(re.fullmatch(r'(?:test(?: case)?|case|用例)', c) for c in lower) and any(re.search(r'result|status|verdict|结果|处理', c) for c in lower):
            header = lower
            continue
        if not header or len(cells) != len(header):
            continue
        ti = next(k for k, c in enumerate(header) if re.fullmatch(r'(?:test(?: case)?|case|用例)', c))
        ri = next(k for k, c in enumerate(header) if re.search(r'result|status|verdict|结果|处理', c))
        for value, key in identifiers(cells[ti], allow_bare=True):
            rows.append({'value': value, 'key': key, 'line': line, 'result': cells[ri]})
    return rows


def issue_inventory(issue):
    cases, complete, unparsed = inventory(issue.get('body') or issue.get('bodyText', ''))
    if not cases:
        # A uniquely named test in both title and opening description is an
        # explicit single-case report, unlike incidental traceback references.
        names=set(re.findall(r'\btest_[A-Za-z0-9_]+',issue['title']))
        opening=(issue.get('body') or issue.get('bodyText','')).split('Traceback',1)[0][:1300]
        singles={key:(value,key) for value,key in identifiers(opening) if key[2] in names}
        if len(singles)==1:
            value,key=next(iter(singles.values()))
            record={};_merge(record,value,key,value,'标题与正文中的明确单例')
            cases=list(record.values());complete=True
    indexed = {c['key']: c for c in cases}
    body_count = len(indexed)
    table_count = 0
    for comment in issue['comments']['nodes']:
        author = comment.get('author') or {}
        if author.get('__typename') == 'Bot' or author.get('login', '').lower().endswith(('bot', '[bot]')):
            continue
        markdown=comment.get('body') or ''
        relocated=bool(re.search(r'(?is)(?:files?|\.py`).{0,100}(?:removed|moved|renamed)|cases now run from.*upstream',markdown))
        for row in result_table_rows(markdown):
            if relocated and row['key'] not in indexed:
                aliases=[c for c in indexed.values() if c['key'][0]==row['key'][0] and c['key'][2]==row['key'][2] and c['key'][1].replace('Device','')==row['key'][1].replace('Device','')]
                if len(aliases)==1:
                    aliases[0].setdefault('aliases',[]).append(row['value'])
                    table_count+=1
                    continue
            _merge(indexed, row['value'], row['key'], row['line'], '人工评论结果表')
            table_count += 1
    declared = re.search(r'(?i)\b(\d+)\s+(?:UTs?|(?:test )?cases?)\b', issue['title'])
    expected = int(declared.group(1)) if declared else None
    if expected is None:
        body = issue.get('body') or issue.get('bodyText', '')
        count = re.search(r'(?i)\b(\d+)\s+(?:failures?\b|(?:affected |failing )?(?:test )?cases?\s+(?:in total|total))', body[:1300])
        expected = int(count.group(1)) if count else None
    if expected is not None:
        complete = len(indexed) == expected and not unparsed and (complete or table_count >= expected)
    elif table_count and not body_count:
        # A table without a declared total does not establish whole-issue scope.
        complete = False
    return list(indexed.values()), bool(indexed) and complete, unparsed, expected
