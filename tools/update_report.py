#!/usr/bin/env python3
"""Read-only GitHub snapshot collector. Uses the existing gh authentication."""
import argparse,csv,json,re,subprocess,os,time
from closure_review import followup_references
from evidence_context import execution_refs
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent.parent
CACHE=Path(__file__).resolve().parent/'snapshots'
QUERY='''query($cursor:String) { repository(owner:"intel",name:"torch-xpu-ops") {
 issues(states:OPEN,first:25,after:$cursor,orderBy:{field:CREATED_AT,direction:ASC}) {
 totalCount pageInfo {hasNextPage endCursor} nodes {
 id number title body bodyText createdAt updatedAt state author {login __typename}
 labels(first:100){totalCount nodes{name}} assignees(first:100){totalCount nodes{login}}
 milestone{title dueOn state} comments(last:50){totalCount pageInfo{hasPreviousPage startCursor} nodes{id createdAt body bodyText author{login __typename}}}
 timelineItems(last:50,itemTypes:[CROSS_REFERENCED_EVENT,REOPENED_EVENT]){
 totalCount pageInfo{hasPreviousPage startCursor} nodes{
 __typename ... on CrossReferencedEvent{createdAt source{__typename ... on PullRequest{id number title state mergedAt updatedAt repository{nameWithOwner}}}}
 ... on ReopenedEvent{createdAt}
 }}
 } } } }'''
PR_QUERY='''query($ids:[ID!]!){nodes(ids:$ids){... on PullRequest{
 id number title state mergedAt updatedAt isDraft reviewDecision mergeable mergeStateStatus
 repository{nameWithOwner} baseRefName headRefName additions deletions changedFiles
 reviewRequests(first:100){totalCount nodes{requestedReviewer{... on User{login} ... on Team{name}}}}
 closingIssuesReferences(first:100){totalCount nodes{number repository{nameWithOwner}}}
 commits(last:1){nodes{commit{oid committedDate statusCheckRollup{state contexts(first:100){totalCount nodes{
 __typename ... on CheckRun{name conclusion status} ... on StatusContext{context state}
 }}}}}}
}}}'''
def api(query,variables=None,allow_missing=False):
 payload={'query':query,'variables':variables or {}}
 env=os.environ.copy();env.pop('GH_DEBUG',None)
 for attempt in range(4):
  proc=subprocess.run(['gh','api','graphql','--input','-'],input=json.dumps(payload),text=True,capture_output=True,env=env)
  if proc.stdout.strip():
   try:data=json.loads(proc.stdout)
   except json.JSONDecodeError:data={}
   if allow_missing and data.get('data') and data.get('errors') and all(e.get('type')=='NOT_FOUND' for e in data['errors']):return data['data']
  if proc.returncode==0:
   data=json.loads(proc.stdout)
   if not data.get('errors'):return data['data']
   problem=str(data['errors'])
  else:problem=proc.stderr[-1200:]
  if attempt==3:raise RuntimeError(problem)
  print('Retry:',problem[:400],flush=True)
  time.sleep(2**attempt)
def verify_open_set(rows):
 expected={i['number'] for i in rows};actual=set();cursor=None
 query='query($cursor:String){repository(owner:"intel",name:"torch-xpu-ops"){issues(states:OPEN,first:100,after:$cursor,orderBy:{field:CREATED_AT,direction:ASC}){totalCount pageInfo{hasNextPage endCursor} nodes{number}}}}'
 while True:
  connection=api(query,{'cursor':cursor})['repository']['issues']
  actual.update(i['number'] for i in connection['nodes'])
  if not connection['pageInfo']['hasNextPage']:break
  cursor=connection['pageInfo']['endCursor']
 assert actual==expected and len(actual)==connection['totalCount'], 'Open issue set changed during collection; rerun required.'
 return datetime.now(timezone.utc).isoformat()
