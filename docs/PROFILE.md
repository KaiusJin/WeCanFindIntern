# Profile 与 Resume Import

Profile 模块把英文文本型简历转换为用户确认后的结构化求职资料，供后续岗位匹配、
推荐和申请辅助复用。

## 产品范围

Profile v1 包含：

- 基本信息；
- Education；
- Work Experience；
- Projects；
- Skills；
- Certifications；
- Languages；
- Awards。

不包含 Volunteer、Publications、Interests、References、照片、性别、年龄、薪资期望、
工作授权和 LinkedIn 自动导入。

简历输入严格限定为：

- 可选择文字的英文 PDF，最多 8 MB、20 页；
- UTF-8 英文 LaTeX `.tex` 源码，最多 1 MB；
- 页面也支持粘贴 LaTeX 源码。

不支持图片、扫描件、OCR、DOC/DOCX、非英文简历或编译 LaTeX。

## GitHub 方案整合

实现采用能力组合，而不是复制整套外部应用：

- JSON Resume / NoStrings Resume：字段分组和可编辑结构；
- CVForge：PDF 文本恢复、解析诊断和人工审核工作台思路；
- ResumeParser：章节识别、规则提取和保留原文 fallback；
- ResumeAI：技能词库、别名规范化和分类。

内部模型为 `profile.v1`，只保留本产品当前需要的字段。外部项目没有作为运行时服务，
避免把其 UI、框架和额外依赖引入现有 FastAPI 单体。

## 安全边界

上传文件视为不可信输入，并依次检查：

1. 文件名不能带目录、控制字符或非允许扩展名；
2. 声明 MIME 必须与 `.pdf` / `.tex` 相符；
3. PDF 必须有 `%PDF-` 魔数和有效 `%%EOF`，防止扩展名伪装；
4. PDF 拒绝加密、超页数、AcroForm、JavaScript、嵌入文件、自动动作和不安全注释；
5. 只允许 `http`、`https`、`mailto` 普通链接注释；
6. PDF 必须提取出足够文本，因此图片和扫描 PDF 会被拒绝；
7. LaTeX 只做 UTF-8 文本解析，绝不编译；
8. LaTeX 拒绝 `input`、`include`、`write18`、文件读写、Lua 执行和 catcode 等命令；
9. 提取文本设总长度上限，并验证英文字符比例和简历章节特征。

浏览器的 `accept` 只改善文件选择体验，不作为安全措施；所有检查都在后端重新执行。

## 数据与审核流程

```text
上传 PDF / LaTeX
  -> 安全验证和文本提取
  -> 章节与技能规则解析
  -> Resume Import Draft
  -> 用户审核、修改或丢弃
  -> Confirm
  -> Profile + Profile Version
```

自动解析不会直接覆盖已保存 Profile。页面先把结果显示为 Draft，确认时才写入当前资料。
每个解析字段保留 `evidence` 和 `confidence`，不确定字段保持为空。

Resume 原件、提取文本、解析草稿和 Profile 历史版本分开保存。删除 Resume 会级联删除其
Import 草稿；已确认 Profile 数据继续独立存在。

## API

```text
GET    /api/v1/profile
PUT    /api/v1/profile
GET    /api/v1/profile/export
POST   /api/v1/profile/resumes
GET    /api/v1/profile/resumes
DELETE /api/v1/profile/resumes/{resume_id}
POST   /api/v1/profile/imports/{import_id}/confirm
```

当前项目仍是单用户本地 MVP。接入认证后，必须在全部 Profile、Resume 和 Import 查询上
加入 `user_id` 隔离，才可以部署为多用户服务。

