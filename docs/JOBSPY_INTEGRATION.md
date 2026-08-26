# JobSpy 集成与返回格式

## 版本基线

- 上游仓库：<https://github.com/speedyapply/JobSpy>
- Python 包名：`python-jobspy`
- 检查版本：`1.1.82`
- Vendored 提交：`fda080a373e8226f3fd60635323f5da9af9892b1`
- Python 要求：3.10 或更高版本

上游源码保存在 `vendor/JobSpy`。项目代码通过本地 editable 安装使用这份源码，便于固定版本、审计行为和有计划地升级。

## `scrape_jobs()` 返回值

`jobspy.scrape_jobs()` 返回 `pandas.DataFrame`，不是 `JobPost` 列表。JobSpy 先让各来源返回 Pydantic `JobPost`，再统一压平成 DataFrame。

在 `1.1.82` 中，非空结果按以下 34 个字段排序：

| 字段 | 常见类型 | 含义 |
|---|---|---|
| `id` | string / null | 来源职位 ID |
| `site` | string | 来源标识 |
| `job_url` | string | 来源页面 URL |
| `job_url_direct` | string / null | 直接申请 URL |
| `title` | string | 原始职位名称 |
| `company` | string / null | 公司名称 |
| `location` | string / null | 已压平的地点文本 |
| `date_posted` | date / null | 发布日期 |
| `job_type` | string / null | 多个类型以逗号连接 |
| `salary_source` | string / null | `direct_data` 或 `description` |
| `interval` | string / null | yearly/monthly/weekly/daily/hourly |
| `min_amount` | number / null | 最低薪资 |
| `max_amount` | number / null | 最高薪资 |
| `currency` | string / null | 币种 |
| `is_remote` | boolean / null | 是否远程 |
| `job_level` | string / null | LinkedIn 职级 |
| `job_function` | string / null | 职能 |
| `listing_type` | string / null | 职位列表类型 |
| `emails` | string / null | 多个邮箱以逗号连接 |
| `description` | string / null | 职位描述 |
| `company_industry` | string / null | 公司行业 |
| `company_url` | string / null | 公司在来源站点的 URL |
| `company_logo` | string / null | 公司 Logo URL |
| `company_url_direct` | string / null | 公司官方网站 URL |
| `company_addresses` | string / null | 公司地址 |
| `company_num_employees` | string / null | 员工数量标签 |
| `company_revenue` | string / null | 营收标签 |
| `company_description` | string / null | 公司简介 |
| `skills` | string / null | Naukri 技能，多值以逗号连接 |
| `experience_range` | string / null | Naukri 经验范围 |
| `company_rating` | number / null | Naukri 公司评分 |
| `company_reviews_count` | integer / null | Naukri 评价数量 |
| `vacancy_count` | integer / null | Naukri 职位空缺数量 |
| `work_from_home_type` | string / null | Naukri 工作地点类型 |

## 重要行为

1. **空结果格式不同**：JobSpy 原生在没有职位时返回零行、零列 DataFrame；本项目的 `stabilize_jobspy_frame()` 会补成零行、34 列。
2. **嵌套对象已丢失**：`Location` 和 `Compensation` 在返回前已被压平；如果以后需要原始嵌套对象，需要直接对接各 scraper，而不是只使用 `scrape_jobs()`。
3. **多值字段已压平**：`job_type`、`emails`、Naukri 的 `skills` 都是逗号分隔字符串。
4. **来源字段不对称**：很多字段只属于单一来源，大量空值属于正常现象。
5. **薪资来源有区别**：`direct_data` 表示来源直接提供；`description` 表示 JobSpy 从描述中解析。后者当前主要面向美国薪资文本。
6. **描述格式可选**：支持 `markdown`、`html` 和 `plain`，项目默认保存 Markdown。

## 项目内部格式

业务代码使用 `NormalizedJob`，不直接使用第三方 DataFrame。主要结构如下：

```json
{
  "source_fingerprint": "sha256...",
  "source": "indeed",
  "source_job_id": "abc123",
  "source_url": "https://...",
  "direct_url": "https://...",
  "title": "Software Engineer Intern",
  "company_name": "Example Co",
  "location_text": "Toronto, ON, Canada",
  "date_posted": "2026-08-24",
  "employment_types": ["internship"],
  "is_remote": false,
  "seniority": null,
  "description": "...",
  "contact_emails": [],
  "salary": {
    "interval": "hourly",
    "minimum": 25,
    "maximum": 32,
    "currency": "CAD",
    "source": "direct_data"
  },
  "company": {
    "industry": null,
    "url": null,
    "direct_url": null,
    "logo_url": null,
    "addresses": null,
    "employee_count_label": null,
    "revenue_label": null,
    "description": null,
    "rating": null,
    "reviews_count": null
  },
  "source_skills": [],
  "experience_range": null,
  "vacancy_count": null,
  "work_from_home_type": null,
  "raw": {}
}
```

`raw` 会保留清洗后的 JobSpy 原始行，便于排查字段映射和未来重新处理。

## 升级规则

升级 JobSpy 时应：

1. 更新 `vendor/JobSpy`；
2. 对比 `jobspy.util.desired_order` 和 `JobPost` 模型；
3. 更新 `JOBSPY_COLUMNS` 与本说明；
4. 运行测试；
5. 小规模运行 `inspect_jobspy_output.py`，确认真实来源返回格式。
