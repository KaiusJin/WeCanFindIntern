# 职位数据接口

## 数据流

```text
JobSpy DataFrame
    → NormalizedJob（第三方边界）
    → CanonicalJobInput（平台业务契约）
    → PostgreSQL 去重写入
    → REST Data API
```

公开响应使用版本化契约：职位详情为 `job.v3`，列表分页为 `job-page.v3`。
对应 JSON Schema 保存在 `schemas/`，第三方字段变化不会直接改变公开接口。
岗位分类和值域详见 [`JOB_DATA_TAXONOMY.md`](JOB_DATA_TAXONOMY.md)。

`raw_job_snapshots` 与 `jobs` 必须分开：原始快照用于审计和重新处理，`jobs`
用于搜索与展示。业务接口不会返回第三方原始 payload。

## 初始化数据库

```bash
cp .env.example .env
set -a
source .env
set +a
docker compose up -d postgres
PYTHONPATH=src .venv/bin/python scripts/maintenance/migrate.py
```

## 抓取并写入

```bash
PYTHONPATH=src .venv/bin/python scripts/collection/ingest_jobspy_to_db.py \
  --site indeed \
  --search-term "software engineer intern" \
  --location "Toronto, ON" \
  --country-indeed Canada \
  --results-wanted 100
```

默认每 250 条提交一次事务，可通过 `--batch-size` 在 50–500 之间调整。

## 启动接口

```bash
PYTHONPATH=src .venv/bin/uvicorn wecanfindintern.api.app:app --reload
```

## API

### `GET /api/v1/jobs`

支持参数：

- `query`
- `country`
- `region`
- `city`
- `company`
- `work_mode`
- `employment_type`
- `opportunity_type`
- `schedule_type`
- `category`
- `subcategory`
- `skill`
- `source`
- `posted_after`
- `salary_min`
- `annual_salary_min`
- `has_salary`
- `currency`
- `cursor`
- `limit`，最大 100

示例：

```http
GET /api/v1/jobs?country=CA&region=ON&employment_type=internship&limit=30
```

地点使用 `country → region → city` 三级结构。返回值同时包含机器筛选码和展示名：

```json
{
  "location": {
    "text": "Toronto, ON, CA",
    "display_name": "Toronto, ON, CA",
    "country_code": "CA",
    "country_name": "Canada",
    "region_code": "ON",
    "region_name": "Ontario",
    "region_type": "province",
    "city": "Toronto"
  }
}
```

`region_type` 为 `province/territory/state/region`。`text` 保留来源原文；国家、省州和
城市字段经过标准化，用于级联筛选与统计。

返回游标分页，不返回昂贵的总行数：

```json
{
  "items": [],
  "next_cursor": null,
  "has_more": false
}
```

### `GET /api/v1/jobs/{job_id}`

返回职位详情及全部来源链接，不包含原始抓取 payload。

### `GET /api/v1/jobs/facets`

返回机会类型、工时、岗位大类、工作模式、技能、地区和公司的实时数量，
用于生成前端筛选项。

## 去重策略

### 第一层：来源内幂等

`source_fingerprint` 使用 32 字节 SHA-256 唯一索引。同一来源职位重复抓取时只更新
`last_seen_at`，原始 payload 没有变化则不重复保存快照。

### 第二层：跨来源候选生成

候选查找只使用索引：

1. 相同直接申请 URL 哈希；
2. 相同公司和地点组成的 `dedupe_block_key`；
3. 发布时间前后 60 天；
4. 最多返回 25 条候选。

不会对全部职位执行字符串相似度计算。

### 第三层：全自动候选判定

候选集在应用内比较：

- 公司名称；
- 职位名称；
- 地点或远程状态；
- 发布时间距离；
- 描述文本相似度；
- 直接申请 URL。

系统使用直接申请 URL、公司、岗位名称、地点、发布时间和 JD 的 5-token
shingle 重合度自动判定。确定是同一岗位时自动合并，否则自动保留为独立岗位，
不创建人工审核任务。每次新来源的判定分数、规则命中情况和算法版本都会写入
`dedupe_decisions`，方便审计和后续离线调参。

### 并发安全

写入时使用事务级 advisory lock，仅锁定相同来源指纹和相同去重 block，避免并发采集产生
重复职位，同时不会串行化整批任务。

## 定时采集与重试

- 自动采集 campaign 由 `config/collection_plans.json` 展开为国家、关键词和来源查询；
- 并发抓取（默认 4），失败按指数退避自动重试（默认 3 次）并带随机抖动；
- 采集进程持有单实例文件锁，launchd 定时任务与手动运行不会重叠；
- `source_overrides` 可为不兼容公共查询参数的来源单独覆盖地点等参数；
- 单个来源查询失败不会阻止其他来源；运行结束时记录失败来源数；
- 数据批次先幂等入库、完成全量去重后，再执行薪资与招聘季节 enrichment。

## 大数据量性能设计

- `jobs` 使用 `BIGINT` 聚簇友好的内部主键，对外暴露 UUID。
- 列表使用 `(published_sort_at, id)` 游标分页，不使用深度 `OFFSET`。
- 热查询索引只覆盖 `status = active` 的职位。
- 搜索向量只包含标题、公司和地点，不把长职位描述放进热 GIN 索引。
- 原始快照按月分区，并使用 BRIN 时间索引。
- 相同 payload 不重复保存，减少长期快照体积。
- 详情页单独查询来源；列表页不聚合来源 JSON。
- 数据接口不执行 `COUNT(*)`，趋势统计应由独立汇总表或异步聚合任务提供。
- 数据库连接池有明确上限，查询设置 statement timeout。

## 后续运维要求

- 提前创建下一个月的快照分区，并监控默认分区是否出现记录。
- 根据查询日志使用 `EXPLAIN (ANALYZE, BUFFERS)` 验证索引命中。
- 定期执行 `VACUUM (ANALYZE)`，并监控 GIN 与局部索引体积。
- 原始快照根据合规和重处理需求设置保留周期，过期分区使用 detach/drop 管理。
