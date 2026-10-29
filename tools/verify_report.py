"""Validate report/source correspondence, lazy details, CSV, and the no-link contract."""
import argparse,json,re,csv
from pathlib import Path
from html.parser import HTMLParser
from render_report import clean

class Markup(HTMLParser):
 def __init__(self):super().__init__();self.ids=[]
 def handle_starttag(self,tag,attrs):
  attrs=dict(attrs)
  if 'id' in attrs:self.ids.append(attrs['id'])
  assert tag not in ['a','iframe','img','link'],'Report contains an external-capable element: '+tag
  assert 'src' not in attrs and 'href' not in attrs,'External resource attribute'

def verify(html,snapshot):
 text=html.read_text();source=json.loads(snapshot.read_text());data=json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>',text,re.S).group(1))
 details={number:json.loads(encoded) for number,encoded in re.findall(r'<script type="application/json" id="detail-(\d+)">(.*?)</script>',text,re.S)}
 originals={str(i['number']):i for i in source['issues']};assert len(data['rows'])==len(originals)==source['total_count'];assert len(details)==len(originals)
 assert 'delta' not in data and not re.search(r'https?://|www\.',text,re.I)
 assert set(originals)=={r['issue'] for r in data['rows']}
 for r in data['rows']:
  i=originals[r['issue']];detail=details[r['issue']]
  assert r['title']==clean(i['title']) and r['updated_at_utc']==i['updatedAt']
  assert len(detail['comments'])==i['comments']['totalCount']==int(r['comment_count'])
  assert set(r['labels_raw'].split('; '))=={x['name'] for x in i['labels']['nodes']} or not i['labels']['nodes'] and not r['labels_raw']
  assert detail['body_markdown']==clean(i.get('body',''))
  if not detail['body_markdown']:assert detail['body_text']==clean(i['bodyText'])
  for actual,expected in zip(detail['comments'],i['comments']['nodes']):assert actual['text']==clean(expected['bodyText'])
  for actual,expected in zip(detail['comments'],i['comments']['nodes']):assert actual['markdown']==clean(expected.get('body',''))
  linked=[dict(data['prs'][p['id']],explicit=p['explicit_fix_reference']) for p in r['linked_prs']]
  assert int(r['explicit_fix_pr_count'])+int(r['cross_reference_pr_count'])==len(linked)
  # Independent reverse check: every already-fetched PR closing this issue must
  # appear, even when GitHub's issue timeline has no cross-reference event.
  for pr in source['prs']:
   closes=any(x['number']==i['number'] and x['repository']['nameWithOwner']=='intel/torch-xpu-ops' for x in pr['closingIssuesReferences']['nodes'])
   if closes:assert any(p['id']==pr['id'] and p['explicit'] for p in linked),'Missing reverse closing reference: '+r['issue']
  assert (r['pr_ci_failed']=='1')==any(p['explicit'] and p['state']=='OPEN' and p['checks'] in ['FAILURE','ERROR'] for p in linked)
  assert r['primary_blocker'] not in ['公开记录未显示负责人','长期没有公开进展']
  if r['classification_review']=='未人工核查':assert r['latest_verified_version']=='未确认'
  closure=detail['closure_review']
  assert len(closure['cases'])==closure['total']==int(r['closure_case_total'])
  assert sum(closure['counts'].values())==closure['total']
  assert closure['disposed']==int(r['closure_case_disposed'])
  assert closure['status']==r['closure_status']
  assert (r['closure_candidate']=='1')==closure['candidate']==bool(closure['reasons'])
  if closure['status']=='优先审查关闭':
   assert closure['inventory_complete'] and closure['total']>0
   assert closure['disposed']==closure['total'] and not closure['blockers'] and not closure['counts']['skipped']
   assert not closure['configuration']['checks'],'Priority requires matching configuration evidence'
  for case in closure['cases']:
   if case['status']=='passed':
    name=case['name'].split(',')[-1].split('::')[-1]
    if '.test_' in name:name=name.split('.test_',1)[1];name='test_'+name
    assert name in case['evidence'][-1]['excerpt'],'Per-case excerpt must identify its case'
  for status,count in closure['counts'].items():assert int(r['closure_case_'+status])==count
 parser=Markup();parser.feed(text);assert len(parser.ids)==len(set(parser.ids)),'Duplicate HTML IDs'
 csvpath=html.parent/f"torch-xpu-ops-issue-inventory-{data['snapshot']}.csv"
 with csvpath.open(encoding='utf-8-sig',newline='') as f:
  reader=csv.DictReader(f);records=list(reader)
  assert len(records)==len(data['rows']);assert reader.fieldnames==data['fields']
  for row,expected in zip(records,data['rows']):assert all(row[k]==str(expected[k]) for k in data['fields'])
 assert data['collection']['comment_count']==sum(len(d['comments']) for d in details.values())
 print(f"PASS: {len(originals)} issues, {data['collection']['comment_count']} comments, {len(data['prs'])} distinct PRs, lazy detail blocks, source correspondence, CSV, unique IDs, no backlinks/external resources.")
 return data
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('html',type=Path);parser.add_argument('snapshot',type=Path);args=parser.parse_args();verify(args.html,args.snapshot)
