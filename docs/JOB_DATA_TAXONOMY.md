# 岗位数据整理、分类与展示技术规范

## 1. 文档目标

本规范定义职位从 JobSpy 原始数据进入平台后，如何整理为可供职位卡片、详情页、
搜索筛选、市场统计、简历匹配和技能差距分析共同使用的稳定数据。

核心原则：

- 来源原始值必须保留，派生字段必须可重新计算；
- “实习/Co-op/正式工作”和“全职/兼职”是两个独立维度；
- 岗位大类主要根据职位名称判定，避免 JD 中偶然出现的技术词造成误分类；
- 技能和要求从 JD 提取，但不能反过来改变岗位本身的类别；
- 所有派生字段带 `classification_version`，规则升级后可批量回填；
- API 只返回稳定字段，不直接暴露第三方原始 payload。

## 2. 数据分层

```text
JobSpy 原始行
  → NormalizedJob：固定第三方边界字段
  → CanonicalJobInput：标准公司、地点、薪资和链接
  → JobClassification：机会类型、工时、类别、技能、要求标签
  → PostgreSQL jobs：展示和筛选的权威数据
  → job.v3 / job-page.v3：前端稳定接口
```

`raw_job_snapshots` 保存来源原文，`jobs` 保存当前标准职位。规则升级时从 `jobs`
重新计算派生字段，不需要重新抓取网站。

## 3. 展示字段

### 3.1 基础身份

| 字段 | 类型 | 用途 |
|---|---|---|
| `id` | UUID | 对外稳定职位 ID |
| `title` | string | 来源职位名称，用于展示 |
| `title_normalized` | string | 去重和内部匹配，不直接展示 |
| `company_name` | string/null | 公司展示名 |
| `company_industry` | string/null | 公司行业 |
| `company_logo_url` | URL/null | 职位卡片 Logo |
| `company_website_url` | URL/null | 公司详情跳转 |
| `source_count` | integer | 同一职位被多少来源共同验证 |

前端不得展示 `company_normalized` 或 `title_normalized`，它们是内部比较键。

### 3.2 地点和工作模式

| 字段 | 示例 | 说明 |
|---|---|---|
| `location.text` | `Toronto, ON, CA` | 来源地点原文 |
| `location.display_name` | `Toronto, ON, CA` | 前端首选显示值 |
| `location.city` | `Toronto` | 城市筛选 |
| `location.region_code` | `ON` | 省/州代码筛选 |
| `location.region_name` | `Ontario` | 省/州显示名 |
| `location.region_type` | `province` | `province/territory/state/region` |
| `location.country_code` | `CA` | ISO 两位国家码 |
| `location.country_name` | `Canada` | 国家显示名 |
| `work_mode` | `remote` | `onsite/hybrid/remote/unknown` |

来源明确标记远程或 JD 提供明确远程类型时才设置 `remote/hybrid`。来源返回
`is_remote=false` 只表示“没有标记为远程”，不能据此断言为现场办公，因此保留
`unknown`。

地点采用明确的三级层级：`country → region → city`。加拿大省和地区统一为官方
两位码，例如 Ontario/ON 统一存储为 `ON`，Alberta 统一为 `AB`，避免 facets 和
趋势统计出现重复地区。常见城市别名也统一为一个官方显示名，例如 Montreal 和
Montréal 都存入结构化城市 `Montréal`。`location.text` 永远保留来源原文。

### 3.3 机会性质与工时

`opportunity_type` 回答“这是什么性质的机会”：

| 值 | 中文含义 |
|---|---|
| `internship` | 实习 |
| `co_op` | 学校 Co-op/带薪实习项目 |
| `new_grad` | 应届生/校招 |
| `apprenticeship` | 学徒项目 |
| `regular` | 普通正式岗位 |
| `contract` | 合同工 |
| `temporary` | 临时岗位 |
| `seasonal` | 季节性岗位 |
| `unknown` | 无法确定 |

`schedule_types` 回答“每周工作量是什么”：

