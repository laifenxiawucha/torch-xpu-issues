"""Conservative dimensions from public evidence; no verification claims from keywords alone."""
import re

IMPACT_TYPES=['正确性','性能','崩溃/稳定性','算子/API 不支持','测试/CI 基础设施故障','测试预期需核对','功能/改进','客户线索','影响未知']

def found(pattern,text):
 return bool(re.search(pattern,text,re.I|re.M))

def reported_versions(body):
 """Separate explicit report environments from comparison baselines."""
 reported=[];baseline=[];ambiguous=[];section='';pending=False
 pattern=re.compile(r'(?i)(?:PyTorch(?: version)?\s*[:=]\s*`?|torch\s*==\s*|\[pip3\]\s*torch\s+)(2\.\d+[^\s`<>,;)]*)')
 for line in body.splitlines():
  if re.match(r'^\s*#{1,6}\s',line):section=line.lower()
  context=(line+' '+section).lower()
  values=pattern.findall(line)
  if not values and re.search(r'baseline|target|current|reported|基线|目标',line,re.I):
   values+=re.findall(r'(?i)(?:release/|PyTorch\s+|\bPT)(2\.\d+(?:\.\d+)?)',line)
  if pending:
   values+=re.findall(r'^\s*`?(2\.\d+[^\s`<>,;)]*)',line)
   if line.strip():pending=False
  if re.fullmatch(r'\s*(?:#{1,6}\s*)?pytorch(?: version)?\s*:?\s*',line,re.I):pending=True
  for value in values:
   if re.search(r'baseline|comparison|reference version|previous version|last (?:good|passing)|对比|基线',line,re.I):target=baseline
   elif re.search(r'baseline|comparison|previous version',section,re.I) and not re.search(r'target|current|reported',line,re.I):target=baseline
   elif re.search(r'PyTorch(?: version)?\s*[:=]|\[pip3\]|target|current|reported|environment|versions?|目标|环境',context,re.I):target=reported
   else:target=ambiguous
   if value not in target:target.append(value)
 return {'reported':reported,'baseline':baseline,'ambiguous':ambiguous}

def classify_impact(title,labels,body=''):
 text=title+' '+labels
 rules=[
  ('正确性',r'accuracy|wrong|incorrect|mismatch|gradcheck|not close|not equal|invalid (?:index|indices)|silently|RuntimeError not raised|AssertionError[^\n]*(?:equal|close)|error.checking|instead of[^\n]*(?:error|exception)'),
  ('崩溃/稳定性',r'crash|segfault|\bSIGFPE\b|\bSIGSEGV\b|\babort(?:ed|s)?\b|\bhang(?:s|ing)?\b|deadlock|host (?:may )?stuck'),
  ('性能',r'performance|\bperf\b|\bslow(?:er|down)?\b|latency|throughput'),
  ('算子/API 不支持',r'notimplemented|not implemented|not supported|unsupported|missing[^\n]*(?:kernel|backend|op support)|does not implement'),
  ('测试/CI 基础设施故障',r'INSTALL_TEST|libtorchbind_test|(?:missing|not found|cannot find)[^\n]*(?:test librar|pytest|test dependenc)|(?:ImportError|ModuleNotFoundError)[^\n]*(?:test_|pytest)|test[^\n]*ImportError|(?:test|ci|runner|pipeline|fixture)[^\n]*(?:misconfigur|configuration error|missing librar|missing dependenc|test harness)|ci[^\n]*(?:script error|setup fail|disabled[^\n]*librar)'),
  ('测试预期需核对',r'Unexpected success|unexpectedly pass|xfail[^\n]*(?:remov|outdated)'),
  ('功能/改进',r'\bfeature request\b|\benhancement\b'),
  ('客户线索',r'\bclient\b')]
 impacts=[name for name,pattern in rules if found(pattern,text)]
 failure_text=re.split(r'(?mi)^#+\s*Versions\s*$|^Versions\s*$|Collecting environment information',body,maxsplit=1)[0]
 body_rules=[
  ('正确性',r'Tensor-likes are not close|Scalars are not equal|Mismatched elements:|Greatest absolute difference:|AssertionError:[^\n]*(?:not equal|not close|incorrect|wrong)|RuntimeError not raised'),
  ('崩溃/稳定性',r'Fatal Python error: Segmentation fault|Aborted \(core dumped\)|Floating point exception|signal (?:SIGSEGV|SIGFPE)|double free or corruption'),
  ('算子/API 不支持',r'NotImplementedError:[^\n]*(?:XPU|not implemented|not supported)|not currently implemented for the XPU|Could not run[^\n]*XPU[^\n]*backend'),
  ('测试/CI 基础设施故障',r'ModuleNotFoundError:[^\n]*(?:pytest|test_)|(?:cannot open|missing|not found)[^\n]*libtorchbind_test|cannot import name[^\n]*from [\x27\"]test_')]
 # Add precise body symptoms even if another title symptom is already known.
 impacts.extend(name for name,pattern in body_rules if found(pattern,failure_text) and name not in impacts)
 return impacts or ['影响未知']

