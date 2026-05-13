# 移动端每日更新评估机器人

每天自动监控 **iOS / Android** 的官方版本与功能更新，调用 LLM 生成「带优先级 + 行动建议」的评估报告，分平台推送到飞书 Lark 群。

## 它能告诉你什么

每天早上你会在 Lark 群里看到两条卡片消息（iOS 一条、Android 一条），每条按以下结构组织：

- **一句话摘要**：今天最重要的事
- **版本时间线**：Beta / RC / GA 的关键节点
- **逐条评估**（最多 N 条）：每条带 **优先级 P0/P1/P2** + **分类** + **对我们 App 的影响** + **建议行动** + **截止日期**
  - 🚨 硬性合规（不做就崩 / 拒审，例如 iOS Scene-Based Lifecycle 迁移、targetSdkVersion 升级、隐私清单）
  - ⚠️ 影响现有功能（例如推送、生物识别、WebView、后台任务等行为变化）
  - ✨ ASO / 体验机会（例如 App Intents、Live Activities、Predictive Back、Themed Icons）
  - 📅 版本时间线

## 系统架构

```
┌─────────────────┐    ┌──────────────┐    ┌──────────────┐    ┌────────────┐
│ 数据源（RSS）    │ -> │ 去重（本地）  │ -> │ AI 评估       │ -> │ Lark 推送   │
│ Apple Dev News  │    │ data/        │    │ OpenAI 兼容   │    │ 卡片消息    │
│ Apple Releases  │    │ seen.json    │    │ JSON 严格模式 │    │ iOS / Android│
│ Android Dev Blog│    │              │    │              │    │ 各一条      │
│ Android Studio  │    │              │    │              │    │            │
└─────────────────┘    └──────────────┘    └──────────────┘    └────────────┘
```

## 快速开始

### 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 然后编辑 .env，填入：
# - OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
#   (兼容 OpenAI / DeepSeek / 通义千问 / 自建网关)
# - LARK_WEBHOOK_URL  (在群里添加自定义机器人后获取)
# - LARK_SECRET       (可选，启用加签时填)
# - APP_CONTEXT       (你的 App 业务上下文，让 AI 评估更精准)
```

### 3. 试跑一次

```bash
# 不真的推 Lark，只在控制台预览卡片 JSON
python main.py --dry-run --no-dedupe

# 只跑 iOS / 只跑 Android
python main.py --platform ios --dry-run

# 正式跑
python main.py
```

### 4. 定时运行

**方式 A：本地 cron（每天早上 9:00 跑一次）**

```bash
crontab -e
# 添加这一行（注意把路径替换为你本地的）
0 9 * * * cd /Users/you/Documents/mobile_uptdate_report && /usr/bin/env -S bash -lc '.venv/bin/python main.py >> run.log 2>&1'
```

**方式 B：GitHub Actions（已提供模板）**

编辑 `.github/workflows/daily.yml`（默认 UTC 00:30 = 北京 08:30），并在仓库 Settings → Secrets 添加：

- `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`
- `LARK_WEBHOOK_URL`、`LARK_SECRET`
- 在 Variables 添加 `APP_CONTEXT`

## 文件结构

```
mobile_uptdate_report/
├── main.py             # 入口，串联抓取/去重/评估/推送
├── config.py           # 环境变量加载
├── sources.py          # iOS / Android 数据源 RSS 抓取
├── analyzer.py         # 调 LLM 做结构化评估
├── lark_notifier.py    # 飞书机器人推送（含加签 + 卡片消息）
├── storage.py          # 去重存储（data/seen.json）
├── prompts/
│   └── system.md       # AI 评估的 System Prompt（可按需调整）
├── data/seen.json      # 已推送过的指纹（自动生成）
├── .env.example        # 配置模板
└── .github/workflows/daily.yml  # 定时任务模板
```

## 输出消息模板（示例）

> 🍎 **iOS 每日更新评估 · 2025-08-12**
>
> **📢 一句话摘要**
> iOS 26 Beta 4 引入 Scene-Based Lifecycle 强制迁移，2026 年起新提交必须支持。
>
> **📅 版本时间线**
> • `2025-08-12` **iOS 26 Beta 4** — 包含 Scene Lifecycle、App Intents 增强
> • `2025-09-中旬` **iOS 26 GA** — 预计正式发布
>
> ---
> **1. 🚨 [Migrating to Scene-Based Lifecycle](https://...)**
> **P0 紧急** · 分类：硬性合规 · 截止：2026-04-30
> **影响**：我们 App 的 `AppDelegate` 仍承担窗口生命周期，迁移涉及登录态恢复、推送跳转、深链路由
> **建议**：iOS 团队在 v6.2 完成迁移，QA 同步覆盖冷启动 / 后台恢复 / 推送跳转矩阵
>
> **2. ✨ [App Intents：新增交易快捷指令](https://...)**
> **P1 重要** · 分类：ASO/体验机会 · 截止：无
> **影响**：可暴露「查看持仓 / 一键下单」到 Spotlight、Siri、Shortcuts，提升 ASO 与日活
> **建议**：PM + iOS 评估 3 条核心指令，1 个迭代内 POC

## 常见问题

**Q: 抓不到内容 / RSS 偶发 5xx？**
A: 已内置 3 次指数退避重试，单源失败不会阻塞其他源。

**Q: AI 评估会不会乱编？**
A: System Prompt 强制 JSON 输出 + 要求"未给出的事实不要编造"。如果调用失败会自动降级为透传原始 RSS 条目（不会漏推）。

**Q: 第一次跑会不会刷屏？**
A: `LOOKBACK_DAYS` 默认是 7，首次运行可改成 `1` 或加 `--no-dedupe` 控制；之后由 `data/seen.json` 自动去重。

**Q: 想加新数据源？**
A: 在 `sources.py` 的 `IOS_FEEDS` / `ANDROID_FEEDS` 列表里追加 `(名称, RSS URL)` 即可，无需改其他代码。