def current_open_numbers():
 numbers=set();cursor=None
 query='query($cursor:String){repository(owner:"intel",name:"torch-xpu-ops"){issues(states:OPEN,first:100,after:$cursor,orderBy:{field:CREATED_AT,direction:ASC}){totalCount pageInfo{hasNextPage endCursor} nodes{number}}}}'
 while True:
  connection=api(query,{'cursor':cursor})['repository']['issues']
  numbers.update(i['number'] for i in connection['nodes'])
  if not connection['pageInfo']['hasNextPage']:break
  cursor=connection['pageInfo']['endCursor']
 assert len(numbers)==connection['totalCount'],'Open set changed while checking; rerun required.'
 return numbers
def collect():
 started=datetime.now(timezone.utc).isoformat();rows=[];cursor=None;total=None
 while True:
  batch=api(QUERY,{'cursor':cursor})['repository']['issues'];total=batch['totalCount'];rows.extend(batch['nodes'])
  print(f'Issues: {len(rows)}/{total}',flush=True)
  if not batch['pageInfo']['hasNextPage']:break
  cursor=batch['pageInfo']['endCursor']
 # Remove issues closed during pagination, then independently verify the set.
 # New/reopened issues require another collection rather than an incomplete page.
 actual=current_open_numbers();rows=[i for i in rows if i['number'] in actual];total=len(actual)
 assert {i['number'] for i in rows}==actual,'New/reopened issue appeared during collection; rerun required.'
 for issue in rows:
  for key in ['labels','assignees']:
   assert issue[key]['totalCount']==len(issue[key]['nodes']), 'Truncated '+key+' connection; update collector before publishing.'
  # Complete truncated comment and timeline connections rather than silently missing evidence.
  for key,selection in [('comments','id createdAt body bodyText author{login __typename}'),('timelineItems','__typename ... on CrossReferencedEvent{createdAt source{__typename ... on PullRequest{id number title state mergedAt updatedAt repository{nameWithOwner}}}} ... on ReopenedEvent{createdAt}')]:
   connection=issue[key]
   while connection['pageInfo']['hasPreviousPage']:
    args=',itemTypes:[CROSS_REFERENCED_EVENT,REOPENED_EVENT]' if key=='timelineItems' else ''
    query='query($id:ID!,$before:String){node(id:$id){... on Issue{'+key+'(last:100,before:$before'+args+'){totalCount pageInfo{hasPreviousPage startCursor} nodes{'+selection+'}}}}}'
    older=api(query,{'id':issue['id'],'before':connection['pageInfo']['startCursor']})['node'][key]
    connection['nodes']=older['nodes']+connection['nodes'];connection['pageInfo']=older['pageInfo']
  assert issue['comments']['totalCount']==len(issue['comments']['nodes'])
  assert issue['timelineItems']['totalCount']==len(issue['timelineItems']['nodes'])
 ids=sorted({event['source']['id'] for i in rows for event in i['timelineItems']['nodes'] if event.get('source',{}).get('__typename')=='PullRequest'})
 prs=[]
 for pos in range(0,len(ids),5):
  prs.extend(p for p in api(PR_QUERY,{'ids':ids[pos:pos+5]})['nodes'] if p)
  print(f'Linked PRs: {min(pos+5,len(ids))}/{len(ids)}',flush=True)
 for pr in prs:
  for key in ['closingIssuesReferences','reviewRequests']:
   assert pr[key]['totalCount']==len(pr[key]['nodes']), 'Truncated '+key+' connection; update collector before publishing.'
 actual=current_open_numbers();rows=[i for i in rows if i['number'] in actual];total=len(actual)
 assert len({i['number'] for i in rows})==len(rows)==total, 'New/reopened issue appeared during collection; rerun required.'
 used_ids={event['source']['id'] for i in rows for event in i['timelineItems']['nodes'] if event.get('source',{}).get('__typename')=='PullRequest'}
 prs=[p for p in prs if p['id'] in used_ids]
 verified_at=verify_open_set(rows)
 raw={'open_set_verified_at':verified_at,'repository':'intel/torch-xpu-ops','started_at':started,'fetched_at':datetime.now(timezone.utc).isoformat(),'total_count':total,'issues':rows,'prs':prs}
 CACHE.mkdir(parents=True,exist_ok=True)
 (CACHE/'collection-checkpoint.json').write_text(json.dumps(raw,ensure_ascii=False))
 raw['review_targets']=collect_review_targets(raw)
 raw['workflow_runs']=collect_workflow_runs(raw)
 return raw

