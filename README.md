# WeCanFindIntern — JobSpy 职位采集

本目录完成项目的职位采集基础能力：

- 使用 vendored JobSpy 源码抓取职位；
- 保留 JobSpy 原始 DataFrame / CSV；
- 将第三方字段转换为项目内部稳定 JSONL 格式；
- 为每个来源职位生成可重复的 `source_fingerprint`，方便后续幂等写入；
- 提供输出格式检查和自动化测试。
- 将标准职位幂等写入 PostgreSQL，并自动完成跨来源去重；
- 自动多来源采集 campaign，带并发抓取、指数退避重试和单实例锁（launchd 每 4 小时运行）；
- 提供高性能游标分页职位数据接口。

JobSpy 源码位于 `vendor/JobSpy`，当前版本与提交记录见
[`docs/JOBSPY_INTEGRATION.md`](docs/JOBSPY_INTEGRATION.md)。

数据库结构、去重规则和 API 使用方式见
[`docs/DATA_API.md`](docs/DATA_API.md)。

岗位分类、标签、薪资换算和前端展示字段规范见
[`docs/JOB_DATA_TAXONOMY.md`](docs/JOB_DATA_TAXONOMY.md)。

Profile、英文 PDF/LaTeX 简历导入、解析审核和上传安全边界见
[`docs/PROFILE.md`](docs/PROFILE.md)。

## 环境准备

需要 Python 3.10 或更高版本。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
docker compose up -d postgres
cp .env.example .env
set -a
source .env
set +a
PYTHONPATH=src .venv/bin/python scripts/maintenance/migrate.py
```

## 查看 JobSpy 实际返回格式

```bash
PYTHONPATH=src .venv/bin/python scripts/dev/inspect_jobspy_output.py \
  --site indeed \
  --search-term "software engineer intern" \
  --location "Toronto, ON" \
  --country-indeed Canada \
  --results-wanted 5
```

该命令会显示：

- DataFrame 行列数；
- 实际列名和数据类型；
- 每列空值数量；
- 一条经过截断的示例记录。

## 抓取并保存职位

```bash
PYTHONPATH=src .venv/bin/python scripts/dev/scrape_jobs.py \
  --site indeed \
  --site linkedin \
  --search-term "software engineer intern" \
  --location "Toronto, ON" \
  --country-indeed Canada \
  --results-wanted 20 \
  --output-dir data/raw
```

每次运行会生成两个文件：

- `*_jobspy_raw.csv`：JobSpy 原始表格格式；
- `*_normalized.jsonl`：WeCanFindIntern 内部稳定格式。

## 抓取并写入数据库

```bash
PYTHONPATH=src .venv/bin/python scripts/collection/ingest_jobspy_to_db.py \
  --site indeed \
  --search-term "software engineer intern" \
  --location "Toronto, ON" \
  --country-indeed Canada \
  --results-wanted 100
```

## 启动自动多来源采集

采集关键词目录位于 `config/collection_plans.json`。当前范围限定为美国和加拿大的
Computer Science / Software、Data Science、Machine Learning / AI 实习与 Co-op，
来源为 Indeed、LinkedIn、Glassdoor、ZipRecruiter 和 Google Jobs。目录会自动展开为
国家、关键词和来源查询。

Google Jobs 需要显式搜索串：`source_overrides.google.google_search_term` 提供模板，
其中 `{search_term}` 和 `{location}` 会在每个计划运行时被关键词和地点替换
（例如 `"{search_term} near {location}"`）。

```bash
PYTHONPATH=src .venv/bin/python scripts/collection/run_collection_campaign.py
```

campaign 严格按“全部采集 → 全量去重 → 全量正则薪资 → DeepSeek 薪资 →
全量正则招聘季节 → DeepSeek 招聘季节”的顺序执行。招聘季节会统一为
`Winter/Spring/Summer/Fall + 年份`，并按标题与 JD 内容哈希持久化，内容不变时不会重复调用模型。
采集结果若无法确认属于美国或加拿大，会在数据库写入前被排除。

### macOS 定时采集

仓库提供 `launchd` 配置，默认每 4 小时运行一次完整 campaign。采集程序自身持有
单实例锁，因此定时任务与手动执行不会重叠。任务会从 `.env` 加载数据库与 DeepSeek
配置，日志写入 `logs/collector.log` 和 `logs/collector-error.log`。

```bash
mkdir -p logs ~/Library/LaunchAgents
cp config/launchd/com.kaius.wecanfindintern.collector.plist \
  ~/Library/LaunchAgents/com.kaius.wecanfindintern.collector.plist
