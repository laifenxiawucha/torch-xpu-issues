import unittest
from body_tasks import task_marks
from team_review import people_for, review_summary
from closure_review import review_issue
from test_closure_review import issue, comment, pr
from render_report import clean


class BodyTaskTests(unittest.TestCase):
    def test_bullets_need_no_numbers_or_test_names(self):
        b=task_marks('- ~~Fix allocation~~\n- ~~Restore device selection~~')
        self.assertTrue(b['all_marked']);self.assertEqual(b['marked'],2)

    def test_numbered_list_and_unfinished_peer(self):
        b=task_marks('1. ~~Fix allocation~~\n2. Restore device selection')
        self.assertEqual(b['remaining'],1);self.assertFalse(b['all_marked'])

    def test_checked_parent_does_not_finish_unchecked_child(self):
        b=task_marks('- [x] Fix allocation\n  - [ ] Confirm Windows behavior')
        self.assertEqual(b['remaining'],1);self.assertFalse(b['all_marked'])

    def test_checkbox_explanations_are_not_separate_open_tasks(self):
        b=task_marks('- [x] Migrate math\n  - Functions: abs, log\n  - Keep complex calls\n- [ ] Verify Windows\n  - Use the standard build')
        self.assertEqual((b['total'],b['marked'],b['remaining']),(2,1,1))
        self.assertEqual(len(b['items'][0]['context']),2)

    def test_address_removal_preserves_strike_delimiters(self):
        text='- ~~Fix https://github.com/intel/torch-xpu-ops/pull/2~~'
        self.assertTrue(task_marks(clean(text))['all_marked'])
        self.assertEqual(clean(clean(text)),clean(text))

    def test_inline_code_and_test_result_paragraphs_are_not_tasks(self):
        for text in ['`~~some code~~`','~~2026-10-28\ntest_ops.py::TestOps::test_one PASSED~~']:
            self.assertFalse(task_marks(text)['has_signal'])

    def test_unmarked_table_peer_is_preserved(self):
        b=task_marks('| Item | Result |\n| --- | --- |\n| ~~Allocation~~ | ~~done~~ |\n| Device selection | pending |')
        self.assertEqual((b['marked'],b['remaining']),(1,1));self.assertFalse(b['all_marked'])

    def test_unmarked_old_report_bullets_are_not_current_pending_tasks(self):
        self.assertEqual(task_marks('## Describe the bug\n- Fails on Linux\n- Fails on Windows')['total'],0)

    def test_module_csv_is_handled_by_case_inventory_instead(self):
        self.assertEqual(task_marks('Cases:\n~~upstream_default,,test.custom_operator.test_custom_ops~~\nupstream_default,,test.dynamo.test_nb_int')['total'],0)

    def test_all_checkboxes_complete_is_a_signal(self):
        b=task_marks('## Remaining work\n- [x] Fix allocation\n- [X] Check devices')
        self.assertTrue(b['all_marked'])

    def test_partial_crossing_does_not_complete_item(self):
        b=task_marks('- Replace ~~old allocation~~ with correct implementation')
        self.assertEqual(b['uncertain'],1);self.assertEqual(b['marked'],0)

    def test_paragraphs_and_headings_are_visible_with_unknown_scope(self):
        for text in ['~~Allocation bug is resolved.~~','### ~~Allocation bug~~','<del>Allocation bug</del>']:
            b=task_marks(text);self.assertTrue(b['has_signal']);self.assertFalse(b['scope_complete'])

    def test_code_logs_and_quotations_never_create_signals(self):
        for text in ['```python\n- ~~Fix allocation~~\n```','~~~text\n~~Fix allocation~~\n~~~','> - ~~Fix allocation~~','    ~~Fix allocation~~','    - ~~Fix allocation~~','<pre>\n~~Fix allocation~~\n</pre>','~~~^~~~~~~~~']:
            self.assertFalse(task_marks(text)['has_signal'],text)

    def test_agent_process_is_not_the_issue_task(self):
        b=task_marks('- [x] Issue formatted (Discovery Agent)\n- [x] Root cause identified (Triage Agent)\n- [x] Fix implemented (Fix Agent)\n- [x] PR proposed\n- [x] Human review')
        self.assertEqual(b['total'],0)

    def test_environment_crossings_are_not_task_completion(self):
        self.assertFalse(task_marks('## Versions\n- ~~Old driver~~')['has_signal'])

    def test_test_inventory_is_not_duplicated_as_unfinished_body_tasks(self):
        self.assertEqual(task_marks('## Cases\n- test_ops.py::TestOps::test_one')['total'],0)

    def test_table_crossing_retains_unknown_coverage(self):
        b=task_marks('| Item | Result |\n| --- | --- |\n| ~~Allocation~~ | ~~done~~ |')
        self.assertTrue(b['has_signal']);self.assertFalse(b['scope_complete'])


