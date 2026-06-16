# 智采-Agent 项目文件说明

> 企业采购智能助手 · 基于 Streamlit + DeepSeek LLM 构建

---

## 📁 项目根目录

| 文件/目录 | 说明 |
|-----------|------|
| `app.py` | **主入口文件**。Streamlit Web 前端，负责页面布局、用户交互、多阶段工作流编排。包含侧边栏（API 配置、供应商库概览）、阶段指示器、聊天输入输出区域、PDF 下载按钮。 |
| `config.py` | **配置中心**。定义所有全局参数：DeepSeek API 配置、Bocha/Tavily 搜索 API Key、数据库路径、供应商匹配阈值、PDF 输出目录、采购需求 JSON Schema、异常输入黑名单关键词。通过 `python-dotenv` 自动加载 `.env` 文件。 |
| `run.bat` | **Windows 一键启动脚本**。双击即可启动 Streamlit 服务，自动从 `.env` 加载 API Key，浏览器自动打开。 |
| `requirements.txt` | **Python 依赖清单**。核心依赖：Streamlit（前端）、OpenAI SDK（调用 DeepSeek）、fpdf2（PDF 生成）、Tavily（备用搜索）、pytest（测试）。 |
| `setup_shortcut.ps1` | **PowerShell 快捷方式创建脚本**。为项目创建桌面快捷方式（仍引用 Anthropic API Key，待修复）。 |
| `create_shortcut.ps1` | **快捷方式创建辅助脚本**。 |
| `.env` | **环境变量配置**（不提交 Git）。包含 `DEEPSEEK_API_KEY`、`BOCHA_API_KEY` 等敏感信息。 |
| `.env.example` | **环境变量模板**。供新开发者参考，不含真实 Key。 |

---

## 📁 `workflows/` — 核心工作流模块

三个工作流对应采购助手的完整处理链路：**需求结构化 → 供应商匹配 → 报告生成**。

| 文件 | 说明 |
|-----------|------|
| `demand_structuring.py` | **工作流 1：采购需求结构化**。调用 DeepSeek LLM 将用户输入的非结构化文字（如"帮我买5000米6平方线缆"）转换为标准 JSON 格式采购清单。提取字段包括：产品名称、规格、数量、预算、交货地点、交期、质量要求等。包含缺失字段追问逻辑（confidence < 0.6 时提示用户补充）。**Prompt 来源**：`src/prompts/demand_structuring_system.md` |
| `supplier_screening.py` | **工作流 2：供应商智能匹配与推荐**。基于结构化需求，先查内部供应商库，计算 5 维匹配度评分（产品匹配 30% + 价格 25% + 交付 20% + 质量 15% + 信用 10%）；若库内匹配不足（< 3 家或均分 < 60），自动触发联网搜索补充外部供应商。最终合并去重、排序输出 Top3 推荐，并包含专业采购建议。 |
| `pdf_generator.py` | **工作流 3：PDF 报告生成**。将供应商推荐结果导出为可下载报告。支持 3 种生成方式降级：fpdf2 → reportlab → HTML。报告包含标题、生成时间、采购需求确认表、供应商推荐详情、评分明细、综合对比表格、免责声明。**已知问题**：中文字体路径硬编码 Windows 字体（`C:/Windows/Fonts/`），Linux/Docker 部署需调整。 |
| `__init__.py` | Python 包标识文件（空）。 |

---

## 📁 `src/prompts/` — AI 提示词管理

**所有 AI 功能相关的系统提示词和用户提示词模板统一存放于此。禁止在业务代码中硬编码长提示词。**

| 文件 | 说明 |
|-----------|------|
| `__init__.py` | **Prompt 加载工具**。提供 `load_prompt(filename, **kwargs)` 函数，从 `.md` 文件读取提示词模板，支持 `{key}` 占位符动态替换。 |
| `demand_structuring_system.md` | **需求结构化系统提示词**。LLM 的角色定义、字段提取规则、3 组 Few-shot 示例（线缆/轴承/A4纸）、输出格式要求（仅 JSON）。被 `workflows/demand_structuring.py` 加载使用。 |
| `web_search_product_extraction.md` | **商品信息提取提示词模板**。引导 LLM 从搜索结果中提取商品详情页信息（平台、商品名、URL、价格）。使用 `{context}` 和 `{results}` 占位符动态插入采购上下文和搜索结果。被 `utils/web_search.py` 加载使用。 |
| `agent_system_reference.md` | **Coze Agent 完整规范（参考文档）**。包含完整的 6 步执行流程、Few-shot 示例、回复规则。这是原始 Coze 版 Agent 的系统提示词，作为架构参考保留，不在当前代码中直接使用。 |

---

## 📁 `db/` — 数据库模块

SQLite 数据库，存储供应商库和采购历史。