launchctl bootstrap gui/$(id -u) \
  ~/Library/LaunchAgents/com.kaius.wecanfindintern.collector.plist
```

查看状态和日志：

```bash
launchctl print gui/$(id -u)/com.kaius.wecanfindintern.collector
tail -f logs/collector.log
tail -f logs/collector-error.log
```

公开岗位格式为 `job.v3`。JSON Schema 位于 `schemas/job.v3.json`、
`schemas/job-page.v3.json` 和 `schemas/job-facets.v2.json`，可通过以下命令重新生成：

```bash
PYTHONPATH=src .venv/bin/python scripts/dev/export_schemas.py
```

岗位分类规则升级后回填现有数据：

```bash
PYTHONPATH=src .venv/bin/python scripts/maintenance/backfill_job_classification.py
```

启动数据接口：

```bash
PYTHONPATH=src .venv/bin/uvicorn wecanfindintern.api.app:app --reload
```

启动后打开 <http://127.0.0.1:8000/> 即可使用职位搜索与浏览页面。页面支持关键词搜索，
国家/地区/城市、工作模式、机会类型、工时、职位方向、技能和薪资筛选；职位卡片可打开
详情并跳转到原始职位链接。

### 导入 WaterlooWorks 岗位

页面中的 `WaterlooWorks` 区域可以启动一个独立的本地 Chrome profile。用户自行完成
Waterloo SSO 和 MFA 后，页面会检测已登录的岗位列表，并依次导入 Full-Cycle、
Employer-Student Direct、Graduating、Contract 和 Campus 五个 job boards。每个 board
会先打开对应 URL、点击 `All Jobs`，再等待结果 table。每个 board 分别显示发现、成功和
失败数量；某个 board 无法访问时会标记失败并继续下一个。

WaterlooWorks 岗位只使用 Job ID 去重；已有 ID 会直接跳过内容检查，并独立保存在
`~/.wecanfindintern/waterlooworks.sqlite3`，不会与 Indeed、LinkedIn 等公共岗位混合或
交叉去重。密码、MFA 和浏览器会话不会写入数据库。

独立 profile 默认保存在 `~/.wecanfindintern/chrome-waterlooworks`。如果 Chrome 不在系统
默认位置，可以通过 `WATERLOOWORKS_CHROME_BINARY` 指定 Chrome 可执行文件。

## 运行测试

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

## 代码质量检查

仓库内置统一的检查入口，`make check` 会依次运行：

- `ruff check src tests scripts`（lint，0 错误为准）；
- `pytest`（单元与路由契约测试）；
- `scripts/dev/verify_frontend_api_contract.py`（校验前端 `fetch`/链接引用的
  `/api/...` 路径都存在于 OpenAPI 路由表，防止前后端断链）；
- `node --check web/modules/*.js`（前端语法）。

GitHub Actions 工作流（`.github/workflows/ci.yml`）在推送/PR 时执行同样的检查。
新增 API 或前端请求时，请保持该契约检查通过。

## 使用约束

- Indeed 中 `hours_old`、`job_type + is_remote`、`easy_apply` 三组筛选只能选择一组。
- LinkedIn 中 `hours_old` 与 `easy_apply` 不能同时使用。
- `linkedin_fetch_description` 会为每个职位增加额外请求，默认关闭。
- 不同来源并不保证填充所有字段；业务代码应使用内部标准格式，而不是直接依赖 DataFrame。
- 请遵守职位来源网站的条款、访问频率要求及适用法律。