class TeamReviewTests(unittest.TestCase):
    def result(self,body,prs=(),comments=(),title='Allocation failure'):
        i=issue(body,comments,title);return review_summary(i,review_issue(i,list(prs)),list(prs))

    def test_plain_completed_tasks_enter_human_queue_without_test_inventory(self):
        r=self.result('- ~~Allocation~~\n- ~~Device selection~~')
        self.assertTrue(r['candidate']);self.assertEqual(r['rank'],1)
        self.assertIn('正文已识别事项均标记处理',r['reasons'])

    def test_partial_crossing_is_a_weaker_review_signal(self):
        r=self.result('~~Allocation issue~~')
        self.assertTrue(r['candidate']);self.assertIn('正文存在处理标记，范围待确认',r['reasons'])

    def test_merged_pr_can_be_prioritized_without_claiming_verified_fix(self):
        r=self.result('Allocation fails.',[pr()]);self.assertEqual(r['rank'],0)
        self.assertTrue(r['checks'])

    def test_unfinished_task_keeps_merged_record_in_remaining_group(self):
        r=self.result('- [x] Allocation\n- [ ] Windows verification',[pr()])
        self.assertEqual(r['rank'],2);self.assertTrue(any('正文' in x for x in r['blockers']))

    def test_do_not_close_is_never_prioritized(self):
        r=self.result('- ~~Allocation~~',[pr()],title='[DONT CLOSE] Known issues')
        self.assertEqual(r['rank'],2)

    def test_human_closing_proposal_is_discoverable(self):
        r=self.result('Allocation fails.',comments=[comment('This issue is now resolved.')])
        self.assertTrue(r['candidate']);self.assertEqual(r['rank'],0)

    def test_auto_analysis_does_not_become_human_closing_proposal(self):
        r=self.result('Allocation fails.',comments=[comment('Generated by label job. This issue is now resolved.')])
        self.assertFalse(r['candidate'])

    def test_conditional_or_questioned_closure_is_not_confirmation(self):
        for text in ['This issue can be closed after verification.', 'This issue can be closed if Windows passes.', 'This issue is now resolved?', '> This issue is now resolved.']:
            self.assertFalse(self.result('Allocation fails.',comments=[comment(text)])['candidate'],text)

    def test_person_roles_are_explicit_and_do_not_include_commenters(self):
        i=issue('x');i['assignees']={'nodes':[{'login':'owner'}]};i['author']={'login':'reporter'}
        p=pr();p.update(author='coder',review_users=['reviewer'],review_teams=['owner'])
        roles=people_for(i,[p])
        self.assertEqual({(x['login'],x['role']) for x in roles},{('owner','assignee'),('reporter','reporter'),('coder','pr_author'),('reviewer','reviewer')})
        self.assertEqual(next(x['reference'] for x in roles if x['role']=='pr_author'),'intel/torch-xpu-ops#2')

    def test_pr_repository_identity_is_preserved(self):
        p=pr();p.update(repository='pytorch/pytorch',author='coder',review_users=[])
        self.assertEqual(people_for(issue('x'),[p])[-1]['reference'],'pytorch/pytorch#2')


if __name__=='__main__':unittest.main()