| 文件 | 说明 |
|-----------|------|
| `schema.sql` | **数据库表结构定义**。3 张表：`suppliers`（供应商主表，18 个字段包括公司名、主营产品、报价范围、质量/信用/服务评分、资质等）、`procurement_history`（采购历史记录）、`supplier_ratings`（供应商评分记录）。含索引优化。 |
| `database.py` | **数据库操作模块**。提供 `init_database()`（建表 + 插入种子数据）、`search_suppliers()`（多条件搜索：关键词/分类/质量/信用/价格）、`get_all_suppliers()`（获取活跃供应商）、`save_procurement_history()`（保存采购历史）。使用 WAL 模式 + 外键约束。 |
| `seed_data.py` | **种子数据**。12 家虚构供应商，覆盖线缆、办公用品、轴承、电子元器件、MRO 等品类。每家企业包含完整的评分、报价、交期、资质等信息。**所有数据均为虚构，仅用于演示**。 |
| `__init__.py` | Python 包标识文件（空）。 |

---

## 📁 `utils/` — 工具模块

| 文件 | 说明 |
|-----------|------|
| `validators.py` | **输入校验与风控模块**。包含：`check_blacklist()`（黑名单检测，过滤涉密/攻击/闲聊）、`classify_input()`（输入类型识别：采购需求/确认指令/闲聊/涉密/模糊不清）、`validate_demand()`（结构化需求字段完整性校验）、`validate_supplier_result()`（供应商数据校验）。 |
| `web_search.py` | **外部联网搜索模块**。优先使用 Bocha API → 降级 Tavily → 兜底 Mock。核心能力：搜索词自动追加电商属性关键词（批发/厂家/1688）；URL 白名单过滤（仅保留商品详情页，拒绝首页/列表页）；LLM 结构化提取商品信息（平台/商品名/URL/价格区间）；结果校验（URL 真实性、非编造检测）。**Prompt 来源**：`src/prompts/web_search_product_extraction.md` |
| `__init__.py` | Python 包标识文件（空）。 |

---

## 📁 `tests/` — 测试模块

| 文件 | 说明 |
|-----------|------|
| `test_eval.py` | **评测测试套件（30 个用例）**。覆盖 4 大核心能力、5 个难度层级：输入异常分类（L5-001~004）、异常输入拦截、标准需求提取（L1-001~003）、缺失字段检测（L3-001~002）、专业需求提取（L4-001~002）、需求校验、供应商筛选、数据库操作、评分算法、端到端集成测试。运行方式：`pytest tests/test_eval.py -v`。**全部 30 个用例已通过**。 |

---

## 📁 `data/` — 运行时数据

| 文件 | 说明 |
|-----------|------|
| `suppliers.db` | **SQLite 数据库文件**。运行时自动创建，包含供应商表和采购历史。首次启动时由 `init_database()` 自动初始化并插入种子数据。 |
| `reports/` | **PDF 报告输出目录**。生成的供应商推荐 PDF/HTML 报告存放于此，文件名格式：`供应商推荐报告_{品类}_{时间戳}.pdf`。 |

---

## 📁 `src/` — 核心源代码

| 文件 | 说明 |
|-----------|------|
| `__init__.py` | Python 包标识文件。 |
| `prompts/` | **AI 提示词管理目录**（详见上方 `src/prompts/` 说明）。 |

---

## 🔄 数据流概览

```
用户输入 (app.py)
    │
    ├─→ classify_input()  ─── 闲聊/涉密 → 拒识
    │
    ├─→ check_blacklist() ─── 命中黑名单 → 拦截
    │
    ▼
demand_structuring.py  ←──── load_prompt("demand_structuring_system.md")
    │  LLM 提取 JSON
    ▼
supplier_screening.py
    │  ├─ 内部库搜索 (database.py)
    │  ├─ 5 维评分 (product/price/delivery/quality/credit)
    │  └─ 联网补充 (web_search.py)  ←── load_prompt("web_search_product_extraction.md")
    │       ├─ Bocha API (优先)
    │       ├─ Tavily API (备用)
    │       └─ Mock (兜底)
    ▼
pdf_generator.py
    │  ├─ fpdf2 (优先)
    │  ├─ reportlab (备用)
    │  └─ HTML (降级)
    ▼
PDF 报告下载
```

---

## ⚠️ 已知待修复问题

| # | 问题 | 相关文件 |
|---|------|----------|
| 1 | `.env` 中 API Key 硬编码有泄露风险 | `.env` |
| 2 | PDF 中文依赖 Windows 字体路径 | `workflows/pdf_generator.py:61-66` |
| 3 | LLM 调用无重试机制 | `workflows/demand_structuring.py` |
| 4 | 联网搜索默认走 Mock 数据 | `utils/web_search.py` |
| 5 | `setup_shortcut.ps1` 仍引用 Anthropic API Key | `setup_shortcut.ps1:41` |

---

*文档生成时间：2026-06-13 | 智采-Agent v1.0*
