# Issue 开发工作台生成器

纯 Python 生成内嵌数据、样式和脚本的独立 HTML。无需前端构建或页面外部请求。桌面端工作台从整体分布、问题矩阵、开发队列进入清单，支持搜索、多选、撤销、浏览器历史和分享筛选状态。

## 重建已发布快照

在仓库根目录运行：

```sh
python tools/update_report.py --cached data/source-snapshot.json --output-dir .
python tools/verify_report.py index.html data/source-snapshot.json
python -m unittest discover -s tools -p 'test_*.py'
```

快照来自公开 GitHub API，保存到仓库前移除了外部地址。清理地址不改变编号、标题、状态、日期、标签、评论内容（除地址）或代码缩进。报告自身不含外部 URL、backlink、图片、外部脚本或网络请求。

本工作区中生成器位于 `torch-xpu-ops-issue-report-tools/`。使用工作区路径时将命令中的 `tools/` 替换为该目录，缓存原始快照位于其 `snapshots/` 子目录。

## 获取最新数据

需要 Python 3 和已经登录的 GitHub CLI（`gh`）：

```sh
python tools/update_report.py --output-dir .
```

所有 GitHub 请求是查询，不写 issue、PR、评论、标签或其他上游仓库内容。采集完整评论与交叉引用，分页后核对数量，再独立核对 Open issue 集合。若其他连接被截断或集合在采集中变化，停止发布。

新快照保存在 `tools/snapshots/YYYY-MM-DD.json`，CSV 按日期输出。报告为静态快照，刷新网页不会触发 API 请求；此仓库不含自动定时任务。发布新快照前更新 `data/source-snapshot.json`，验证通过后提交 `index.html`、CSV 和快照。

## 文件职责

- `update_report.py`：GitHub 只读采集、分页和集合验证。
- `issue_facts.py`：问题表现、记录性质、发现渠道、材料和验证候选线索。
- `render_report.py`：分类、去重 PR、人工更正、CSV 与 HTML 输出。
- `report-template.html`、`report.css`、`report.js`：模板、桌面布局和交互；生成时内联。
- `overrides.json`：实际应用的人工核查配置，默认空。
- `test_issue_facts.py`、`verify_report.py`、`check_browser.cjs`：规则测试、数据验证与浏览器回归。

## 人工更正

不直接编辑生成的 HTML。修改 `overrides.json`，并保留原因、核查人、日期和证据。示例（替换编号及内容后使用）：

```json
{
  "schema_version": 1,
  "issues": {
    "5125": {
      "reason": "已核对错误设备输入的异常语义",
      "reviewer": "实际核查人",
      "reviewed_at": "2026-10-15",
      "evidence": "公开正文中描述错误异常类型的原文及复测记录",
      "set": {
        "public_record_status": "缺陷/失败线索",
        "impact_types": ["正确性"]
      }
    }
  }
}
```

允许更正 `surface_scope`、`impact_types`、`public_record_status`、`primary_blocker`。已人工核对的验证记录通过 `verification` 写入，必须同时包含 `version`、`date`、`result`（仍复现/未复现/部分解决）、`hardware`、`evidence`。没有这些事实时保持未确认；人工评论关键词仅作为候选证据，不自动宣称已验证。

## 统计口径

- 所有顶部指标均为 issue 条数；关联 PR 的去重数量单列，不把一对多引用混作 PR 数量。
- 关闭引用由 GitHub `closingIssuesReferences` 提供；普通交叉引用不计入关闭引用 PR 队列，也不能据其缺失断言没有修复。
- 负责人字段和实际阻塞分开；没有 assignee、带 skipped 标签、较久没有人工评论均不自动证明阻塞。
- `Unexpected success` 为测试预期需核对；`not implemented` 不自动代表新功能请求。
- 发现渠道与问题影响分开；正确性和 regression 标签分开。
- 技术范围、影响及记录性质是规则推断，未知不强行分类。范围不依赖旧快照。
- 整体分布固定使用全部 issue；矩阵可切换数量和行内占比，未知列用中性灰。相同维度多选为 OR，不同维度为 AND。
- 详情按需解析；完整正文和普通 PR 按需展示，评论每批 20 条。复制代码使用原始 Markdown 代码块，不把不连续的代码行拼成完整脚本。

## 浏览器检查

只在本地/开发环境安装可选工具，不影响页面运行：

```sh
npm install --no-save playwright axe-core
npx playwright install chromium
PLAYWRIGHT_MODULE=playwright AXE_MODULE=node_modules/axe-core/axe.min.js node tools/check_browser.cjs
```

可传入在线 URL 验证部署结果。自动检查覆盖多选、历史、矩阵数据、引用语义、懒加载、原文代码、导出分享及浅/深色可访问性，不针对手机端适配。