def record_nature(title,labels,body,scope,impacts):
 if scope=='跟踪/规划':return '跟踪/规划事项'
 if found(r'\bbug\b|defect',labels) or any(x in impacts for x in ['正确性','崩溃/稳定性','测试/CI 基础设施故障','测试预期需核对']):return '缺陷/失败线索'
 if found(r'fail|error|assert|crash|wrong|regress|mismatch|incorrect|instead of',title):return '缺陷/失败线索'
 if found(r'feature request|enhancement|request (?:to|for)|add support|support request',title+' '+labels) or found(r'^\s*#+\s*🚀\s*The feature',body):return '功能/改进请求'
 if '算子/API 不支持' in impacts:return '支持范围缺口（性质待核实）'
 return '性质待核实'

def discovery_channels(title,labels,test_paths):
 channels=[];text=title+' '+labels
 if test_paths or found(r'test:\s*ut|\bunit tests?\b|\bUT\b',text):channels.append('UT')
 if found(r'test:\s*e2e|\be2e\b|torchbench|timm_',text):channels.append('E2E / 模型测试')
 if found(r'\bCI\b|ci-failure|windows_ci|ut_upstream|actions-run',text):channels.append('CI')
 return channels or ['渠道未识别']

def reproduction_clues(body,test_paths,reported_version,markdown=''):
 commands=[];code_lines=[]
 for line in body.splitlines():
  if re.match(r'^\s*(?:import (?:torch|numpy)\b|from (?:torch|numpy)[ .]|def (?:test_|repro|main)|[\w, ]+\s*=\s*(?:torch\.|[\w]+\.(?:cuda|xpu|to|backward)\()|torch\.(?:testing\.|compile\(|manual_seed\()|[\w]+\.backward\()',line):code_lines.append(line)
  if re.match(r'^\s*(?:\$\s*)?(?:pytest\s+|python[23]?(?:\s+-m\s+(?:pytest|unittest)|\s+[^\s]*repro[^\s]*\.py|\s+-c\s))',line):commands.append(line)
 blocks=[]
 for language,code in re.findall(r'^```([^\n]*)\n(.*?)^```\s*$',markdown,re.M|re.S):
  if language.strip().lower() in ['python','py'] or found(r'(?m)^\s*(?:import torch\b|from torch[ .]|def test_)',code):blocks.append(code.rstrip('\n'))
 code_present=bool(blocks) or len(code_lines)>=2
 materials=[]
 if test_paths:materials.append('有测试入口')
 if code_present:materials.append('有代码片段线索')
 if commands:materials.append('有复现命令线索')
 if reported_version:materials.append('有报告版本')
 gaps=[]
 if not test_paths:gaps.append('未提取到测试入口')
 if not code_present:gaps.append('未提取到代码片段')
 if not reported_version:gaps.append('未提取到报告版本')
 return {'repro_materials':'；'.join(materials) or '未提取到上述材料','repro_gaps':'；'.join(gaps) or '入口、代码、报告版本均有线索','repro_code_present':'1' if code_present else '0','repro_commands':'\n'.join(commands[:8]),'repro_code_excerpt':'\n'.join(code_lines[:16]) if code_present else '', 'repro_code_blocks':blocks,'latest_verified_version':'未确认'}

def verification_candidates(comments):
 candidates=[]
 for c in reversed(comments):
  if c['is_bot'] or c.get('is_generated'):continue
  for line in c['text'].splitlines():
   if found(r'(?i)\b(?:verified (?:with|on|using)|retested (?:with|on)|re-tested (?:with|on)|still fails?|no longer reproducible|not reproduced|cannot reproduce|test(?:s)? pass(?:es|ed)?|passes|still exists)\b',line) and not found(r'\b(?:please|will|should|can we|could you|need to|to be)\b',line):
    candidates.append({'date':c['created_at'][:10],'author':c['author'],'excerpt':line.strip()[:900],'source':'人工评论中的验证线索，未人工确认'})
    break
 return candidates[:8]

def blocker_info(labels,latest_text):
 dependency_labels=[s.strip() for s in labels.split(';') if s.strip().startswith('dependency component:')]
 if dependency_labels:return '存在依赖线索','原始标签：'+'; '.join(dependency_labels)
 for line in latest_text.splitlines():
  if found(r'blocked by|depends on|waiting (?:for|on) (?:upstream|onednn|oneapi|driver)|等待.*(?:上游|驱动)',line):return '存在依赖线索','最近人工评论：'+line.strip()[:500]
 return '未记录明确阻塞','公开结构化信息不足；负责人、标签或评论间隔不作为阻塞事实'

def repair_summary(linked):
 explicit=[p for p in linked if p['explicit_fix_reference']]
 opened=[p for p in explicit if p['state']=='OPEN'];merged=[p for p in explicit if p['state']=='MERGED']
 status='明确关闭引用 PR 进行中' if opened else '明确关闭引用 PR 已合并' if merged else '明确关闭引用 PR 已关闭未合并' if explicit else '仅见普通交叉引用' if linked else '未见 PR 引用'
 return {'explicit':explicit,'opened':opened,'merged':merged,'status':status,'ci_failed':any(p['checks'] in ['FAILURE','ERROR'] for p in opened),'relation':'有明确关闭引用' if explicit else '仅见普通交叉引用' if linked else '未见 PR 引用'}
