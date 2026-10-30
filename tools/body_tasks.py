"""Find issue-description disposition marks without assuming numbered tests.

The result describes the current document, never a test execution or a fix.
Unstructured marks stay partial; code, quotations and agent process checklists
cannot establish completion of the issue's actual work.
"""
import re
from case_inventory import identifiers

ENVIRONMENT = re.compile(r'(?i)^(?:versions?|environment|platform|system information|reproduction|steps to reproduce|error ?logs?|traceback|expected behavior|actual behavior|references|背景|环境|版本|复现步骤)\b')
PROCESS = re.compile(r'(?i)discovery agent|triage agent|fix agent|issue formatted|root cause identified|fix implemented \(.*agent|fix verified locally|PR proposed|PR proposal|human review|agent progress|agent workflow|reproduce on dev machine|verify fix locally')
LIST = re.compile(r'^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[([ xX])\]\s*)?(.*)$')
HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$')


def plain(text):
    text = re.sub(r'</?(?:del|s)(?:\s[^>]*)?>', '~~', text, flags=re.I)
    return text.strip()


def fully_crossed(text):
    text = plain(text).strip(' \t*')
    return bool(re.fullmatch(r'~~\s*\S[\s\S]*?~~[.!。！]?\s*', text)) and not re.sub(r'~~[\s\S]*?~~', '', text).strip(' .!。！')


def meaningful(text):
    return re.sub(r'~~|[*`]|</?\w+[^>]*>', '', text).strip()


def case_only(text):
    stripped = plain(text).strip(' ~`*')
    found = identifiers(stripped, allow_bare=True)
    return bool(found) and any(stripped == value for value, _ in found)


def task_marks(markdown):
    lines = markdown.splitlines()
    groups, paragraphs, headings = [], [], []
    section, level, fenced, html_code, comment = '', 0, '', False, False
    current, table = [], []
    paragraph = []

    def flush_list():
        nonlocal current
        if current:
            groups.append(current)
            current = []

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            text = '\n'.join(x[1] for x in paragraph)
            paragraphs.append({'text': text, 'line': paragraph[0][0], 'section': section, 'kind': 'paragraph'})
            paragraph = []

    def flush_table():
        nonlocal table
        if table and any('~~' in item['text'] for item in table):
            groups.append(table)
        table = []

    for number, original in enumerate(lines, 1):
        line = plain(original)
        if not line.startswith('|'):
            flush_table()
        if '<!--' in line:
            comment = True
        if comment:
            if '-->' in line:
                comment = False
            continue
        if re.match(r'^\s*<(?:pre|code)\b', line, re.I):
            html_code = True
        if html_code:
            if re.search(r'</(?:pre|code)>', line, re.I):
                html_code = False
            continue
        fence = re.match(r'^\s{0,3}(`{3,}|~{3,})', original)
        if fence:
            flush_list(); flush_paragraph()
            token = fence.group(1)[0]
            if not fenced:
                fenced = token
            elif fenced == token:
                fenced = ''
            continue
        if fenced or original.lstrip().startswith('>'):
            continue
        h = HEADING.match(original)
        if h:
            flush_list(); flush_paragraph()
            section, level = meaningful(h.group(2)), len(h.group(1))
            headings.append({'text': h.group(2), 'line': number, 'section': section, 'level': level, 'kind': 'heading'})
            continue
        if ENVIRONMENT.search(section) or PROCESS.search(section):
            continue
        item = LIST.match(original)
        if item:
            flush_paragraph()
            if len(item.group(1).expandtabs(4)) >= 4 and not current:
                continue
            text = plain(item.group(3))
            if PROCESS.search(text) or case_only(text):
                continue
            depth = len(item.group(1).expandtabs(4))
            parent = next((p for p in reversed(current) if p['depth'] < depth), None)
            # Plain sub-bullets under a checkbox describe that task. An
            # explicit unchecked child remains an independent open task.
            if parent and parent['kind'] == 'checklist' and item.group(2) is None and '~~' not in text:
                parent.setdefault('context', []).append(text)
                continue
            current.append({'text': text, 'line': number, 'section': section, 'kind': 'checklist' if item.group(2) is not None else 'list',
                            'depth': depth, 'checked': item.group(2) in ('x', 'X')})
            continue
        if current and original[:1].isspace() and line:
            current[-1]['text'] += '\n' + line
            continue
        if line.startswith('|'):
            flush_paragraph(); flush_list()
            if re.match(r'^\|?[\s:|\-]+\|?$', line):
                table = []  # preceding row is the column header
            elif not PROCESS.search(line):
                cells = [x.strip() for x in line.strip('|').split('|')]
                # Keep the whole row. Crossing only a test/word does not finish it.
                if not any(case_only(x) for x in cells):
                    state = 'marked' if all(fully_crossed(x) or re.fullmatch(r'\d+', x) for x in cells if x) else 'uncertain' if '~~' in line else 'open'
                    table.append({'text': line, 'line': number, 'section': section, 'kind': 'table', 'state': state, 'depth': 0, 'checked': False})
            continue
        if not line:
            flush_paragraph()
            # Blank lines may separate Markdown list items; keep their scope.
            continue
        flush_list()
        if len(original) - len(original.lstrip()) >= 4 or case_only(line) or PROCESS.search(line):
            continue
        paragraph.append((number, line))
    flush_list(); flush_paragraph(); flush_table()

    selected = []
    for group in groups:
        has_marks = any(x.get('checked') or '~~' in x['text'] for x in group)
        # A plain list in an old bug report is not a current open-task list.
        # Only explicit checkboxes, or a group containing disposition marks,
        # establish this description-specific task scope.
        explicit = any(x['kind'] == 'checklist' for x in group)
        if not (has_marks or explicit):
            continue
        for item in group:
            if 'state' not in item:
                item['state'] = 'marked' if item['checked'] or fully_crossed(item['text']) else 'uncertain' if '~~' in item['text'] else 'open'
            item['scope'] = '清单事项'
            selected.append(item)
    # A crossed heading/paragraph is useful even without list numbering. Its
    # surrounding scope cannot be proved complete, so it never becomes 3/3 done.
    for item in paragraphs + headings:
        if '~~' in item['text'] and not identifiers(item['text'], allow_bare=True) and not re.search(r'(?m)^(?:~~)?[\w.-]+,(?:[\w.-]*,)?test[._]', item['text']) and not re.fullmatch(r'`+.*`+', item['text'], re.S) and not PROCESS.search(item['text']) and not ENVIRONMENT.search(item['section']):
            item.update({'state': 'marked' if fully_crossed(item['text']) else 'uncertain', 'depth': 0, 'scope': '段落范围待确认'})
            selected.append(item)
    selected.sort(key=lambda x: x['line'])
    marked = sum(x['state'] == 'marked' for x in selected)
    remaining = sum(x['state'] == 'open' for x in selected)
    uncertain = sum(x['state'] == 'uncertain' for x in selected)
    complete_scope = bool(selected) and all(x['scope'] == '清单事项' and x['kind'] != 'table' for x in selected)
    return {'items': selected, 'total': len(selected), 'marked': marked, 'remaining': remaining, 'uncertain': uncertain,
            'all_marked': complete_scope and marked == len(selected), 'scope_complete': complete_scope,
            'has_signal': bool(marked or uncertain), 'source': '当前 Issue 描述',
            'note': '标记仅表示正文中的处理状态；需本人确认含义和覆盖范围。正文编辑时间不等于逐项处理时间。'}
