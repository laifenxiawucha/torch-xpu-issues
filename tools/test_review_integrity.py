import unittest
import json, tempfile, io, re
from pathlib import Path
from contextlib import redirect_stdout
from issue_facts import reported_versions
from evidence_context import execution_refs
from render_report import render, clean


class ReviewIntegrityTests(unittest.TestCase):
    def test_report_target_and_baseline_are_separate(self):
        v=reported_versions('## Environment\nTarget PyTorch: torch==2.14.0.dev20260805+xpu\nBaseline: torch==2.13.0+xpu')
        self.assertEqual(v['reported'],['2.14.0.dev20260805+xpu'])
        self.assertEqual(v['baseline'],['2.13.0+xpu'])

    def test_alternate_version_formats_and_na(self):
        for body in ['PyTorch: `2.14.0+xpu`','PyTorch version: 2.14.0+xpu','### Pytorch Version\n\n2.14.0+xpu']:
            self.assertEqual(reported_versions(body)['reported'],['2.14.0+xpu'])
        self.assertEqual(reported_versions('PyTorch version: N/A')['reported'],[])

    def test_baseline_is_never_promoted_to_report_version(self):
        self.assertEqual(reported_versions('## Baseline\ntorch==2.13.0')['reported'],[])

    def test_release_baseline_without_pip_version_is_preserved(self):
        v=reported_versions('Target PyTorch: torch==2.14.0.dev20260805+xpu\nBaseline: PyTorch release/2.13 XPU wheel')
        self.assertEqual(v['baseline'],['2.13'])
        self.assertEqual(v['reported'],['2.14.0.dev20260805+xpu'])

    def test_run_identity_survives_no_link_sanitization(self):
        original='https://github.com/intel/torch-xpu-ops/actions/runs/40512364980'
        self.assertEqual(execution_refs(clean(original)),execution_refs(original))
        self.assertNotIn('https://',clean(original))

    def test_reverse_closing_reference_is_not_lost_without_issue_event(self):
        i={'number':1,'title':'test example','body':'','bodyText':'','createdAt':'2026-10-01T00:00:00Z','updatedAt':'2026-10-29T00:00:00Z','author':{'login':'person'},'comments':{'nodes':[]},'timelineItems':{'nodes':[]},'labels':{'nodes':[]},'assignees':{'nodes':[]},'milestone':None}
        pr={'id':'P1','number':2,'title':'fix','state':'OPEN','mergedAt':None,'updatedAt':'2026-10-29T00:00:00Z','isDraft':False,'reviewDecision':None,'mergeable':'UNKNOWN','mergeStateStatus':'UNKNOWN','repository':{'nameWithOwner':'pytorch/pytorch'},'baseRefName':'main','headRefName':'fix','changedFiles':1,'commits':{'nodes':[]},'reviewRequests':{'nodes':[]},'closingIssuesReferences':{'nodes':[{'number':1,'repository':{'nameWithOwner':'intel/torch-xpu-ops'}}]}}
        raw={'fetched_at':'2026-10-29T01:00:00+00:00','started_at':'2026-10-29T00:00:00+00:00','total_count':1,'issues':[i],'prs':[pr]}
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            result=render(raw,Path(tmp))
        self.assertEqual(result['rows'][0]['repair_pr_status'],'明确关闭引用 PR 进行中')
        self.assertEqual(result['rows'][0]['explicit_fix_pr_count'],'1')


if __name__=='__main__':unittest.main()
