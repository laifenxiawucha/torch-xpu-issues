"""Rebuildable standalone report: cached snapshot + explicit reviewed overrides."""
import csv,json,re
from datetime import datetime,timezone,timedelta
from pathlib import Path
from issue_facts import (IMPACT_TYPES, classify_impact,record_nature,discovery_channels,reproduction_clues,verification_candidates,blocker_info,repair_summary,reported_versions)
from closure_review import review_issue, CASE_STATUS
from evidence_context import generated_analysis, evidence_source
from team_review import review_summary, TEAM_STATES, ROLE_LABELS
REPO='intel/torch-xpu-ops'
URL=re.compile(r'(?:https?://|www\.)[^\s<>\]\)"\x27]+',re.I)
SCOPES=['算子/kernel','编译器/图执行','分布式/XCCL','运行时/驱动/设备','模型/端到端（根因待定位）','量化/torchao','对外 API/语义','Profiler/性能工具','构建/安装/工具链','测试/CI 基础设施','跟踪/规划','文档/术语','未知']
def clean(value):
 if isinstance(value,str):
  def replacement(hit):
   # Markdown delimiters can touch a bare URL. They describe the surrounding
   # task, so removing the address must not erase its closing strike/check.
   suffix=re.search(r'([~*`]+[.,;:!?]*)$',hit.group(0))
   tail=suffix.group(1) if suffix else ''
   run=re.search(r'github\.com/([\w.-]+/[\w.-]+)/actions/runs/(\d+)',hit.group(0),re.I)
   if run:return '[运行记录：'+run.group(1)+'@run:'+run.group(2)+']'+tail
   ref=re.search(r'github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)',hit.group(0),re.I)
   return ('[地址已移除：'+ref.group(1)+'#'+ref.group(2)+']' if ref else '[地址已移除]')+tail
  return URL.sub(replacement,value)
 if isinstance(value,list):return [clean(v) for v in value]
 if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
 return value
def compact(value,limit=500):return re.sub(r'\s+',' ',clean(value or '')).strip()[:limit]
def match(pattern,text):return bool(re.search(pattern,text,re.I))
def extract(pattern,text,limit=12):return list(dict.fromkeys(re.findall(pattern,text,re.I)))[:limit]
def explicit_source_paths(text):
 return extract(r'(?:(?:aten|csrc|src|include|torch|sycl|kernels)/[\w./-]+\.(?:cpp|cuh|cu|cc|c|hpp|h|sycl))\b',text,50)
