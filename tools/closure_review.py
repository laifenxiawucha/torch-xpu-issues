"""Explain closure-review candidates from case-level public evidence.

This module never declares a bug fixed or an issue safe to close. Incomplete
inventories, historical crossings and temporary skips remain visible to reviewers.
"""
import re
from collections import Counter
from case_inventory import case_identity, inventory, issue_inventory, identifiers
from evidence_context import evidence_scope, configuration_check, evidence_source, generated_analysis, result_context, pass_conditions, review_kind

REPO = 'intel/torch-xpu-ops'
CASE_STATUS = {
    'passed': '有通过记录', 'fixed': '修复 PR 已合并',
    'skipped': '已 skip / xfail', 'obsolete': '测试已删除 / 替换',
    'transferred': '已转移跟踪', 'crossed': '已划掉，结论待确认',
    'failed': '有失败反馈', 'unknown': '处理未知',
    'conditional': '调整条件后有通过记录', 'workaround': '绕过问题后有通过记录',
}
DISPOSED = {'passed', 'fixed', 'skipped', 'obsolete', 'transferred'}
PLAN = re.compile(r'\b(?:please|will|should|could|would|might|maybe|plan(?:ned)?|need(?:s)? to|to be|until|once|only (?:when|after)|not yet)\b', re.I)
NEGATIVE = re.compile(r'\b(?:still (?:fail\w*|persist\w*|reproduc\w*)|issue still (?:exists|persists)|do not close|don.t close|not (?:yet )?(?:fixed|resolved)|remain\w* fail\w*|regression in release|cannot (?:be )?(?:re.?checked|collect)|will be intentionally introduced|stays? open until)\b|仍然?失败|尚未修复', re.I)
FOLLOWUP = re.compile(r'duplicate (?:of|issue)|track(?:ed|ing)? (?:this |the |these |that |problem |issues? |cases? )*(?:in|under)|follow.up|split from|parent issue|转移.*跟踪|重复.*问题', re.I)


def references(text):
    """Keep repository identity; a PyTorch number must not become an Intel issue."""
    found = []
    pattern = r'https?://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)|\b([\w.-]+/[\w.-]+)#(\d+)|(?<![\w/#])#(\d+)'
    for m in re.finditer(pattern, text):
        repo = m.group(1) or m.group(3) or REPO
        number = int(m.group(2) or m.group(4) or m.group(5))
        token = f'{repo}#{number}'
        if token not in found:
            found.append(token)
    return found


def followup_references(text):
    lines = text.splitlines()
    result = set()
    for index, line in enumerate(lines):
        if FOLLOWUP.search(line):
            result.update(references('\n'.join(lines[index:index + 3])))
    return result


def _is_bot(comment):
    author = comment.get('author') or {}
    return author.get('__typename') == 'Bot' or author.get('login', '').lower().endswith(('bot', '[bot]'))