- `full_time`：全职；
- `part_time`：兼职；
- `flexible`：来源同时允许全职和兼职；
- `unknown`：没有可靠信息。

因此一个职位可以同时是：

```json
{
  "opportunity_type": "co_op",
  "schedule_types": ["full_time"],
  "primary_schedule_type": "full_time"
}
```

分类优先级为：标题中的 Co-op → 标题中的 internship → new grad → 来源类型 →
合同/临时/季节性 → regular。这样 `Software Developer Co-op` 不会被来源统一的
`internship` 值覆盖。

### 3.4 岗位大类和子类

`job_category` 是单值大类，供导航、趋势统计和主筛选使用：

- `software_engineering`
- `data_ai`
- `cybersecurity`
- `cloud_devops`
- `qa_testing`
- `product_design`
- `product_management`
- `it_support`
- `hardware_embedded`
- `research`
- `engineering`
- `architecture_planning`
- `business_operations`
- `finance`
- `marketing_sales`
- `human_resources`
- `healthcare`
- `education`
- `skilled_trades`
- `legal`
- `customer_service`
- `supply_chain`
- `administrative`
- `other`

`job_subcategories` 是标题可确认的岗位方向，例如：

- 软件：`frontend/backend/full_stack/mobile/game_development`；
- 数据与 AI：`data_engineering/data_science/machine_learning/generative_ai`；
- 云平台：`devops/site_reliability/cloud`；
- 安全：`application_security/security_operations`；
- 硬件：`embedded/firmware/robotics`；
- 测试和设计：`automation_testing/ux_ui`。

大类和子类只使用标题中的角色信息。JD 中出现 React、Power BI 或机器人项目时，
这些内容进入 `skill_tags`，不会把建筑设计师改成软件工程师，也不会把现场工程师
改成数据分析师。

### 3.5 技能与岗位要求标签

`skill_tags` 是规范化的小写机器标签，当前覆盖：

- 语言：Python、Java、JavaScript、TypeScript、C/C++、C#、Go、Rust、SQL、R；
- 前后端：React、Angular、Vue、Node.js、Django、FastAPI、Spring、.NET；
- 云与平台：AWS、Azure、GCP、Docker、Kubernetes、Terraform、Linux、Git；
- 数据：PostgreSQL、MySQL、MongoDB、Redis、Spark、Pandas；
- AI：PyTorch、TensorFlow、scikit-learn、LLM；
- 工程工具：REST API、GraphQL、微服务、Agile、Jira、Figma、Power BI、Tableau。

来源直接提供的技能和 JD 提取技能会合并去重。职位列表返回这些标签，详情页同时
保留 `skills` 来源原值。

`requirement_tags` 当前识别：

- `visa_sponsorship_available`
- `no_visa_sponsorship`
- `security_clearance`
- `driver_license`
- `travel_required`
- `relocation_available`
- `weekend_shift`
- `evening_shift`

这些标签适合在详情页作为“重要要求”展示，也可用于后续推荐排除条件。

### 3.6 薪资

来源薪资字段原样标准化为：

```json
{
  "interval": "hourly",
  "minimum": 25,
  "maximum": 32,
  "currency": "CAD",
  "source": "direct_data",
  "annualized_minimum": 52000,
  "annualized_maximum": 66560
}
```

年薪换算只用于跨岗位排序和筛选：

- 小时薪 × 2,080；
- 日薪 × 260；
- 周薪 × 52；
- 月薪 × 12；
- 年薪 × 1。

职位卡片优先显示来源原始周期，例如 `$25–$32/hour`，不要把换算值伪装成来源
承诺的年薪。若薪资为空，明确显示“薪资未公开”，不推测薪资。

### 3.7 时间与来源

| 字段 | 说明 |
|---|---|
| `date_posted` | 来源发布日期，可能为空 |
| `published_at` | 非空排序时间；无发布日期时使用首次发现时间 |
| `first_seen_at` | 平台首次发现 |
| `last_seen_at` | 最近仍可见 |
| `sources` | 详情页的所有来源链接和直接申请链接 |