def bucket(days):return '>365' if days>365 else '181-365' if days>180 else '91-180' if days>90 else '31-90' if days>30 else '0-30'
def encode(value):return json.dumps(clean(value),ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
def classify(title,body,labels):
 if '测试/CI 基础设施故障' in classify_impact(title,labels,body):return '测试/CI 基础设施','标题或正文的明确基础设施故障线索','规则推断'
 rules=[('跟踪/规划',r'\btracker\b|\btracking\b|checklist|\bRFC\b|known issues list|auto skip|periodic run'),('量化/torchao',r'torchao|quantiz|pt2e|\b(?:INT[48]|FP8)\b.*(?:GEMM|WOQ|RTN)'),('分布式/XCCL',r'distributed|xccl|symm_mem|dtensor|torchrun|c10d|sharding|collective'),('Profiler/性能工具',r'profiler|profiling|\bpti\b|unitrace'),('构建/安装/工具链',r'cmake|installation|install wheel|build fail|linker|C\+\+ extension|setup.py'),('运行时/驱动/设备',r'\bdriver\b|oneapi|sycl (?:rt|runtime)|\bur error\b|device context|outofmemory|\boom\b|device_properties|ipc_handle'),('模型/端到端（根因待定位）',r'test: e2e|torchbench|timm_|transformers.*training|model.*accuracy'),('编译器/图执行',r'\binductor\b|\bdynamo\b|torch\.compile|compiled_autograd|aotinductor|\baoti\b|dynamic_shapes|graph.*capture|xpu.graph|graph_break'),('对外 API/语义',r'RuntimeError not raised|error.checking|invalid.*argument|instead of.*(?:error|exception)'),('算子/kernel',r'aten[.:]|torch\.linalg|test_decomp|test_foreach|kernel|convolution|conv[123]d|softmax|pool[123]d|\btopk\b|\bkthvalue\b|\bmedian\b|matmul|addmm|interpolate|layer.?norm|instance_norm|\bgelu\b|scatter|gather|reduce|reduction|test_nn|test_unary|test_ops|test_linalg'),('文档/术语',r'documentation|terminology|doc.*typo')]
 explicit_modules={'ops':'算子/kernel','inductor':'编译器/图执行','dynamo':'编译器/图执行','sdpa':'算子/kernel','distributed':'分布式/XCCL','profiler':'Profiler/性能工具'}
 for module,scope in explicit_modules.items():
  if re.search(r'(?i)module: '+re.escape(module)+r'(?:;|$)',labels):return scope,'原始模块标签：module: '+module,'规则推断'
 for scope,pattern in rules:
  hit=re.search(pattern,title+' '+labels,re.I)
  if hit:return scope,'标题/原始标签：'+hit.group(0),'规则推断'
 # Avoid environment appendices when classifying technical scope.
 opening=re.split(r'Collecting environment information|(?mi:^#+\s*Versions\s*$|^Versions\s*$)',body,maxsplit=1)[0][:2000]
 for scope,pattern in rules[1:]:
  hit=re.search(pattern,opening,re.I)
  if hit:return scope,'正文技术线索：'+hit.group(0),'待核查'
 return '未知','未找到足够明确的技术层证据','待核查'
def pr_details(p):
 commit=(p.get('commits',{}).get('nodes') or [{}])[-1].get('commit',{})
 rollup=commit.get('statusCheckRollup') or {};connection=rollup.get('contexts') or {};checks=connection.get('nodes',[])
 failed=[c.get('name',c.get('context','')) for c in checks if c.get('conclusion') in ['FAILURE','TIMED_OUT','CANCELLED','ACTION_REQUIRED','STARTUP_FAILURE'] or c.get('state') in ['FAILURE','ERROR']]
 if p['state']=='MERGED':stage='已合并'
 elif p['state']=='CLOSED':stage='已关闭未合并'
 elif p['isDraft']:stage='草稿'
 elif p['mergeable']=='CONFLICTING':stage='存在合并冲突'
 elif p['reviewDecision']=='CHANGES_REQUESTED':stage='审查请求修改'
 elif rollup.get('state') in ['FAILURE','ERROR']:stage='CI 失败'
 elif p['reviewDecision']=='REVIEW_REQUIRED':stage='等待审查'
 elif rollup.get('state') in ['PENDING','EXPECTED']:stage='CI 进行中'
 elif p['mergeStateStatus']=='CLEAN' and rollup.get('state')=='SUCCESS':stage='检查通过，合入条件待核对'
 else:stage='Open，状态待核查'
 return clean({'author':(p.get('author') or {}).get('login',''),'review_users':[n['requestedReviewer']['login'] for n in p['reviewRequests']['nodes'] if (n.get('requestedReviewer') or {}).get('login')],'review_teams':[n['requestedReviewer'].get('slug',n['requestedReviewer'].get('name','')) for n in p['reviewRequests']['nodes'] if (n.get('requestedReviewer') or {}).get('__typename')=='Team' or (n.get('requestedReviewer') or {}).get('name')],'id':p['id'],'number':p['number'],'repository':p['repository']['nameWithOwner'],'title':p['title'],'state':p['state'],'stage':stage,'is_draft':p['isDraft'],'review':p['reviewDecision'] or '未提供','checks':rollup.get('state','未提供'),'mergeable':p['mergeable'],'merge_state':p['mergeStateStatus'],'failed_checks':failed,'check_details_complete':len(checks)==connection.get('totalCount',0),'base':p['baseRefName'],'head':p['headRefName'],'updated_at':p['updatedAt'],'merged_at':p.get('mergedAt'),'changed_files':p['changedFiles'],'commit':commit.get('oid',''),'review_requests':[n['requestedReviewer'].get('login',n['requestedReviewer'].get('name','')) for n in p['reviewRequests']['nodes'] if n.get('requestedReviewer')]})
def load_overrides(path,ids):
 if not path.exists():return {}
 data=json.loads(path.read_text());assert data.get('schema_version')==1,'Unsupported override schema'
 issues=data.get('issues',{});allowed={'surface_scope','impact_types','public_record_status','primary_blocker','verification'}
 for number,item in issues.items():
  assert number.isdigit(),'Override issue number must be numeric: '+number
  assert all(isinstance(item.get(k),str) and item[k].strip() for k in ['reason','reviewer','reviewed_at','evidence']),'Override requires reason, reviewer, reviewed_at and evidence: '+number
  datetime.fromisoformat(item['reviewed_at'])
  changes=item.get('set',{});assert changes and set(changes)<=allowed,'Unsupported override fields: '+number
  if 'surface_scope' in changes:assert changes['surface_scope'] in SCOPES
  if 'impact_types' in changes:assert isinstance(changes['impact_types'],list) and changes['impact_types'] and all(x in IMPACT_TYPES for x in changes['impact_types'])
  if 'verification' in changes:
   v=changes['verification'];assert all(isinstance(v.get(k),str) and v[k] for k in ['version','date','result','hardware','evidence'])
   datetime.fromisoformat(v['date']);assert v['result'] in ['仍复现','未复现','部分解决']
 # Reviewed issues can close; retain their attribution in the config and skip
 # archived records when rebuilding the current open set.
 return clean({number:item for number,item in issues.items() if number in ids})
def render(raw,root):
 raw=clean(raw)
 snapshot=datetime.fromisoformat(raw['fetched_at']).astimezone(timezone.utc);day=snapshot.date();stamp=day.isoformat();root=Path(root)
 raw_prs={p['id']:p for p in raw['prs']};prs={id:pr_details(p) for id,p in raw_prs.items()}
 overrides=load_overrides(Path(__file__).parent/'overrides.json',{str(i['number']) for i in raw['issues']})
 closing_index={}
 for pr in raw['prs']:
  for ref in pr['closingIssuesReferences']['nodes']:
   if ref['repository']['nameWithOwner']==REPO:closing_index.setdefault(ref['number'],[]).append(pr['id'])
 rows=[];details={}
 for i in raw['issues']:
  number=i['number'];key=str(number);title=i['title'];body=i['bodyText'];markdown=i.get('body','');label_list=[n['name'] for n in i['labels']['nodes']];labels='; '.join(label_list)
  age=(day-datetime.fromisoformat(i['createdAt'].replace('Z','+00:00')).date()).days
  comments=clean([{'id':c.get('id',''),'author':c['author']['login'] if c.get('author') else '账号不可见','is_bot':not c.get('author') or c['author']['__typename']=='Bot' or c['author']['login'].lower().endswith(('bot','[bot]')),'created_at':c['createdAt'],'text':c['bodyText'],'markdown':c.get('body','')} for c in i['comments']['nodes']])
  for c in comments:
   c['is_generated']=generated_analysis(c['markdown'] or c['text']);c['provenance']=evidence_source(c['markdown'] or c['text'],c['is_bot'])
  humans=[c for c in comments if not c['is_bot'] and not c['is_generated']];last=humans[-1] if humans else None
  linked_ids=list(dict.fromkeys(e['source']['id'] for e in i['timelineItems']['nodes'] if e.get('source',{}).get('__typename')=='PullRequest'))
  linked_ids=list(dict.fromkeys(linked_ids+closing_index.get(number,[])))
  linked=[dict(prs[id],explicit_fix_reference=any(x['number']==number and x['repository']['nameWithOwner']==REPO for x in raw_prs[id]['closingIssuesReferences']['nodes'])) for id in linked_ids if id in prs]
  linked.sort(key=lambda p:(not p['explicit_fix_reference'],p['state']!='OPEN',-p['number']));repair=repair_summary(linked)
  scope,scope_evidence,confidence=classify(title,body,labels);impacts=classify_impact(title,labels,body);nature=record_nature(title,labels,markdown or body,scope,impacts)
  blocker,blocker_evidence=blocker_info(labels,last['text'] if last else '')
  review=overrides.get(key)
  if review:
   changes=review['set']
   if 'surface_scope' in changes:
    scope=changes['surface_scope'];scope_evidence='人工核查：'+review['evidence'];confidence='人工核查'
   if 'impact_types' in changes:impacts=changes['impact_types']
   nature=changes.get('public_record_status',record_nature(title,labels,markdown or body,scope,impacts))
   if 'primary_blocker' in changes:blocker=changes['primary_blocker'];blocker_evidence='人工核查：'+review['evidence']
  skip=any(x.startswith('skipped') for x in label_list);regression='regression' in label_list
  paths=extract(r'(?:test[/\\][\w./\\-]+\.py|\btest_[\w]+\.py)(?:::[\w.\[\],=:+-]+)*',title+'\n'+body,100)
  source_paths=explicit_source_paths(title+'\n'+body)
  apis=extract(r'\b(?:aten::[A-Za-z_][\w]*|symm_mem::[A-Za-z_][\w]*|torch\.(?:[A-Za-z_][\w]*\.)*[A-Za-z_][\w]*)',title+'\n'+body[:4000],8)
  versions=reported_versions(markdown or body);version=versions['reported'];reported_os=extract(r'(?m)^OS:\s*([^\n]+)',body,1)
  hardware='; '.join(l[4:] for l in label_list if l.startswith('hw: ')) or '; '.join(extract(r'\b(?:PVC|BMG|LNL|MTL|ARL|DG2|PTL)\b',title))
  osnames='; '.join(l[4:] for l in label_list if l.startswith('os: ')) or '; '.join(extract(r'\b(?:Windows|Linux|Ubuntu)\b',title))
  if not osnames and reported_os:osnames='Windows' if match('Windows',reported_os[0]) else 'Linux' if match('Ubuntu|Linux|Debian|CentOS|Red Hat',reported_os[0]) else reported_os[0][:80]
  release='; '.join(extract(r'(?:release/\d+\.\d+|PT\d+\.\d+|oneAPI\s*\d{4}\.\d+)',title+'\n'+body[:3000]))
  failure='; '.join(extract(r'(?:[A-Za-z_]+(?:Error|Exception)|SIGFPE|SIGSEGV|Unexpected success|not implemented|not supported|undefined symbol|device-side assert)',title+'\n'+body[:5000],8))
  clues=reproduction_clues(body,paths,version,markdown);candidates=verification_candidates(comments)
  fix_open=repair['opened'];fix_merged=repair['merged'];recent=bool(last and (day-datetime.fromisoformat(last['created_at'].replace('Z','+00:00')).date()).days<=30)
  progress='明确关闭引用 PR 进行中' if fix_open else '明确关闭引用 PR 已合并，验证待核对' if fix_merged else '明确关闭引用 PR 已关闭未合并' if repair['explicit'] else '仅见普通 PR 引用，进展待核对' if linked else '存在依赖线索' if blocker=='存在依赖线索' else '跟踪/规划事项' if scope=='跟踪/规划' else '近 30 天有人工评论' if recent else '带跳过标签，验证待核对' if skip else '当前公开进展待核对'
  action='核对跟踪清单与剩余工作' if scope=='跟踪/规划' else '核对测试预期与 xfail 标记' if '测试预期需核对' in impacts else '排查明确关闭引用 PR 的失败检查' if repair['ci_failed'] else '处理明确关闭引用 PR 的合并冲突' if any(p['mergeable']=='CONFLICTING' for p in fix_open) else '响应审查修改意见' if any(p['review']=='CHANGES_REQUESTED' for p in fix_open) else '核对已合并 PR 范围和复测记录' if fix_merged and not fix_open else '核对跳过条件与当前复测' if skip else '跟进明确关闭引用 PR 的审查与检查' if fix_open else '核对依赖版本与上游进展' if blocker=='存在依赖线索' else '确认回归的通过/失败版本' if regression else '用当前版本核对测试入口' if paths else '补充问题描述与复现材料'
  row={'issue':key,'title':title,'created_at':i['createdAt'][:10],'updated_at':i['updatedAt'][:10],'updated_at_utc':i['updatedAt'],'age_days':str(age),'age_bucket':bucket(age),'last_human_comment_at':last['created_at'][:10] if last else '', 'surface_scope':scope,'scope_evidence':scope_evidence,'classification_confidence':confidence,'public_record_status':nature,'impact_types':'；'.join(impacts),'impact_evidence':'未识别到具体影响线索' if impacts==['影响未知'] else '标题/原始标签或正文中的明确症状线索（规则推断）','primary_blocker':blocker,'blocker_evidence':blocker_evidence,'owner_status':'已设置 GitHub assignee' if i['assignees']['nodes'] else '未设置 GitHub assignee','assignees':'; '.join(n['login'] for n in i['assignees']['nodes']),'milestone':(i['milestone'] or {}).get('title',''),'labels_raw':labels,'regression_label':'1' if regression else '0','skipped_label':'1' if skip else '0','discovery_channels':'；'.join(discovery_channels(title,labels,paths)),'hardware_in_title':hardware,'os_in_title':osnames,'reported_os':reported_os[0] if reported_os else '', 'release_in_title':release,'reported_pytorch_version':version[0] if version else '', 'environment':' / '.join(v for v in [hardware,osnames,'PT '+version[0][:65] if version else release] if v) or '未提取到环境信息','test_paths':'; '.join(paths),'source_paths':'; '.join(source_paths),'api_family':apis[0] if apis else 'API 未识别','failure_signatures':failure,'comment_count':str(len(comments)),'current_progress':progress,'repair_pr_status':repair['status'],'pr_relation':repair['relation'],'explicit_fix_pr_count':str(len(repair['explicit'])),'cross_reference_pr_count':str(len(linked)-len(repair['explicit'])),'pr_ci_failed':'1' if repair['ci_failed'] else '0','pr_stage':'；'.join(dict.fromkeys(p['stage'] for p in fix_open)) if fix_open else repair['status'],'engineering_action':action,'repro_materials':clues['repro_materials'],'repro_gaps':clues['repro_gaps'],'repro_code_present':clues['repro_code_present'],'verification_status':'有待核查的人工评论线索' if candidates else '未提取到明确验证线索','verification_candidate_count':str(len(candidates)),'latest_verified_version':'未确认','classification_review':'未人工核查','linked_prs':[{'id':p['id'],'explicit_fix_reference':p['explicit_fix_reference']} for p in linked]}
  row['reported_pytorch_version']='；'.join(version)
  row['baseline_pytorch_version']='；'.join(versions['baseline'])
  row['other_version_clues']='；'.join(versions['ambiguous'])
  if review:
   for field,value in review['set'].items():
    if field=='verification':row['latest_verified_version']=value['version'];row['verification_status']='已人工核查验证记录'
    else:row[field]='；'.join(value) if isinstance(value,list) else value
   row['classification_review']='有人工核查记录';row['scope_evidence']='人工核查：'+review['evidence'] if 'surface_scope' in review['set'] else row['scope_evidence']
   if 'impact_types' in review['set']:row['impact_evidence']='人工核查：'+review['evidence']
  closing=review_issue(i,linked,raw.get('review_targets',{}),raw.get('workflow_runs',{}))
  row.update({'closure_candidate':'1' if closing['candidate'] else '0','closure_status':closing['status'],'closure_reasons':'；'.join(closing['reasons']) or '暂无关闭候选依据','closure_case_total':str(closing['total']),'closure_declared_total':str(closing['declared_total'] or ''),'closure_case_disposed':str(closing['disposed']),'closure_inventory_complete':'1' if closing['inventory_complete'] else '0','closure_blockers':'；'.join(closing['blockers']),'closure_checks':'；'.join(closing['checks']),'closure_latest_evidence':closing['latest_evidence_at']})
  for status,count in closing['counts'].items():row['closure_case_'+status]=str(count)
  row['closure_configuration_status']=closing['configuration']['status']
  row['target_branch']='；'.join(extract(r'\brelease/\d+\.\d+\b',title))
  row['merged_fix_pr_count']=str(len(fix_merged))
  row['review_kind']=closing['configuration']['kind']
  counts=closing['counts']
  row['next_action']=f"复核 {counts['failed']} 个仍失败或无法验证的用例" if closing['candidate'] and counts['failed'] else f"核对 {counts['conditional']+counts['workaround']} 个受限通过用例的原条件与后续处理" if closing['candidate'] and (counts['conditional'] or counts['workaround']) else '按原参数及编译器验证修复后的构建' if row['review_kind']=='构建问题' else f"补充 {counts['unknown']} 个用例的处理证据" if closing['candidate'] and counts['unknown'] else '确认 skip / xfail 的保留原因及跟踪去向' if closing['candidate'] and counts['skipped'] else '补充原环境及目标分支的复测证据' if closing['candidate'] and closing['configuration']['checks'] else action
  row['missing_evidence']='；'.join(dict.fromkeys(closing['blockers']+closing['checks'])) if closing['candidate'] or row['review_kind']=='构建问题' else row['repro_gaps']
  row['engineering_action']=row['next_action']
  detail={'body_text':body if not markdown else '','body_markdown':markdown,'comments':comments,'last_human_comment_excerpt':last['text'] if last else '', 'repro_commands':clues['repro_commands'],'repro_code_excerpt':clues['repro_code_excerpt'],'repro_code_blocks':clues['repro_code_blocks'],'verification_candidates':candidates,'review':review}
  detail['closure_review']=closing
  team=review_summary(i,closing,linked)
  row['issue_author']=(i.get('author') or {}).get('login','')
  row['review_people_summary']='；'.join('@'+p['login']+'（'+ROLE_LABELS[p['role']]+'，'+p['reference']+'）' for p in team['people'])
  row['review_pr_summary']='；'.join(p['repository']+'#'+str(p['number'])+' · '+p['state']+' · @'+(p['author'] or '未提取')+' · '+('明确关闭引用' if p['explicit_fix_reference'] else '普通交叉引用') for p in linked)
  row['team_review_candidate']='1' if team['candidate'] else '0'
  row['team_review_status']=team['status'] if team['candidate'] else '未提取到关闭线索'
  row['team_review_rank']=str(team['rank'])
  row['team_review_reasons']='；'.join(team['reasons'])
  row['team_review_blockers']='；'.join(team['blockers'])
  row['team_review_checks']='；'.join(team['checks'])
  row['body_task_summary']=team['body_summary']
  row['review_people']=team['people']
  detail['team_review']=team
  rows.append(clean(row));details[key]=clean(detail)
 data={'schema_version':6,'repository':REPO,'snapshot':stamp,'fetched_at':raw['fetched_at'],'started_at':raw['started_at'],'snapshot_local':snapshot.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),'rows':rows,'prs':prs,'impact_types':IMPACT_TYPES,'scopes':SCOPES,'case_statuses':CASE_STATUS,'team_states':TEAM_STATES,'team_roles':ROLE_LABELS,'people_fetched_at':raw.get('people_fetched_at',raw['fetched_at']),'workflow_runs':raw.get('workflow_runs',{}),'collection':{'comments_complete':True,'timelines_complete':True,'linked_pr_count':len(prs),'review_target_count':len(raw.get('review_targets',{})),'workflow_run_count':len(raw.get('workflow_runs',{})),'workflow_run_available':sum(bool(x.get('available')) for x in raw.get('workflow_runs',{}).values()),'markdown_comment_count':sum(bool(c.get('body')) for i in raw['issues'] for c in i['comments']['nodes']),'comment_count':sum(len(x['comments']) for x in details.values()),'open_set_verified_at':raw.get('open_set_verified_at',raw['fetched_at']),'issue_count_verified':raw['total_count']}}
 data['fields']=[k for k in rows[0] if k not in ('linked_prs','review_people')]
 inventory=root/f'torch-xpu-ops-issue-inventory-{stamp}.csv'
 for path,items in [(inventory,rows),(root/f'torch-xpu-ops-aged-issues-{stamp}.csv',[r for r in rows if int(r['age_days'])>180])]:
  with path.open('w',encoding='utf-8-sig',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=data['fields'],extrasaction='ignore',lineterminator='\n');writer.writeheader();writer.writerows(items)
 template=(Path(__file__).parent/'report-template.html').read_text();css=(Path(__file__).parent/'report.css').read_text();js=(Path(__file__).parent/'team-review.js').read_text()+'\n'+(Path(__file__).parent/'report.js').read_text()
 scripts='\n'.join('<script type="application/json" id="detail-'+key+'">'+encode(detail)+'</script>' for key,detail in details.items())
 html=template.replace('__REPORT_STYLE__',css).replace('__REPORT_SCRIPT__',js).replace('__REPORT_DATA__',encode(data)).replace('__DETAIL_SCRIPTS__',scripts).replace('__SNAPSHOT_LOCAL__',data['snapshot_local']).replace('__TOTAL__',str(len(rows)))
 assert not re.search(r'https?://|www\.',html,re.I),'No external URLs in report'
 compat=root/'torch-xpu-ops-issue-engineering-report-2026-09-23.html'
 target=compat if compat.exists() else root/'index.html';target.write_text(html)
 print(json.dumps({'snapshot':data['snapshot_local'],'issues':len(rows),'distinct_prs':len(prs),'comments':data['collection']['comment_count'],'html':str(target),'html_bytes':len(html.encode()),'initial_json_bytes':len(encode(data).encode())},ensure_ascii=False,indent=2))
 return data
