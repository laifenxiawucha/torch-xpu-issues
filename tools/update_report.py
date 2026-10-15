#!/usr/bin/env python3
"""Read-only GitHub snapshot collector. Uses the existing gh authentication."""
import argparse,csv,json,re,subprocess,os,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent.parent
CACHE=Path(__file__).resolve().parent/'snapshots'
QUERY='''query($cursor:String) { repository(owner:"intel",name:"torch-xpu-ops") {
 issues(states:OPEN,first:25,after:$cursor,orderBy:{field:CREATED_AT,direction:ASC}) {
 totalCount pageInfo {hasNextPage endCursor} nodes {
 id number title body bodyText createdAt updatedAt state author {login __typename}
 labels(first:100){totalCount nodes{name}} assignees(first:100){totalCount nodes{login}}
 milestone{title dueOn state} comments(last:50){totalCount pageInfo{hasPreviousPage startCursor} nodes{createdAt bodyText author{login __typename}}}
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
def api(query,variables=None):
 payload={'query':query,'variables':variables or {}}
 env=os.environ.copy();env.pop('GH_DEBUG',None)
 for attempt in range(4):
  proc=subprocess.run(['gh','api','graphql','--input','-'],input=json.dumps(payload),text=True,capture_output=True,env=env)
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
def collect():
 started=datetime.now(timezone.utc).isoformat();rows=[];cursor=None;total=None
 while True:
  batch=api(QUERY,{'cursor':cursor})['repository']['issues'];total=batch['totalCount'];rows.extend(batch['nodes'])
  print(f'Issues: {len(rows)}/{total}',flush=True)
  if not batch['pageInfo']['hasNextPage']:break
  cursor=batch['pageInfo']['endCursor']
 for issue in rows:
  for key in ['labels','assignees']:
   assert issue[key]['totalCount']==len(issue[key]['nodes']), 'Truncated '+key+' connection; update collector before publishing.'
  # Complete truncated comment and timeline connections rather than silently missing evidence.
  for key,selection in [('comments','createdAt bodyText author{login __typename}'),('timelineItems','__typename ... on CrossReferencedEvent{createdAt source{__typename ... on PullRequest{id number title state mergedAt updatedAt repository{nameWithOwner}}}} ... on ReopenedEvent{createdAt}')]:
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
 for pos in range(0,len(ids),25):
  prs.extend(p for p in api(PR_QUERY,{'ids':ids[pos:pos+25]})['nodes'] if p)
  print(f'Linked PRs: {min(pos+25,len(ids))}/{len(ids)}',flush=True)
 for pr in prs:
  for key in ['closingIssuesReferences','reviewRequests']:
   assert pr[key]['totalCount']==len(pr[key]['nodes']), 'Truncated '+key+' connection; update collector before publishing.'
 final_total=api('query{repository(owner:"intel",name:"torch-xpu-ops"){issues(states:OPEN){totalCount}}}')['repository']['issues']['totalCount']
 assert len({i['number'] for i in rows})==len(rows)==total==final_total, 'Issue set changed during collection; rerun required.'
 verified_at=verify_open_set(rows)
 return {'open_set_verified_at':verified_at,'repository':'intel/torch-xpu-ops','started_at':started,'fetched_at':datetime.now(timezone.utc).isoformat(),'total_count':total,'issues':rows,'prs':prs}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--collect-only',action='store_true');p.add_argument('--cached',type=Path);p.add_argument('--output-dir',type=Path,default=ROOT);args=p.parse_args()
 if args.cached:raw=json.loads(args.cached.read_text())
 else:
  raw=collect();CACHE.mkdir(parents=True,exist_ok=True);path=CACHE/(raw['fetched_at'][:10]+'.json');path.write_text(json.dumps(raw,ensure_ascii=False));print('Saved snapshot:',path,flush=True)
 if not args.collect_only:
  from render_report import render
  args.output_dir.mkdir(parents=True,exist_ok=True)
  render(raw,args.output_dir)