前端的“新发布”应基于 `date_posted`；缺失时可使用 `first_seen_at`，但要标记为
“最近发现”而不是“最近发布”。

## 4. 展示标签

`display_tags` 是职位卡片可直接使用的有限标签组合，按以下顺序产生：

1. 机会性质；
2. 岗位大类；
3. 工时类型；
4. 远程模式；
5. 最多五个主要技能。

前端建议最多展示 4–6 个，其余折叠。筛选逻辑必须使用结构化字段，不能依赖
`display_tags`，因为该字段是面向视觉展示的摘要。

## 5. API

### 5.1 列表与详情

- `GET /api/v1/jobs` 返回 `job-page.v3`；
- `GET /api/v1/jobs/{job_id}` 返回 `job.v3`；
- JSON Schema：`schemas/job-page.v3.json`、`schemas/job.v3.json`。

新增筛选参数：

| 参数 | 示例 |
|---|---|
| `opportunity_type` | `co_op` |
| `city` | `Toronto` |
| `schedule_type` | `full_time` |
| `category` | `software_engineering` |
| `subcategory` | `backend` |
| `skill` | `python` |
| `has_salary` | `true` |
| `annual_salary_min` | `60000` |

示例：

```http
GET /api/v1/jobs?country=CA&opportunity_type=co_op&schedule_type=full_time&category=software_engineering&skill=python
```

### 5.2 筛选聚合

`GET /api/v1/jobs/facets` 返回 `job-facets.v2`，包含当前有效职位中实际存在的：

- 机会性质及数量；
- 工时、岗位大类和工作模式及数量；
- 技能、国家、省州、城市、公司及数量。

前端使用该接口生成筛选器，并在选项旁显示数量，不需要硬编码当前数据分布。

## 6. 前端展示建议

职位卡片首屏字段：

1. `title`；
2. `company_name` 和 Logo；
3. `location.display_name` + `work_mode`；
4. 来源周期薪资或“薪资未公开”；
5. `opportunity_type` + `primary_schedule_type`；
6. `job_category` 和最多三个技能；
7. 发布日期/首次发现日期；
8. 直接申请链接。

职位详情页再展示全部技能、重要要求、公司行业、JD 和全部来源链接。

## 7. 版本、回填和运维

分类规则位于 `src/wecanfindintern/domain/classification.py`。修改匹配词典或分类行为时：

1. 增加 `CLASSIFICATION_VERSION`；
2. 如增加数据库允许值，新增迁移并更新 CHECK constraint；
3. 增加误判和边界测试；
4. 执行迁移；
5. 执行回填；
6. 检查分类分布和随机样本；
7. 重新导出 JSON Schema。

```bash
PYTHONPATH=src .venv/bin/python scripts/migrate.py
PYTHONPATH=src .venv/bin/python scripts/backfill_job_classification.py
PYTHONPATH=src .venv/bin/python scripts/export_schemas.py
```

`backfill_job_classification.py` 默认只更新旧版本岗位；`--force` 用于同版本规则的
开发验证。脚本按 ID 游标分批处理，可重复执行。

## 8. 已知限制与后续扩展

- 当前分类是可解释的确定性规则，适合稳定上线和人工审计；长尾标题会落入 `other`；
- 技能词典优先覆盖软件、数据和工程岗位，后续应根据真实未识别词频持续扩展；
- 地点解析基于来源文本，不做地图地理编码；未来可增加经纬度和城市标准 ID；
- 薪资不做税前/税后、奖金、股票和币种汇率换算；
- JD 中否定语境的复杂判断仍有限，例如“无需安全许可”可能需要更强的句法规则；
- 当确定性规则覆盖率稳定后，可增加 AI 分类作为低置信长尾补充，但 AI 结果必须带
  模型版本、置信度和可重跑记录，不能覆盖来源原始数据。

建议持续监控：大类 `other` 比例、工时 `unknown` 比例、无技能标签比例、薪资公开率、
每个规则版本的分类变化率和抽样误判率。
