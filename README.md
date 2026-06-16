# 智采 Agent — AI 采购专家智能体

基于 LLM 大语言模型与 RAG 检索增强生成技术，针对企业级采购场景打造，实现从非结构化需求识别、智能解析到精准供应商推荐的全链路自动化闭环。

## 核心能力

| 阶段 | 功能 | 说明 |
|------|------|------|
| 01 需求结构化 | 非结构化输入 → 标准采购单 JSON | 微信/邮件/语音等非标输入，LLM 精准提取品类、规格、数量、预算等字段 |
| 02 供应商匹配 | 内部库 + 联网搜索双源匹配 | 优先检索内部供应商库，不足时自动联网（1688/企业官网）补充 |
| 03 PDF 报告 | 一键导出推荐报告 | Top3 供应商对比表 + 采购建议 + 风险提示，支持 PDF 下载 |

## 技术栈

- **前端**：Streamlit
- **LLM**：DeepSeek API（OpenAI 兼容）
- **搜索**：Bocha Web Search / Tavily
- **数据库**：SQLite（内部供应商库 250+）
- **集成**：Coze 平台 / 飞书机器人
- **Prompt 工程**：Few-shot + CoT 思维链，字段提取准确率 92%

## 项目结构

```
├── index.html              # 集成入口页
├── app/                    # Streamlit 应用
│   ├── app.py              # 主入口
│   ├── config.py           # 配置
│   ├── workflows/          # 工作流（需求结构化/供应商筛选/PDF生成）
│   ├── utils/              # 工具（搜索/评分/校验/MCP客户端）
│   ├── db/                 # 数据库
│   └── src/prompts/        # LLM Prompt 模板
├── coze/                   # Coze AI 对话集成
├── docs/                   # 产品文档（PRD / 评测集 / 路演资料）
└── assets/                 # 演示文件
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r app/requirements.txt

# 2. 配置 API Key
cp app/.env.example app/.env
# 编辑 app/.env 填入 DeepSeek 和 Bocha API Key

# 3. 启动
cd app
streamlit run app.py --server.port 8501
```

## 产品边界

- ✅ 非结构化需求解析 / 供应商智能匹配 / PDF 报告生成
- ❌ 不做最终采购决策 / 不直接操作业务系统 / 所有结果需人工复核

---

> 🤖 AI 生成 · 仅供参考 · 请以人工核对为准
