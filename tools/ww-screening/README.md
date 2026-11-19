# WaterlooWorks 个人筛选工具

在项目根目录启动：

```sh
.venv/bin/python tools/ww-screening/serve.py
```

打开 http://127.0.0.1:8766，选取导出的 JSON 或 CSV，输入 DeepSeek API Key，点击开始。
默认并发 15，结果只显示匹配 Job ID，支持复制和 TXT 下载。刷新/关闭页面不保留结果。

寻找 Data Engineering、Web 开发、前端开发、软件 QA 和测试岗位；缺失 JD、无有效原文依据或 API 失败不会进入匹配名单。
模型判断仍可能出错。不会修改原始文件或 App 数据。Key 仅在内存中使用，不写入持久存储。

模型与 JSON 输出参考：
- https://api-docs.deepseek.com/api/list-models
- https://api-docs.deepseek.com/guides/json_mode/

验证：`.venv/bin/pytest tests/test_ww_screening.py`
