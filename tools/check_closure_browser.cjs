/* Additional regressions for the maintainer closure-review workflow. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const url=process.argv[2]||'file://'+path.resolve(__dirname,'../index.html');
 const options={headless:true,args:['--no-sandbox']};if(/^https?:/.test(url)&&(process.env.HTTPS_PROXY||process.env.https_proxy))options.proxy={server:process.env.HTTPS_PROXY||process.env.https_proxy};
 const browser=await chromium.launch(options),context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));await page.goto(url,{waitUntil:'load'});
 const rows=await page.evaluate(()=>all),candidates=rows.filter(r=>r.closure_candidate==='1');assert(candidates.length>0);
 await page.locator('#closurePanel [data-view="closure"]').click();assert.equal(await page.locator('#rows tr').count(),Math.min(10,candidates.length));assert.match(await page.locator('#resultCount').innerText(),new RegExp('^'+candidates.length+' 条结果'));
 assert.equal(await page.locator('#worklistHead th').nth(2).innerText(),'用例处理情况');assert.equal(await page.locator('#sort').inputValue(),'closure-priority');
 const ranked=await page.evaluate(()=>compute().map(r=>r.closure_status));assert.deepEqual(ranked,[...ranked].sort((a,b)=>({'优先审查关闭':0,'需要补充确认':1,'存在未完成事项':2}[a]-{'优先审查关闭':0,'需要补充确认':1,'存在未完成事项':2}[b])));
 const shared=page.url(),other=await context.newPage();await other.goto(shared,{waitUntil:'load'});assert.equal(await other.evaluate(()=>state.view),'closure');assert.match(await other.locator('#resultCount').innerText(),new RegExp('^'+candidates.length+' 条结果'));await other.close();
 for(const status of ['优先审查关闭','需要补充确认','存在未完成事项']){await page.locator(`[data-closure-status="${status}"]`).click();assert.equal(await page.evaluate(()=>resultRows.length),candidates.filter(r=>r.closure_status===status).length);}
 await page.locator('.view-switch [data-view="developer"]').click();
 // Debounced input must not survive an explicit undo.
 await page.evaluate(()=>{singleFilter('surface_scope','算子/kernel');$('search').value='unlikely_query_199999';$('search').dispatchEvent(new Event('input',{bubbles:true}));$('undo').click();});await page.waitForTimeout(250);assert.equal(await page.evaluate(()=>state.query),'');assert.equal(await page.evaluate(()=>resultRows.length),rows.length);
 // Clicking one of two selected categories must narrow to that category.
 await page.evaluate(()=>{clearState();state.filters.surface_scope=['算子/kernel','编译器/图执行'];render();singleFilter('surface_scope','算子/kernel');});assert.deepEqual(await page.evaluate(()=>state.filters.surface_scope),['算子/kernel']);
 // Invalid shared values stay visible and cannot silently expand results.
 await page.goto(url.split('#')[0]+'#v=3&f.assignees=former_owner_293049',{waitUntil:'load'});assert.equal(await page.evaluate(()=>resultRows.length),0);assert(await page.locator('#restoreNotice').isVisible());assert.match(await page.locator('#restoreNotice').innerText(),/former_owner_293049/);
 await page.locator('#reset').click();
 // Denied clipboard opens a separate modal without destroying the issue body.
 await page.evaluate(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>Promise.reject(new Error('denied'))}});showIssue(all[0].issue);});const title=await page.locator('#dialogTitle').innerText();await page.locator('#dialogContent [data-copy-issue]').click();assert(await page.locator('#copyDialog').isVisible());assert.equal(await page.locator('#dialogTitle').innerText(),title);assert.equal(await page.locator('#commentDetails').count(),1);await page.locator('#closeCopy').click();assert(await page.locator('#issueDialog').isVisible());await page.keyboard.press('Escape');
 // Case evidence is lazy and every row is reachable through batch expansion.
 const withCases=await page.evaluate(()=>all.find(r=>Number(r.closure_case_total)>60)?.issue);assert(withCases);await page.evaluate(id=>showIssue(id),withCases);assert.equal(await page.locator('.case-record').count(),0);assert.equal(await page.locator('#closureEvidenceRows').innerText(),'');await page.locator('#caseDetails').evaluate(e=>e.open=true);await page.waitForFunction(()=>document.querySelectorAll('.case-record').length>0);assert.equal(await page.locator('.case-record').count(),30);await page.locator('#moreCases').click();assert.equal(await page.locator('.case-record').count(),60);await page.keyboard.press('Escape');
 // Known scope mistakes must stay fixed in fresh data too.
 for(const id of ['4932','5049','2463']){const r=rows.find(x=>x.issue===id);if(r)assert.notEqual(r.surface_scope,'量化/torchao',id);}
 for(const [id,total] of [['4835',2],['4873',140],['4877',32]]){const r=rows.find(x=>x.issue===id);if(r)assert.equal(Number(r.closure_case_total),total,'review inventory regression '+id);}
 const otherCase=rows.find(r=>r.issue==='5837');if(otherCase)assert(!otherCase.closure_reasons.includes('后续跟踪'),'related sibling is not a transfer');
 const fp=rows.find(r=>r.issue==='4981');if(fp)assert.notEqual(fp.closure_status,'优先审查关闭','different dpclang/nightly configuration');
 const targetVersion=rows.find(r=>r.issue==='4905');if(targetVersion){assert.match(targetVersion.reported_pytorch_version,/2\.14/);assert.match(targetVersion.baseline_pytorch_version,/2\.13/);}
 const reverse=rows.find(r=>r.issue==='5044');if(reverse)assert(Number(reverse.explicit_fix_pr_count)>0,'reverse PR reference retained');
 await page.evaluate(()=>showIssue('4436'));await page.locator('#caseDetails').evaluate(e=>e.open=true);await page.waitForFunction(()=>document.querySelectorAll('.case-record').length>0);const contextual=await page.evaluate(()=>getDetail('4436').closure_review.cases.filter(c=>c.status==='passed').every(c=>c.evidence.at(-1).excerpt.includes(c.name.split(',').at(-1))));assert(contextual,'each displayed evidence identifies the case');assert(await page.locator('.run-record').count()>0,'plain text workflow provenance visible');await page.keyboard.press('Escape');
 const release=rows.find(r=>r.issue==='4959');if(release)assert.equal(release.closure_status,'存在未完成事项');
 const skip=rows.find(r=>r.issue==='5329');if(skip)assert.notEqual(skip.closure_status,'优先审查关闭');
 for(const r of candidates){if(r.closure_status==='优先审查关闭'){assert.equal(r.closure_inventory_complete,'1');assert.equal(r.closure_case_disposed,r.closure_case_total);assert.equal(r.closure_blockers,'');assert.equal(r.closure_configuration_status,'已匹配公开配置字段');}}
 const mixed=await page.evaluate(()=>all.find(r=>r.pr_ci_failed==='1'&&linkedFor(r).filter(p=>p.explicit_fix_reference&&p.state==='OPEN').length>1)?.issue);if(mixed){const text=await page.evaluate(id=>repairCell(rowById.get(id)),mixed);assert.match(text,/CI 失败/);}
 // Preferences are persisted, explicit shared values retain precedence.
 await page.locator('#pageSize').selectOption('25');await page.locator('#overview').evaluate(e=>e.open=false);await page.waitForTimeout(80);await page.reload({waitUntil:'load'});assert.equal(await page.locator('#pageSize').inputValue(),'25');assert.equal(await page.locator('#overview').getAttribute('open'),null);
 const base=url.split('#')[0];await page.goto(base+'#v=2&f.surface_scope='+encodeURIComponent('算子/kernel'),{waitUntil:'load'});assert.equal(await page.locator('#pageSize').inputValue(),'10','legacy shares have deterministic page size');
 const longQuery='a'.repeat(1300);await page.goto(base+'#v=3&q='+longQuery+'&size=10',{waitUntil:'load'});assert.equal(await page.locator('#search').inputValue(),longQuery,'share query is lossless');await page.locator('#reset').click();
 if(process.env.AXE_MODULE){await page.addScriptTag({path:process.env.AXE_MODULE});for(const scheme of ['light','dark']){await page.emulateMedia({colorScheme:scheme});await page.locator('#closurePanel [data-view="closure"]').click();const problems=await page.evaluate(async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})));assert.deepEqual(problems,[],scheme+' closure accessibility');}}
 assert.deepEqual(errors,[]);console.log('PASS: closure counts/status/reasons, ranking/sharing, lazy per-case evidence, regression examples, undo race, multi-select chart, stale shares, clipboard denial, PR aggregation, preferences, desktop accessibility.');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
