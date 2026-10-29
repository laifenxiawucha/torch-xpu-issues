"""Plain-text provenance and conservative environment matching for test evidence."""
import re


def execution_refs(text):
    found = re.findall(r'github\.com/([\w.-]+/[\w.-]+)/actions/runs/(\d+)', text)
    found += re.findall(r'([\w.-]+/[\w.-]+)@run:(\d+)', text)
    return list(dict.fromkeys(f'{repo}@run:{number}' for repo, number in found))


def platforms(text):
    hardware = set(re.findall(r'\b(?:PVC|BMG|B60|B580|LNL|MTL|ARL|DG2|PTL(?:-H)?)\b', text, re.I))
    hardware = {('BMG' if h.upper() in ('B60', 'B580') else 'PTL' if h.upper() == 'PTL-H' else h.upper()) for h in hardware}
    operating = set()
    if re.search(r'\bWSL\w*\b', text, re.I):
        operating.add('WSL')
    if re.search(r'\bWindows\b', text, re.I):
        operating.add('Windows')
    if re.search(r'\b(?:Linux|Ubuntu|Debian|CentOS)\b', text, re.I):
        operating.add('Linux')
    return {'hardware': sorted(hardware), 'os': sorted(operating)}


def evidence_scope(text, runs):
    scope = platforms(text)
    scope['runs'] = execution_refs(text)
    scope['versions'] = list(dict.fromkeys(re.findall(r'(?i)(?:torch\s*[=:]+\s*|PyTorch(?: version)?\s*[:=]\s*|release/)(2\.\d+(?:\.\d+)?[^\s`<>,;]*)', text)))
    scope['same_configuration'] = bool(re.search(r'(?i)(?:verified|retested|passed).{0,80}(?:same|original) (?:configuration|environment)|(?:same|original) (?:configuration|environment).{0,80}(?:verified|passed)', text))
    return scope


def configuration_check(issue, cases, runs):
    labels = [x['name'] for x in issue.get('labels', {}).get('nodes', [])]
    expected = platforms(issue['title'] + ' ' + ' '.join(labels))
    original = [runs.get(ref, {}) for ref in execution_refs(issue.get('body') or issue.get('bodyText', ''))]
    paths = {r.get('path') for r in original if r.get('available') and r.get('path')}
    branches = {r.get('branch') for r in original if r.get('available') and r.get('branch')}
    if re.search(r'(?i)dpclang', issue['title'] + ' ' + (issue.get('body') or '')[:1800]):
        paths.add('.github/workflows/pull_dpclang.yml')
    release = re.search(r'(?i)release[/ ](\d+\.\d+)', issue['title'])
    checks = []
    checked = 0
    for case in cases:
        if case['status'] != 'passed':
            continue
        checked += 1
        notes = []
        scope = case['evidence'][-1].get('configuration', {}) if case['evidence'] else {}
        actual_runs = [runs.get(ref,{}) for ref in scope.get('runs', []) if runs.get(ref,{}).get('available')]
        if not scope.get('same_configuration'):
            if not paths:
                notes.append('原报告 workflow 未确认')
            elif not actual_runs:
                notes.append('通过记录缺少可核对的 workflow run')
            elif not paths.intersection(r.get('path') for r in actual_runs):
                notes.append('通过记录与原报告 workflow 不同')
            if branches and actual_runs and not branches.intersection(r.get('branch') for r in actual_runs):
                notes.append('通过记录与原报告分支不同')
            if expected['hardware'] and not set(expected['hardware']).issubset(scope.get('hardware', [])):
                notes.append('通过记录未覆盖原报告全部硬件')
            if expected['os'] and not set(expected['os']).issubset(scope.get('os', [])):
                notes.append('通过记录未覆盖原报告全部操作系统')
        if release and not any(v.startswith(release.group(1)) for v in scope.get('versions', [])):
            notes.append('通过记录未确认目标 release/' + release.group(1))
        if not notes and not expected['hardware'] and not expected['os'] and not scope.get('same_configuration'):
            notes.append('原报告平台范围需人工确认')
        case['configuration_status'] = '待确认' if notes else '已匹配公开配置字段'
        case['configuration_notes'] = notes
        checks.extend(notes)
    return {'status': '需要核对配置' if checks else '已匹配公开配置字段' if checked else '无通过记录，配置待核对' if cases else '覆盖未知',
            'checks': list(dict.fromkeys(checks)), 'expected': expected,
            'original_runs': original}