def _evidence(text, date, author, source, kind, scope=None, comment_id='', case=None):
    refs = references(text)
    text = re.sub(r'https?://[^\s<>\])]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if case:
        name = case['key'][2]
        position = text.find(name)
        if position >= 0:
            start = max(0, position - 160)
            end = min(len(text), position + len(name) + 330)
            excerpt = ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')
        else:
            excerpt = '整组结论对应此项：' + case['name'] + ' · ' + text[:500]
    else:
        excerpt = text[:850]
    return {'date': date[:10], 'timestamp': date, 'author': author, 'source': source,
            'kind': kind, 'excerpt': excerpt, 'references': refs,
            'comment_id': comment_id, 'configuration': scope or {}}


def relationship_kind(text, matching):
    if re.search(r'(?i)\b(?:likely|possibly|maybe|not a duplicate|not duplicate)\b', text):
        return 'related'
    if re.search(r'(?i)\b(?:other|another|sibling) (?:\w+ )?(?:case|issue)|follow.up to|split from|parent issue', text):
        return 'related'
    if re.search(r'(?i)\bduplicate of\b', text):
        return 'duplicate'
    explicit = re.search(r'(?i)(?:this (?:issue|problem)|all (?:remaining )?(?:cases|tasks|work)|remaining (?:cases|tasks|work)).{0,100}(?:transferred|moved|tracked|tracking).{0,30}(?:in|to|under)\s', text)
    if explicit or matching and re.search(r'(?i)\btracked (?:in|under)\b', text):
        return 'transfer'
    return 'related'


def _matching_cases(text, cases, lookup):
    """A bare test name is accepted only when unambiguous within this issue."""
    explicit_keys = {key for value,key in identifiers(text)}
    for token in set(re.findall(r'\btest_[A-Za-z0-9_]+', text)):
        options = lookup.get(token, [])
        for case in options:
            module, klass, name = case['key']
            # Parameter identifiers are significant. Do not match a prefix variant.
            if not case['_pattern'].search(text):
                continue
            if len(options) > 1 and case['key'] not in explicit_keys and not any(case_identity(alias) in explicit_keys for alias in case.get('aliases',[])):
                continue
            yield case


def review_issue(issue, linked, targets=None, runs=None):
    targets = targets or {}
    runs = runs or {}
    markdown = issue.get('body') or issue.get('bodyText', '')
    cases, complete, unparsed, declared_total = issue_inventory(issue)
    lookup = {}
    for case in cases:
        name = case['key'][2]
        case['_pattern'] = re.compile(r'(?<![\w])' + re.escape(name) + r'(?![\w\[])')
        lookup.setdefault(re.match(r'test_[A-Za-z0-9_]+', name).group(0), []).append(case)
    comments = sorted(issue['comments']['nodes'], key=lambda c: c['createdAt'])
    prs = {f"{p['repository']}#{p['number']}": p for p in linked}
    closure = [p for p in linked if p['explicit_fix_reference']]
    merged = [p for p in closure if p['state'] == 'MERGED']
    opened = [p for p in closure if p['state'] == 'OPEN']
    evidence, blockers, cautions, reasons, related, suggestions = [], [], [], [], [], []
    latest_negative = ''
    latest_positive = ''
    duplicate = False
    transfer = False
    source_texts = [(markdown, issue['updatedAt'], (issue.get('author') or {}).get('login', '报告者'), 'Issue 正文', False, '')]
    source_texts += [(c.get('body') or c['bodyText'], c['createdAt'], (c.get('author') or {}).get('login', '账号不可见'), evidence_source(c.get('body') or c['bodyText'], _is_bot(c)), _is_bot(c), c.get('id','')) for c in comments]
    for text, date, author, source, bot, comment_id in source_texts:
        scope = evidence_scope('\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('>')), runs)
        trusted_nightly = bot and author in ('github-actions', 'github-actions[bot]') and 'Passed in' in text and 'nightly testing' in text
        if generated_analysis(text):
            suggestions.append(_evidence(text, date, author, '自动分析建议', '自动建议，需独立核查', scope, comment_id))
            continue
        # Agent narratives and commands never become proof of a completed disposition.
        if bot and not trusted_nightly:
            continue
        lines = text.splitlines()
        groups = []
        for idx, line in enumerate(lines):
            if trusted_nightly:
                groups = [text]
                break
            if line.strip().startswith('>') or re.search(r'^\s*@\w+\s+(?:fix|repro)', line):
                continue
            # A short status heading may apply to an immediately following test list.
            context = result_context(line, text)
            if re.search(r'(?i)\b(?:fixed|skipped|passed|deleted|removed|renamed|tracked in)\b', line) and not re.search(r'\btest_\w+', line):
                tail = []
                for following in lines[idx + 1:]:
                    if re.match(r'^\s*#{1,6}\s', following):
                        break
                    if re.match(r'^\s*(?:[-*]\s+|`|\w+,[\w.-]+,test_|[\w./\\-]+\.py::)', following) and re.search(r'test_', following) or not following.strip() or following.strip().startswith('```'):
                        tail.append(following)
                    else:
                        break
                context += '\n' + '\n'.join(tail)
            groups.append(context)
        for context in groups:
            refs = references(context)
            matching = list(_matching_cases(context, cases, lookup))
            restricted_status, restricted_note = pass_conditions(context)
            restricted_pass = restricted_status != 'passed' and bool(re.search(r'(?i)\b(?:passed|passes)\b', context)) and not PLAN.search(context.splitlines()[0])
            specific_failure = source != 'Issue 正文' and not trusted_nightly and bool(matching) and re.search(r'(?i)\b(?:fails?|failed|hangs?|crash(?:es|ed)?)\b', context) and not PLAN.search(context) and not re.search(r'(?i)\b(?:before|previously|used to|no longer|now pass\w*|not fail\w*|doesn.t fail\w*)\b', context)
            negative = not restricted_pass and bool(NEGATIVE.search(context) or specific_failure)
            if negative and source != 'Issue 正文':
                item = _evidence(context, date, author, source, '失败或未完成反馈', scope, comment_id)
                evidence.append(item)
                latest_negative = max(latest_negative, date)
            relation = relationship_kind(context, matching)
            is_dup = relation == 'duplicate'
            is_transfer = relation == 'transfer'
            valid_targets = [ref for ref in refs if ref != f"{REPO}#{issue['number']}" and targets.get(ref, {}).get('type') == 'Issue' and (targets[ref].get('state') == 'OPEN' or is_dup and targets[ref].get('state') == 'CLOSED')]
            if FOLLOWUP.search(context) and refs and relation == 'related':
                related.append(_evidence(context, date, author, source, '相关引用，不作为关闭依据', scope, comment_id))
            if (is_dup or is_transfer) and valid_targets and not PLAN.search(context):
                duplicate |= is_dup
                if not matching:
                    transfer |= is_transfer
                evidence.append(_evidence(context, date, author, source, '重复主记录' if is_dup else '剩余任务转移记录', scope, comment_id))
            global_pass = source != 'Issue 正文' and bool(re.search(r'(?i)\ball (?:the )?(?:tests|cases|reported tests|tests reported in this issue)[^\n]{0,100}(?:are (?:currently )?passing|now pass|have passed)', context)) and not negative and not PLAN.search(context) and not re.search(r'(?i)\b(?:except|apart from|most|some|remaining)\b', context)
            if global_pass:
                matching = cases
            if not matching:
                continue
            status = None
            if restricted_pass:
                status = restricted_status
            elif negative:
                status = 'failed'
            elif trusted_nightly or global_pass or (source != 'Issue 正文' or context.lstrip().startswith('|')) and re.search(r'(?i)\b(?:passed|passes|now passing|verified passing)\b', context) and not PLAN.search(context) and not re.search(r'(?i)\b(?:fail\w*|hang\w*|crash\w*|baseline|isolation|alone|previous|earlier|rest of|other tests)\b', context):
                status = 'passed'
            elif re.search(r'(?i)\b(?:deleted|removed|renamed|replaced|obsolete)\b', context) and not PLAN.search(context):
                status = 'obsolete'
            elif re.search(r'(?i)\b(?:skipped|xfail(?:ed)?|skip added)\b', context) and not re.search(r'(?i)\b(?:will|should|please|plan|to be|not yet)\b', context):
                if any(prs.get(ref, {}).get('state') == 'MERGED' for ref in refs) or context.lstrip().startswith('|') or re.search(r'(?i)\b(?:are|is|now|already|have been) (?:\w+ )?(?:skipped|xfail)|skip (?:is|was|has been) (?:added|merged)', context):
                    status = 'skipped'
            elif re.search(r'(?i)\bfix(?:ed)?\b', context) and not PLAN.search(context) and any(prs.get(ref, {}).get('state') == 'MERGED' for ref in refs):
                status = 'fixed'
            elif is_transfer and valid_targets and not PLAN.search(context):
                status = 'transferred'
            if status:
                condition_note = restricted_note if restricted_pass else ''
                if status == 'passed':
                    status, condition_note = pass_conditions(context)
                item = _evidence(context, date, author, source, CASE_STATUS[status], scope, comment_id)
                evidence.append(item)
                if status in DISPOSED:
                    latest_positive = max(latest_positive, date)
                for case in matching:
                    # Body edit time is not a per-case verification timestamp.
                    if case['evidence'] and source == 'Issue 正文':
                        continue
                    case['status'] = status
                    case['condition_note'] = condition_note
                    case['evidence'] = (case['evidence'] + [_evidence(context, date, author, source, CASE_STATUS[status], scope, comment_id, case)])[-3:]
    for case in cases:
        if case['status'] == 'unknown' and case['crossed']:
            case['status'] = 'crossed'
            case['evidence'].append(_evidence(case['line'], issue['updatedAt'], '正文标记', 'Issue 正文', CASE_STATUS['crossed']))
    counts = Counter(c['status'] for c in cases)
    total = len(cases)
    disposed = sum(counts[k] for k in DISPOSED)
    marked = total > 0 and all(c['status'] in DISPOSED or c['crossed'] for c in cases)
    if merged:
        reasons.append('关闭引用 PR 已合并')
        for p in merged:
            evidence.append(_evidence(f"{p['repository']}#{p['number']} · {p['title']} · MERGED · 目标 {p['base']}", p.get('merged_at') or p['updated_at'], 'GitHub PR 状态', 'PR', '关闭引用 PR 已合并'))
    if total and complete and disposed == total:
        reasons.append('失败清单已有处理结论')
    elif total and complete and all(c['status'] in DISPOSED | {'conditional', 'workaround'} for c in cases):
        reasons.append('失败清单已有处理线索（含受限通过）')
    elif complete and marked:
        reasons.append('清单均有处理记录或删除线')
    if duplicate:
        reasons.append('存在重复主记录')
    if transfer or total and complete and disposed == total and counts['transferred']:
        reasons.append('剩余任务有明确转移记录')
    if opened:
        blockers.append(f'仍有 {len(opened)} 个明确关闭引用 PR 未合并')
    if counts['failed']:
        blockers.append(f"{counts['failed']} 个用例有失败或无法验证反馈")
    if counts['conditional'] or counts['workaround']:
        cautions.append(f"{counts['conditional'] + counts['workaround']} 个用例仅在调整条件或绕过问题后通过；需核对原条件和后续处理")
    # A later positive for one test does not clear a release-wide failure report.
    negative_items = [e for e in evidence if e['kind'] == '失败或未完成反馈']
    unresolved_negative = [e for e in negative_items if re.search(r'(?i)release|pre.release|2\.\d+|do not close|issue still|still persists|still failing', e['excerpt'])]
    if unresolved_negative:
        blockers.append('存在版本/分支或未完成反馈，需人工核对是否已解除')
    elif latest_negative and latest_negative > latest_positive:
        blockers.append('最近的失败反馈尚无更新的通过证据')
    title = issue['title']
    branch = re.search(r'(?i)release[/ ](\d+\.\d+)', title)
    branch_unconfirmed = bool(branch and not (cases and all(any(v.startswith(branch.group(1)) for e in c['evidence'][-1:] for v in e.get('configuration',{}).get('versions',[])) for c in cases)))
    expectation_unconfirmed = bool(re.search(r'(?i)unexpected success|unexpectedly pass', title) and not merged)
    flaky = any(re.search(r'(?i)random|flaky', label['name']) for label in issue.get('labels', {}).get('nodes', []))
    residual = bool(re.search(r'(?i)^\s*\[skip-added\]|reason for skip|real fix.*needs|follow.up:.*(?:implement|enable|remove)', title + '\n' + markdown))
    if residual:
        blockers.append('本条仍承载 skip 原因、能力缺口或恢复测试的后续工作')
    if not total and review_kind(issue, cases) != '构建问题':
        cautions.append('未识别到可完整核对的失败用例清单；不代表没有剩余用例')
    elif total and not complete:
        cautions.append('清单解析不完整或原文仅列部分用例，处理覆盖未知')
    if counts['unknown']:
        cautions.append(f"{counts['unknown']} 个用例处理未知")
    if counts['crossed']:
        cautions.append(f"{counts['crossed']} 个用例仅有删除线，处理原因待确认")
    if counts['skipped']:
        cautions.append('skip / xfail 不证明缺陷已修复，需确认保留跟踪或明确接受排除')
    if duplicate or transfer:
        cautions.append('需确认主记录覆盖全部剩余任务，并取得维护者关闭结论')
    if merged:
        cautions.append('需确认修复覆盖全部问题，并进入 issue 要求的目标版本 / pin')
    if branch_unconfirmed:
        cautions.append(f'原报告目标 release/{branch.group(1)}；逐项证据未完整确认该分支')
    if expectation_unconfirmed:
        cautions.append('Unexpected success 需核对过期 xfail / expectedFailure 是否已清理，普通通过记录不足以确认')
    if flaky:
        cautions.append('带 random / flaky 标签；单次通过不能确认间歇性问题已解除')
    if len({e.get('source') for e in evidence if e['kind'] in CASE_STATUS.values()}) == 1 and any(e['source'] == 'Bot 评论' for e in evidence):
        cautions.append('nightly 结果仅覆盖记录中的平台 / 配置，需核对原报告范围')
    configuration = configuration_check(issue, cases, runs)
    if configuration['checks']:
        cautions.extend(configuration['checks'])
    is_candidate = bool(reasons)
    status = '存在未完成事项' if is_candidate and blockers else '优先审查关闭' if is_candidate and total and complete and disposed == total and not counts['skipped'] and not residual and not branch_unconfirmed and not expectation_unconfirmed and not flaky and configuration['status']=='已匹配公开配置字段' else '需要补充确认' if is_candidate else '暂无关闭候选依据'
    unique = []
    seen = set()
    for item in sorted(evidence, key=lambda e: e.get('timestamp', e['date']), reverse=True):
        key = (item['date'], item['author'], item['kind'], item['excerpt'])
        if key not in seen:
            unique.append(item)
            seen.add(key)
    for case in cases:
        del case['key']
        del case['line']
        del case['_pattern']
    return {'candidate': is_candidate, 'status': status, 'reasons': reasons,
            'counts': {k: counts[k] for k in CASE_STATUS}, 'total': total,
            'disposed': disposed, 'inventory_complete': bool(total and complete),
            'declared_total': declared_total, 'configuration': configuration, 'related_records': related, 'analysis_suggestions': suggestions,
            'unparsed_lines': unparsed, 'blockers': list(dict.fromkeys(blockers)),
            'checks': list(dict.fromkeys(cautions)), 'evidence': unique,
            'cases': cases, 'latest_evidence_at': unique[0]['date'] if unique else ''}
