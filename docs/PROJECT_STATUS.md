# WeCanFindIntern 项目任务拆分与当前状态

更新时间：2026-08-26
状态口径：以当前工作区文件和本地可运行验证为准。

## 一、项目当前目标

当前项目的核心目标是完成可运行的多来源实习/职位搜索与浏览 MVP：

```text
JobSpy 多来源抓取
  → 第三方数据标准化
  → 岗位分类、技能/要求标签、地点和薪资标准化
  → 去重、幂等写入 PostgreSQL
  → 定时采集、断点续跑和失败重试
  → 版本化岗位 API 与筛选 Facets
  → 全英文职位搜索、筛选、列表和详情页面
```

当前 P0 数据链路、P1 搜索浏览主体和基础申请跟踪已经完成；简历匹配、推荐和完整求职流程仍在后续计划中。

## 二、已经完成的步骤

| 阶段 | 已完成内容 | 现有证据 | 状态 |
|---|---|---|---|
| 1. 项目骨架 | Python 包、配置、脚本、测试目录和 vendored JobSpy 已建立 | `src/`、`scripts/`、`tests/`、`vendor/JobSpy/` | 已完成 |
| 2. 多来源采集接入 | 已按 JobSpy 结构接入 Indeed、LinkedIn、Glassdoor、ZipRecruiter、Google Jobs 等来源配置 | `src/wecanfindintern/ingestion/jobspy_adapter.py`、`config/collection_plans.json` | 已实现，真实覆盖仍需扩大 |
| 3. 原始数据保留 | 支持保存 JobSpy 原始 CSV | `data/raw/*_jobspy_raw.csv` | 已完成 |
| 4. 数据标准化 | 将 DataFrame 转换为稳定的 `NormalizedJob`，处理空值、日期、URL、薪资、技能等字段 | `jobspy_adapter.py` | 已完成 |
| 5. 业务岗位模型 | 已有 `CanonicalJobInput`、公司、地点、薪资和岗位字段模型 | `src/wecanfindintern/domain/jobs.py` | 已完成 |
| 6. 岗位分类 | 支持机会类型、工时、岗位大类、子类、技能标签、要求标签和展示标签 | `src/wecanfindintern/domain/classification.py` | 已完成 |
| 7. 地点/薪资/招聘季节规范化 | 支持国家、省州/地区、城市三级地点；薪资与招聘季节均采用全量正则优先、DeepSeek JSON 约束回退并按岗位内容哈希持久化 | `domain/salary*.py`、`domain/recruiting_term*.py` | 已完成 |
| 8. 去重和幂等设计 | 已实现来源指纹、URL/公司/地点/JD 候选去重、事务和 advisory lock | `deduplication.py`、`db/ingestion_repository.py` | 已实现，需真实规模验收 |
| 9. 数据库层 | 已有迁移、连接池、写入仓库、读取仓库、自动采集和申请跟踪相关表设计 | `migrations/0001–0009`、`src/wecanfindintern/db/`、`src/wecanfindintern/tracker/` | 已实现并完成本地迁移 |
| 10. 调度系统 | 已实现 collection plan、4 小时间隔、租约、offset、断点、重试和 exhausted 状态 | `scheduler/`、`config/collection_plans.json` | 已实现，需故障演练 |
| 11. 数据 API | 已实现职位列表、详情、筛选、游标分页、Facets、采集运行状态和采集计划接口 | `src/wecanfindintern/api/app.py`、`docs/DATA_API.md` | 已实现 |
| 12. API 契约 | 已有 `job.v3`、`job-page.v3`、`job-facets.v2` schema 和契约检查脚本 | `schemas/`、`scripts/dev/verify_data_api_contract.py` | 已完成 |
| 13. 申请跟踪 | 支持申请记录、阶段、备注、统计和职位详情关联 | `migrations/0009_application_tracker.sql`、`src/wecanfindintern/tracker/` | 已实现，仍需完善用户隔离 |
| 14. 技术文档 | 已有 JobSpy 集成、数据 API、岗位分类/展示规范和项目状态文档 | `docs/` | 已完成 |
| 15. 搜索和浏览页面 | 全英文搜索框、筛选器、职位卡片、详情弹窗、分页加载、原职位跳转和申请跟踪入口 | `web/`、`src/wecanfindintern/api/app.py` | 已完成 |

