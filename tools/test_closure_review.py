import unittest
import io, json, re, tempfile
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
from closure_review import review_issue, inventory, references
from render_report import classify, clean, render


def issue(body, comments=(), title='[Bug Skip]: UT failures'):
    return {'number': 1, 'title': title, 'body': body, 'bodyText': body,
            'updatedAt': '2026-10-29T05:00:00Z', 'author': {'login': 'reporter'},
            'comments': {'nodes': list(comments)}}


def comment(text, day='28', bot=False):
    return {'createdAt': f'2026-10-{day}T00:00:00Z', 'bodyText': text,
            'author': {'login': 'github-actions' if bot else 'maintainer', '__typename': 'Bot' if bot else 'User'}}


def pr(state='MERGED', explicit=True):
    return {'repository': 'intel/torch-xpu-ops', 'number': 2, 'title': 'Fix tests',
            'state': state, 'explicit_fix_reference': explicit,
            'base': 'main', 'merged_at': '2026-10-20T00:00:00Z',
            'updated_at': '2026-10-20T00:00:00Z'}


CASES = 'Cases:\nop_ut,test.xpu.test_ops_xpu.TestOps,test_one\nop_ut,test.xpu.test_ops_xpu.TestOps,test_two\n'


class ClosureReviewTests(unittest.TestCase):
    def test_inventory_deduplicates_but_does_not_read_traceback(self):
        cases, complete, _ = inventory(CASES + 'op_ut,test.xpu.test_ops_xpu.TestOps,test_one\n## ErrorLog\ntest_other.py::TestOps::test_three')
        self.assertEqual(len(cases), 2)
        self.assertTrue(complete)

    def test_crossings_are_not_passes(self):
        body = 'Cases:\n~~op_ut,test.xpu.test_ops_xpu.TestOps,test_one~~'
        result = review_issue(issue(body), [])
        self.assertTrue(result['candidate'])
        self.assertEqual(result['counts']['crossed'], 1)
        self.assertEqual(result['disposed'], 0)
        self.assertEqual(result['status'], '需要补充确认')

    def test_nightly_records_can_complete_a_list(self):
        result = review_issue(issue(CASES, [comment('✅ test_one test_two Passed in nightly testing on BMG', bot=True)]), [])
        self.assertEqual(result['status'], '需要补充确认')
        self.assertEqual(result['counts']['passed'], 2)

    def test_latest_failed_case_overrides_old_pass(self):
        result = review_issue(issue(CASES, [comment('test_one test_two Passed in nightly testing', '20', True), comment('test_two still fails on release/2.15')]), [pr()])
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(result['status'], '存在未完成事项')

    def test_new_explicit_failure_does_not_require_the_word_still(self):
        result=review_issue(issue(CASES,[comment('test_one test_two Passed in nightly testing','20',True),comment('test_two FAILED on BMG')]),[pr()])
        self.assertEqual(result['counts']['failed'],1)
        self.assertEqual(result['status'],'存在未完成事项')

    def test_release_feedback_blocks_merged_pr_candidate(self):
        result = review_issue(issue(CASES, [comment('This issue still persists in PyTorch 2.15 pre-release')]), [pr()])
        self.assertEqual(result['status'], '存在未完成事项')

    def test_fix_command_and_agent_narrative_are_not_completion(self):
        c = comment('@torchxpubot fix\nWill fix test_one and test_two')
        agent = comment('test_one test_two fixed and passed', bot=True)
        agent['author']['login'] = 'torchxpubot'
        result = review_issue(issue(CASES, [c, agent]), [])
        self.assertFalse(result['candidate'])
        self.assertEqual(result['disposed'], 0)

    def test_partial_list_is_never_reported_complete(self):
        result = review_issue(issue('Representative cases:\ntest_ops.py::TestOps::test_one', [comment('test_one Passed in nightly testing', bot=True)]), [])
        self.assertFalse(result['inventory_complete'])
        self.assertFalse(result['candidate'])

    def test_case_fix_requires_merged_associated_pr(self):
        for state, expected in [('OPEN', 0), ('MERGED', 1)]:
            result = review_issue(issue(CASES, [comment('test_one fixed in #2')]), [pr(state, False)])
            self.assertEqual(result['counts']['fixed'], expected)

    def test_ordinary_merged_reference_does_not_imply_closure(self):
        self.assertFalse(review_issue(issue('No case list.'), [pr(explicit=False)])['candidate'])

    def test_skip_followup_issue_keeps_its_work(self):
        result = review_issue(issue(CASES, [comment('test_one test_two are already skipped')], '[skip-added] restore unsupported backend'), [pr()])
        self.assertEqual(result['counts']['skipped'], 2)
        self.assertEqual(result['status'], '存在未完成事项')

    def test_duplicate_requires_existing_open_issue_target(self):
        source = issue('This is a duplicate of #2')
        self.assertFalse(review_issue(source, [])['candidate'])
        target = {'intel/torch-xpu-ops#2': {'type': 'Issue', 'state': 'OPEN'}}
        self.assertIn('存在重复主记录', review_issue(source, [], target)['reasons'])

    def test_ambiguous_bare_names_do_not_complete_multiple_classes(self):
        source = issue('Cases:\ntest_ops.py::TestA::test_one\ntest_ops.py::TestB::test_one', [comment('test_one Passed in nightly testing', bot=True)])
        self.assertEqual(review_issue(source, [])['disposed'], 0)

    def test_reference_identity_and_sanitized_rebuild(self):
        text = 'Fixed in https://github.com/pytorch/pytorch/pull/200000; follow-up #2'
        self.assertEqual(references(text), ['pytorch/pytorch#200000', 'intel/torch-xpu-ops#2'])
        self.assertEqual(references(clean(text)), references(text))
        self.assertNotIn('https://', clean(text))

    def test_integer_dtypes_do_not_imply_quantization(self):
        for title in ['dot_xpu_mkl not implemented for int8/uint8', 'test_decomp test_quick_mv_xpu_uint8', 'OSError: SYCL runtime is not detected']:
            self.assertNotEqual(classify(title, 'torch.empty(3, dtype=torch.int8)', '')[0], '量化/torchao')

    def test_neighboring_test_paragraph_does_not_inherit_deleted_status(self):
        comments = [comment('**1. test_one** — Obsolete, deleted upstream.\n\n**2. test_two** — Fixed upstream, PR link to be added.')]
        result = review_issue(issue(CASES, comments), [])
        self.assertEqual(result['cases'][0]['status'], 'obsolete')
        self.assertEqual(result['cases'][1]['status'], 'unknown')

    def test_title_declared_count_prevents_partial_completion(self):
        result = review_issue(issue(CASES, [comment('test_one test_two Passed in nightly testing', bot=True)], 'UT failures (3 cases)'), [])
        self.assertEqual(result['declared_total'], 3)
        self.assertFalse(result['inventory_complete'])
        self.assertFalse(result['candidate'])

    def test_matching_title_count_does_not_override_explicit_partial_list(self):
        source=issue('Cases:\nRepresentative cases only:\ntest_ops.py::TestOps::test_one',[comment('test_one Passed in nightly testing',bot=True)],'1 cases')
        self.assertFalse(review_issue(source,[])['inventory_complete'])

    def test_unexpected_success_is_not_resolved_by_ordinary_pass(self):
        source=issue(CASES,[comment('test_one test_two Passed in nightly testing',bot=True)],'Unexpected success in test_ops')
        self.assertEqual(review_issue(source,[])['status'],'需要补充确认')

    def test_flaky_case_needs_repeatability_review(self):
        source=issue(CASES,[comment('test_one test_two Passed in nightly testing',bot=True)])
        source['labels']={'nodes':[{'name':'random'}]}
        self.assertEqual(review_issue(source,[])['status'],'需要补充确认')

    def test_real_hyphenated_repo_and_multiple_suite_names(self):
        body = 'Cases:\nop_ut,third_party.torch-xpu-ops.test.xpu.test_ops_xpu.TestOps,test_one\nupstream_inductor,test.inductor.test_ops.TestOps,test_two'
        cases, complete, _ = inventory(body)
        self.assertTrue(complete)
        self.assertEqual(len(cases), 2)

    def test_baseline_passes_in_report_body_are_not_current_resolution(self):
        source=issue('Cases:\ntest_ops.py::TestOps::test_one fail on LNL. Tests passed on BMG PT2.13 and LNL PT2.12')
        self.assertEqual(review_issue(source,[])['disposed'],0)

    def test_conditional_close_instruction_is_not_a_passing_result(self):
        source=issue(CASES,[comment('Close only once test_one passes on XPU and the xfail is removed.')])
        self.assertEqual(review_issue(source,[])['disposed'],0)

    def test_order_dependent_failure_is_not_fixed_by_isolated_pass(self):
        source=issue('test_ops.py::TestOps::test_one passes in isolation but hangs after other tests.')
        self.assertFalse(review_issue(source,[])['candidate'])

    def test_other_tests_passing_does_not_resolve_the_reported_cases(self):
        source=issue(CASES+'\nThe test_one and test_two hang; the rest of the file passed.')
        self.assertEqual(review_issue(source,[])['disposed'],0)

    def test_reviewed_impact_recomputes_engineering_action(self):
        i=issue('Cases:\ntest_ops.py::TestOps::test_one',title='test_one Unexpected success')
        i.update(createdAt='2026-10-01T00:00:00Z',labels={'nodes':[]},assignees={'nodes':[]},milestone=None,timelineItems={'nodes':[]})
        review={'reason':'manual review','reviewer':'maintainer','reviewed_at':'2026-10-29','evidence':'specific correctness evidence','set':{'impact_types':['正确性'],'surface_scope':'算子/kernel'}}
        raw={'fetched_at':'2026-10-29T05:00:00+00:00','started_at':'2026-10-29T04:55:00+00:00','total_count':1,'issues':[i],'prs':[]}
        with tempfile.TemporaryDirectory() as tmp, patch('render_report.load_overrides',return_value={'1':review}), redirect_stdout(io.StringIO()):
            render(raw,Path(tmp))
            html=Path(tmp,'index.html').read_text()
            data=json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>',html,re.S).group(1))
        row=data['rows'][0]
        self.assertEqual(row['impact_types'],'正确性')
        self.assertNotIn('xfail',row['engineering_action'])
        self.assertEqual(row['classification_confidence'],'人工核查')
        self.assertIn('人工核查',row['impact_evidence'])

    def test_logs_before_cases_are_not_inventory(self):
        body='test_ops.py::TestOps::test_previous PASSED\nCases:\ntest_ops.py::TestOps::test_one\ntest_ops.py::TestOps::test_two'
        self.assertEqual(len(inventory(body)[0]),2)

    def test_singular_case_section_is_a_declared_list(self):
        cases,complete,_=inventory('Case:\n`op_ut,test.ops.TestOps,test_one`\nRoot cause\nDetails')
        self.assertEqual(len(cases),1)
        self.assertTrue(complete)

    def test_explicit_single_case_in_title_and_body_is_recognized(self):
        source=issue('test_ops.py::TestOps::test_one fails before the assertion.','',title='test_one fails on XPU')
        source['comments']={'nodes':[]}
        result=review_issue(source,[])
        self.assertEqual(result['total'],1)
        self.assertTrue(result['inventory_complete'])

    def test_relocated_class_alias_requires_explicit_source_statement(self):
        body='Cases:\ntest_indexing_xpu.py::TestIndexingXPU::test_one'
        c=comment('');c['body']='The wrapper files were removed; cases now run from upstream.\n| Test | Result |\n| --- | --- |\n| test_indexing.py::TestIndexingDeviceXPU::test_one | passed |'
        result=review_issue(issue(body,[c],'1 UTs fail'),[])
        self.assertEqual(result['total'],1)
        self.assertEqual(result['counts']['passed'],1)
        self.assertEqual(len(result['cases'][0]['aliases']),1)

    def test_configuration_is_not_claimed_matched_without_a_pass(self):
        result=review_issue(issue(CASES),[])
        self.assertEqual(result['configuration']['status'],'无通过记录，配置待核对')

    def test_dotted_node_ids_and_modules_are_distinguished(self):
        body='Affected Test Cases\n```\ntest_ops.py — 12 failures\n```\nops.test_ops_xpu::TestOps.test_one\nops.test_ops_xpu::TestOps.test_two'
        cases, complete, _=inventory(body)
        self.assertTrue(complete)
        self.assertEqual(len(cases),2)
        self.assertTrue(all(c['key'][2].startswith('test_') for c in cases))

    def test_human_comment_result_table_supplements_missing_inventory(self):
        c=comment('')
        c['body']='Re-checked all 2 cases.\n\n| Test | Result on main |\n| --- | --- |\n| test_ops.py::TestOps::test_one | passed |\n| test_ops.py::TestOps::test_two | skipped by design |'
        result=review_issue(issue('Full per-test list not enumerated.',[c],'2 UTs fail'),[])
        self.assertEqual(result['total'],2)
        self.assertTrue(result['inventory_complete'])
        self.assertEqual(result['counts']['passed'],1)
        self.assertEqual(result['counts']['skipped'],1)

    def test_related_other_case_does_not_become_transfer_candidate(self):
        target={'intel/torch-xpu-ops#2':{'type':'Issue','state':'OPEN'}}
        for text in ['The other FP8 case is already tracked in #2.','Follow-up to #2.','Split from #2.','Likely duplicate of #2.']:
            result=review_issue(issue(text),[],target)
            self.assertFalse(result['candidate'],text)
            self.assertTrue(result['related_records'],text)

    def test_explicit_remaining_work_transfer_is_preserved(self):
        target={'intel/torch-xpu-ops#2':{'type':'Issue','state':'OPEN'}}
        result=review_issue(issue('All remaining work is now tracked in #2.'),[],target)
        self.assertIn('剩余任务有明确转移记录',result['reasons'])

    def test_case_excerpt_contains_case_even_in_large_batch(self):
        names=['test_case_'+str(n) for n in range(100)]
        source=issue('Cases:\n'+'\n'.join('op_ut,test.ops.TestOps,'+n for n in names),[comment(' '.join(names)+' Passed in nightly testing on BMG',bot=True)])
        result=review_issue(source,[])
        self.assertEqual(result['counts']['passed'],100)
        self.assertTrue(all(c['key'][2] in c['evidence'][-1]['excerpt'] if 'key' in c else c['name'].split(',')[-1] in c['evidence'][-1]['excerpt'] for c in result['cases']))

    def test_workflow_and_branch_mismatch_prevents_priority(self):
        source=issue(CASES+'\nFound in https://github.com/intel/torch-xpu-ops/actions/runs/10',[comment('test_one test_two Passed in [nightly testing](https://github.com/intel/torch-xpu-ops/actions/runs/20) on BMG',bot=True)],'[BMG][dpclang] failures')
        runs={'intel/torch-xpu-ops@run:10':{'available':True,'path':'.github/workflows/pull_dpclang.yml','branch':'feature'},'intel/torch-xpu-ops@run:20':{'available':True,'path':'.github/workflows/nightly.yml','branch':'main'}}
        result=review_issue(source,[],runs=runs)
        self.assertEqual(result['status'],'需要补充确认')
        self.assertIn('通过记录与原报告 workflow 不同',result['configuration']['checks'])
        self.assertIn('通过记录与原报告分支不同',result['configuration']['checks'])

    def test_matching_workflow_branch_and_hardware_can_be_prioritized(self):
        source=issue(CASES+'\nFound in https://github.com/intel/torch-xpu-ops/actions/runs/10',[comment('test_one test_two Passed in [nightly testing](https://github.com/intel/torch-xpu-ops/actions/runs/20) on BMG',bot=True)],'[BMG] failures')
        runs={f'intel/torch-xpu-ops@run:{n}':{'available':True,'path':'.github/workflows/nightly.yml','branch':'main'} for n in [10,20]}
        self.assertEqual(review_issue(source,[],runs=runs)['status'],'优先审查关闭')


if __name__ == '__main__':
    unittest.main()