def collect_workflow_runs(raw):
 refs=set()
 for issue in raw['issues']:
  refs.update(execution_refs(issue.get('body') or issue['bodyText']))
  for comment in issue['comments']['nodes']:
   if 'Passed in' in comment['bodyText'] and 'nightly testing' in comment['bodyText']:
    refs.update(execution_refs(comment.get('body') or comment['bodyText']))
 def fetch(ref):
  repo,run=ref.split('@run:');env=os.environ.copy();env.pop('GH_DEBUG',None)
  for attempt in range(3):
   proc=subprocess.run(['gh','api',f'repos/{repo}/actions/runs/{run}'],text=True,capture_output=True,env=env,timeout=35)
   if not proc.returncode:
    data=json.loads(proc.stdout)
    return ref,{'reference':ref,'available':True,'repository':repo,'run_id':run,'name':data['name'],'path':data['path'],'branch':data['head_branch'],'commit':data['head_sha'],'conclusion':data['conclusion'],'event':data['event'],'created_at':data['created_at']}
   if '404' in proc.stderr or '403' in proc.stderr:
    return ref,{'reference':ref,'available':False,'reason':'运行记录已不可访问；配置保持待确认'}
   if attempt==2:raise RuntimeError('Cannot verify workflow run '+ref+': '+proc.stderr[-250:])
   time.sleep(2**attempt)
 with ThreadPoolExecutor(max_workers=4) as pool:
  results=dict(pool.map(fetch,sorted(refs)))
 print(f'Workflow runs: {len(results)} checked, {sum(r["available"] for r in results.values())} available',flush=True)
 return results

def collect_review_targets(raw):
 refs=set()
 for issue in raw['issues']:
  refs.update(followup_references(issue.get('body') or issue['bodyText']))
  for comment in issue['comments']['nodes']:
   author=comment.get('author') or {}
   if author.get('__typename')!='Bot' and not author.get('login','').lower().endswith(('bot','[bot]')):
    refs.update(followup_references(comment.get('body') or comment['bodyText']))
 targets={};items=sorted(refs)
 for pos in range(0,len(items),30):
  batch=items[pos:pos+30];parts=[]
  for idx,ref in enumerate(batch):
   repo,number=ref.rsplit('#',1);owner,name=repo.split('/')
   parts.append('r'+str(idx)+':repository(owner:'+json.dumps(owner)+',name:'+json.dumps(name)+'){item:issueOrPullRequest(number:'+number+'){__typename ...on Issue{number title issueState:state updatedAt} ...on PullRequest{number title prState:state updatedAt}}}')
  result=api('query{'+''.join(parts)+'}',allow_missing=True)
  for idx,ref in enumerate(batch):
   node=(result.get('r'+str(idx)) or {}).get('item')
   if node:targets[ref]={'type':node['__typename'],'state':node.get('issueState') or node.get('prState'),'title':node['title'],'updated_at':node['updatedAt']}
  print(f'Review follow-ups: {min(pos+30,len(items))}/{len(items)}',flush=True)
 return targets
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--collect-only',action='store_true');p.add_argument('--cached',type=Path);p.add_argument('--output-dir',type=Path,default=ROOT);args=p.parse_args()
 if args.cached:raw=json.loads(args.cached.read_text())
 else:
  raw=collect();CACHE.mkdir(parents=True,exist_ok=True);path=CACHE/(raw['fetched_at'][:10]+'.json');path.write_text(json.dumps(raw,ensure_ascii=False));print('Saved snapshot:',path,flush=True)
 if not args.collect_only:
  from render_report import render
  args.output_dir.mkdir(parents=True,exist_ok=True)
  render(raw,args.output_dir)