## 三、当前做得怎么样

### 已在当前环境确认的结果

- 最新薪资周期一致性改动已完成 12 项针对性测试和 JavaScript/Python 语法检查。
- 本轮已完成 Python/JavaScript 语法检查和 `git diff --check`。
- 当前已有 Indeed 原始采集产物：1 个 CSV 和 1 个标准化 JSONL 文件。
- 当前标准化 JSONL 样本为 5 条记录。
- 代码结构已覆盖从采集到 API 的主要后端链路。
- 最近一次 staged campaign 完成 18 个来源查询，采集 482 条唯一来源记录；统一去重后新增 379、合并 28、已有 75。
- 薪资严格在全量去重后执行：全量正则完成后新增 95 条，再由 DeepSeek 新增 72 条。
- 后续采集范围已收紧为美国和加拿大的 Computer Science / Software、Data Science、Machine Learning / AI，共 19 个关键词、38 个国家关键词计划和 76 个来源查询。
- 最新完整采集阶段获得 1,795 条唯一来源记录；统一去重后新增 1,459、合并 157、已有 179。
- 招聘季节已完成全量回填：1,869 个活跃岗位全部检查，502 个具有标准化季节，未检查和 pending generation 均为 0。
- 招聘季节支持完整名称、Autumn/Fall 归一、常见缩写、两位/撇号年份、学期夹词和紧凑 term code；冲突表达不强制猜测。
- 每次招聘季节 DeepSeek 调用先生成持久化 UUID，并保存输入片段、模型、JSON 输出和 token 用量；页面只显示标准化 `Season Year`。
- 每个岗位使用数据库内部自增 `jobs.id` 持久化派生结果；再次抓到相同来源岗位且 JD 未变化时复用已保存薪资，不重复调用 LLM。
- 加拿大省/地区和美国州统一映射为标准两位代码，并清洗常见错误，例如 `Ontraio → ON`。
- BMO 的 `$61,600–$113,900` 年薪已识别，并在页面换算为 `CAD $29.62–$54.76/hour`。
- 已修复 JobSpy 强制年化造成的周期错配：42 条异常薪资由正则恢复，最终全库复查为 0 条未解决；包括 `$14.50–$30.51/hour` 和 `$24–$29/hour` 样例。
- 本地首页和岗位 API 已启动在 `http://127.0.0.1:8000`，HTTP 检查返回 200。
- 任务拆分、数据契约、迁移文件和运行脚本相对齐全，已经不是单纯的原型目录。

### 目前的质量判断

| 方面 | 判断 | 说明 |
|---|---|---|
| 代码骨架 | 较好 | 分层清晰，采集、领域模型、数据库、调度和 API 已拆开 |
| 单元测试 | 合格但范围偏小 | 17 项测试覆盖分类、薪资、地点、适配器和部分调度异常 |
| 单来源采集 | 已有可运行验证 | 当前可见的真实数据产物来自 Indeed |
| 多来源采集 | 已接入但未完全验收 | 还缺每个来源的真实成功率、字段完整率和反爬表现 |
| 数据库能力 | 设计和代码已具备 | 当前状态快照未重新连接 PostgreSQL 验证实际数据量和迁移状态 |
| 调度可靠性 | 已实现主要机制 | 仍需进程中断、网络失败、数据库断连和重复 worker 测试 |
| API 生产准备度 | 接近后端 MVP | 还缺并发性能、真实规模索引验证和监控告警 |
| 产品完整度 | 搜索浏览和基础申请跟踪 MVP 已完成 | 简历匹配、推荐、认证和完整求职流程尚未完成 |

## 四、还剩什么步骤

### P0：先把数据平台稳定下来

