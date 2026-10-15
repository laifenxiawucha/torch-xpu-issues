import unittest,json,tempfile
from pathlib import Path
from issue_facts import classify_impact,record_nature,discovery_channels,reproduction_clues,repair_summary,blocker_info,verification_candidates
from render_report import load_overrides,classify
class IssueFactsTests(unittest.TestCase):
 def test_unit_failure_is_not_infrastructure_failure(self):
  self.assertEqual(classify_impact('test_topk.py returns wrong indices','test: ut'),['正确性'])
  self.assertEqual(discovery_channels('test_topk.py returns wrong indices','test: ut','test_topk.py'),['UT'])
 def test_generic_ci_failure_keeps_impact_unknown(self):
  self.assertEqual(classify_impact('test_foo fails on CI','skipped; pytorch-ci-failure'),['影响未知'])
 def test_unexpected_success_is_an_expectation_issue(self):
  self.assertEqual(classify_impact('test_foo Unexpected success','test: ut'),['测试预期需核对'])
  self.assertNotEqual(classify('test_ops Unexpected success','','test: ut')[0],'测试/CI 基础设施')
 def test_missing_test_library_is_infrastructure(self):
  self.assertIn('测试/CI 基础设施故障',classify_impact('[CI] INSTALL_TEST disabled, missing libtorchbind_test.so',''))
 def test_change_does_not_mean_hang(self):
  self.assertNotIn('崩溃/稳定性',classify_impact('change profiler configuration',''))
  self.assertIn('崩溃/稳定性',classify_impact('XPU hangs when compiling',''))
 def test_performance_regression_does_not_imply_correctness(self):
  self.assertEqual(classify_impact('e2e performance regression','regression'),['性能'])
 def test_body_symptoms_add_to_title_symptoms(self):
  self.assertEqual(classify_impact('accuracy failure','','Floating point exception'),['正确性','崩溃/稳定性'])
 def test_environment_appendix_does_not_classify_impact(self):
  self.assertEqual(classify_impact('test_foo fails','','Versions\nModuleNotFoundError: pytest'),['影响未知'])
 def test_incorrect_error_is_a_defect_not_feature(self):
  title='conv reports not implemented instead of a device-mismatch error'
  impact=classify_impact(title,'');self.assertEqual(record_nature(title,'','','算子/kernel',impact),'缺陷/失败线索')
 def test_support_gap_is_not_automatically_a_feature_request(self):
  title='FA4 backend is not supported on XPU';impact=classify_impact(title,'')
  self.assertEqual(record_nature(title,'','','算子/kernel',impact),'支持范围缺口（性质待核实）')
 def test_explicit_feature_request_is_a_feature(self):
  self.assertEqual(record_nature('Add support for new API','','','对外 API/语义',['算子/API 不支持']),'功能/改进请求')
 def test_absent_owner_is_not_a_blocker(self):
  self.assertEqual(blocker_info('skipped','')[0],'未记录明确阻塞')
  self.assertEqual(blocker_info('dependency component: oneAPI','')[0],'存在依赖线索')
 def test_full_markdown_code_preserves_indentation(self):
  code='import torch\nif True:\n    x = torch.ones(3)\n    print(x)'
  clues=reproduction_clues(code,'test_foo.py',['2.15'],'```python\n'+code+'\n```')
  self.assertEqual(clues['repro_code_blocks'],[code]);self.assertEqual(clues['repro_code_present'],'1')
 def test_traceback_is_not_a_repro_code_block(self):
  self.assertEqual(reproduction_clues('Traceback: File torch/test_foo.py RuntimeError','','')['repro_code_present'],'0')
 def test_ordinary_pr_failure_is_not_a_closing_reference_failure(self):
  linked=[{'explicit_fix_reference':False,'state':'OPEN','checks':'FAILURE'},{'explicit_fix_reference':True,'state':'OPEN','checks':'SUCCESS'}]
  self.assertFalse(repair_summary(linked)['ci_failed']);self.assertEqual(repair_summary(linked[:1])['status'],'仅见普通交叉引用')
 def test_merged_closing_reference_has_explicit_status(self):
  self.assertEqual(repair_summary([{'explicit_fix_reference':True,'state':'MERGED','checks':'SUCCESS'}])['status'],'明确关闭引用 PR 已合并')
 def test_verification_plan_is_not_completed_validation(self):
  comments=[{'is_bot':False,'text':'Please verify on next weekly build','created_at':'2026-10-15','author':'person'},{'is_bot':False,'text':'Verified with pt28, this issue still exists.','created_at':'2026-10-14','author':'person'}]
  result=verification_candidates(comments);self.assertEqual(len(result),1);self.assertIn('still exists',result[0]['excerpt'])
 def test_override_requires_attribution_and_valid_fields(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'overrides.json';path.write_text(json.dumps({'schema_version':1,'issues':{'1':{'set':{'surface_scope':'算子/kernel'}}}}))
   with self.assertRaises(AssertionError):load_overrides(path,{'1'})
   item={'reason':'reviewed symptom','reviewer':'maintainer','reviewed_at':'2026-10-15','evidence':'exact description','set':{'impact_types':['正确性']}}
   path.write_text(json.dumps({'schema_version':1,'issues':{'1':item}}));self.assertEqual(load_overrides(path,{'1'})['1']['set']['impact_types'],['正确性'])
if __name__=='__main__':unittest.main()
