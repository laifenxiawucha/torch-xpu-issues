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
        self.assertEqual(result['status'], '优先审查关闭')
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


if __name__ == '__main__':
    unittest.main()