- [ ] 对每个计划中的真实来源分别抓取验收：成功率、超时、限流、字段空值率、重复率。
- [ ] 生成采集运行报告：`fetched / created / merged / unchanged / failed`、耗时和错误摘要。
- [ ] 建立数据质量指标：地点完整率、JD 完整率、薪资公开率、技能标签覆盖率和重复率。
- [ ] 在真实 PostgreSQL 上确认迁移、索引、游标分页和 Facets 查询。
- [ ] 用真实规模数据运行 `EXPLAIN (ANALYZE, BUFFERS)`，验证地点、技能、薪资和关键词查询。
- [ ] 演练 worker 中断、数据库断连、来源超时、重复 worker 并发和断点重放。
- [ ] 增加失败告警、重试上限通知、结构化日志和基础运行监控。
- [ ] 明确原始 JD 保存周期、删除策略、来源条款和访问频率限制。
- [ ] 补齐城市标准 ID、时区、无城市远程岗位和多地点岗位处理。

### P1：完成职位搜索和浏览产品

- [x] 职位列表、卡片、详情弹窗和直接申请跳转。
- [x] 国家、省州/地区和城市筛选。
- [x] 机会类型、工时、岗位类别、技能、远程模式和薪资筛选。
- [x] 关键词搜索框和后端组合查询。
- [x] 页面统一使用英文；薪资统一只展示时薪，不展示原始薪资文本和提取来源。
- [ ] 自然语言查询解析，例如“加拿大 Python AWS 后端实习”。
- [ ] 趋势数据：岗位数量、技能热度、地区分布和公司活跃度。

### P2：加入简历和推荐能力

- [ ] 简历上传、文本解析和用户技能画像。
- [ ] 简历与 JD 的结构化匹配评分。
- [ ] 缺失技能、推荐原因和技能提升建议。
- [ ] 语义检索、候选岗位排序和推荐解释。
- [ ] 为模型输出保存版本、置信度、输入快照和可重跑记录。

### P2：完善求职管理

- [x] 申请记录、阶段流转、备注和基础统计。
- [ ] 收藏职位并与申请记录关联。
- [ ] 增加提醒、申请时间线和岗位状态变化记录。
- [ ] 区分已关闭、重复和已申请岗位。

### P3：产品化和上线运营

- [ ] 用户认证、权限和个人数据隔离。
- [ ] 隐私、简历删除、数据导出和审计日志。
- [ ] 生产部署、密钥管理、备份恢复和迁移流程。
- [ ] API 限流、日志、指标、链路追踪和成本监控。
- [ ] 端到端测试、浏览器验收测试和发布流水线。

## 五、建议的下一步顺序

1. 先对所有来源做一次真实采集验收，并产出数据质量表。
2. 接着完成调度/重试/断点/去重的故障测试。
3. 扩大职位搜索浏览 MVP 的真实数据规模并处理长尾字段。
4. 然后增加自然语言搜索和趋势 API。
5. 最后加入简历匹配、推荐和完整求职流程管理。

## 六、当前结论

项目已经完成多来源岗位采集后端和职位搜索浏览 MVP，并加入正则 + DeepSeek JSON 约束的薪资与招聘季节混合提取。当前可本地演示，但还不能称为生产稳定版本。下一阶段重点是扩大真实来源覆盖、提高数据质量、验证故障恢复并补充监控告警。

## 七、关键文件

- [项目 README](../README.md)
- 当前剩余任务见本文第四、第五节；原任务拆分内容已合并到本状态文档。
- [JobSpy 集成说明](JOBSPY_INTEGRATION.md)
- [数据 API 说明](DATA_API.md)
- [岗位字段与分类规范](JOB_DATA_TAXONOMY.md)
- [采集适配器](../src/wecanfindintern/ingestion/jobspy_adapter.py)
- [岗位领域模型](../src/wecanfindintern/domain/jobs.py)
- [岗位分类规则](../src/wecanfindintern/domain/classification.py)
- [数据库写入仓库](../src/wecanfindintern/db/ingestion_repository.py)
- [调度运行器](../src/wecanfindintern/scheduler/runner.py)
- [API 应用](../src/wecanfindintern/api/app.py)
