"""People routing and human-review priority, separate from closing proof."""
import re

TEAM_STATES = ['建议优先核对', '有关闭线索，需本人确认', '仍有明确未完成事项']
ROLE_LABELS = {'assignee': 'Issue 负责人', 'reporter': 'Issue 报告者', 'pr_author': '关联 PR 作者', 'reviewer': '被请求 Review'}


def people_for(issue, prs):
    people = []
    def add(login, role, reference):
        if login:
            item = {'login': login, 'role': role, 'reference': reference}
            if item not in people:
                people.append(item)
    reference = 'intel/torch-xpu-ops#' + str(issue['number'])
    for user in issue.get('assignees', {}).get('nodes', []):
        add(user['login'], 'assignee', reference)
    add((issue.get('author') or {}).get('login'), 'reporter', reference)
    for pr in prs:
        ref = pr['repository'] + '#' + str(pr['number'])
        add(pr.get('author'), 'pr_author', ref)
        for login in pr.get('review_users', []):
            add(login, 'reviewer', ref)
    return people


def review_summary(issue, closing, prs):
    body = closing['body_tasks']
    reasons = list(closing['reasons'])
    blockers = list(closing['blockers'])
    checks = list(closing['checks'])
    if body['has_signal']:
        if body['all_marked']:
            reasons.append('正文已识别事项均标记处理')
        else:
            reasons.append('正文存在处理标记，范围待确认')
        checks.append('请本人确认正文划线或勾选表示修复、skip、转移还是已不适用，并核对遗漏事项')
    if body['remaining']:
        blockers.append(f"正文清单仍有 {body['remaining']} 个未标记处理的事项")
    if re.search(r'(?i)don.?t close|do not close|keep (?:this issue )?open|不要关闭|勿关闭', issue['title']):
        blockers.append('标题明确要求保留跟踪，不作为普通收尾事项')
    proposals = closing.get('human_proposals', [])
    if proposals:
        reasons.append('人工讨论提出已解决或可关闭')
        checks.append('请相关成员确认人工结论覆盖原问题，并核对之后的反馈')
    reasons = list(dict.fromkeys(reasons))
    strong = any(p['explicit_fix_reference'] and p['state'] == 'MERGED' for p in prs) or bool(closing['total'] and closing['inventory_complete'] and closing['disposed'] == closing['total']) or bool(proposals)
    priority = 2 if blockers else 0 if strong and not closing['counts']['conditional'] and not closing['counts']['workaround'] else 1
    return {'candidate': bool(reasons), 'status': TEAM_STATES[priority], 'rank': priority, 'reasons': reasons,
            'blockers': list(dict.fromkeys(blockers)), 'checks': list(dict.fromkeys(checks)),
            'people': people_for(issue, prs), 'body_summary': f"{body['marked']} / {body['total']} 个正文事项有处理标记" if body['total'] else '',
            'question': '确认剩余事项后再判断是否关闭' if blockers else '请本人确认原问题是否已结束，以及是否可以关闭当前记录'}
