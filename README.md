# TIPS 黄金宏观监控

双击 `生成TIPS黄金报告.bat` 后，会自动抓取并生成一份网页报告：

- 10 年期 TIPS 实际收益率、10 年/2 年美债、联邦基金利率、美元指数
- COMEX 黄金期货、COMEX 白银期货、原油、铜、VIX
- COMEX 金银期权链；若公开接口取不到，则自动使用 GLD/SLV 期权作为代理并在报告标注
- 黄金周度汇总、关键涨跌幅、相关性、简单风险信号
- JSON 原始数据、Markdown 摘要、静态 HTML 网页

输出目录：

- `reports/latest.html`
- `reports/markdown/YYYY-MM-DD.md`
- `reports/data/YYYY-MM-DD.json`

如果希望报告里的文字总结由 OpenAI 生成，把 `.env.example.bat` 复制为 `.env.bat`，填入 `OPENAI_API_KEY`。不配置也可以运行，会使用本地规则生成总结。
