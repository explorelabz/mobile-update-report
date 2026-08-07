# Cursor Automations 运行说明

本仓库默认 **不再调用 OpenAI**。评估有两条路径：

| 路径 | 适用 | 命令 |
|------|------|------|
| 本地规则 | launchd / 本机直接跑 | `python main.py`（`AI_PROVIDER=local`） |
| Cursor Agent | Automations 云端 | `--dump-pending` → Agent 写报告 → `--push-report` |

## Automations 推荐流程

1. `pip install -r requirements.txt`
2. 配置环境变量（Automation Secrets）：`LARK_WEBHOOK_URL`、可选 `LARK_SECRET`、`APP_CONTEXT`
3. `python main.py --dump-pending` → 生成 `data/pending.json`
4. Agent 阅读 `prompts/system.md` + `data/pending.json`，写出 `data/report.json`：

```json
{
  "fingerprints": ["从 pending.json 原样拷贝"],
  "platforms": {
    "iOS": {
      "headline": "...",
      "timeline": [{"label": "...", "date": "YYYY-MM-DD", "note": "..."}],
      "items": [
        {
          "title": "...",
          "url": "...",
          "category": "新功能机会 | 安全合规 | 稳定性影响 | 版本时间线",
          "priority": "P0 | P1 | P2",
          "evidence": "原文摘录或 原文未公开变更细节",
          "impact": "对我们 App 的具体影响",
          "action": "可执行建议",
          "deadline": "无"
        }
      ],
      "no_update": false
    },
    "Android": { "...": "同上" }
  }
}
```

5. `python main.py --push-report data/report.json`
6. 若 `data/seen.json` 有变更：提交并 push 回仓库，供下次去重

## 注意

- 不要设置 `OPENAI_API_KEY`；云端缺 key 时旧逻辑会推「AI 评估失败」。
- 若本机仍开着 launchd 定时任务，请 unload，避免与 Automations 重复推送。
