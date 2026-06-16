"""
智采-Agent (Smart Procurement Agent) 配置文件
基于 Coze 版 Agent 完整复刻
"""

import os
from pathlib import Path

# ─── 自动加载 .env 文件 ───
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv 未安装时忽略

# ─── 项目根目录 ───
ROOT_DIR = Path(__file__).parent.absolute()

# ─── LLM API 配置 ───
# 使用 DeepSeek API（OpenAI 兼容接口）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_MAX_TOKENS = 4096
DEEPSEEK_TEMPERATURE = 0.3  # 低温度保证输出稳定

# ─── 外部搜索 API 配置 ───
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")     # 博查 Web Search API（优先）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")   # Tavily 备用
SEARCH_MAX_RESULTS = 5  # 每次搜索返回结果数

# ─── MCP 服务器配置 ───
# 优先级: MCP > API Key > Mock（MCP 可用时自动跳过 API Key 调用）
MCP_BOCHA_ENABLED = os.getenv("MCP_BOCHA_ENABLED", "true").lower() == "true"
MCP_PRICEPEEK_ENABLED = os.getenv("MCP_PRICEPEEK_ENABLED", "true").lower() == "true"
# PricePeek MCP 入口脚本路径
MCP_PRICEPEEK_ENTRY = os.getenv(
    "MCP_PRICEPEEK_ENTRY",
    "D:/Node.js/node_global/node_modules/mcp-pricepeek/dist/index.js"
)

# ─── 数据库配置 ───
DATABASE_PATH = ROOT_DIR / "data" / "suppliers.db"

# ─── 供应商匹配阈值 ───
MATCH_MIN_COUNT = 3           # 最少匹配数量
MATCH_MIN_SCORE = 60          # 平均匹配度阈值（百分制）
MATCH_LOW_THRESHOLD = 50      # 低匹配度阈值，低于此值触发联网搜索

# ─── PDF 输出配置 ───
PDF_OUTPUT_DIR = ROOT_DIR / "data" / "reports"

# ─── 采购需求单 JSON Schema ───
# 需求结构化工作流的输出格式（v2.0 专业采购需求单）
DEMAND_SCHEMA = {
    "type": "object",
    "properties": {
        "采购需求单编号": {"type": "string", "description": "格式 PR-YYYYMMDD-XXX"},
        "所选择的供应商": {"type": "string", "description": "供应商名称，初始为空"},
        "期望送达日期": {"type": "string", "description": "YYYY-MM-DD 格式"},
        "整体特殊要求": {"type": "array", "description": "适用于所有采购物品的要求"},
        "采购物品清单": {
            "type": "array",
            "description": "采购物品列表",
            "items": {
                "type": "object",
                "properties": {
                    "序号": {"type": "number"},
                    "品类": {"type": "string"},
                    "数量": {"type": "number"},
                    "单位": {"type": "string"},
                    "规格": {"type": "string"},
                    "预算": {"type": "string"},
                    "物品特殊要求": {"type": "array"},
                },
            },
        },
    },
    "required": ["采购需求单编号", "采购物品清单"]
}

# ─── 供应商推荐 Schema ───
SUPPLIER_SCHEMA = {
    "type": "object",
    "properties": {
        "supplierName": {"type": "string", "description": "供应商名称"},
        "mainProducts": {"type": "string", "description": "主营产品"},
        "priceRange": {"type": "string", "description": "报价范围"},
        "deliveryCycle": {"type": "string", "description": "交付周期"},
        "matchScore": {"type": "number", "description": "匹配度评分 0-100"},
        "scoreBreakdown": {
            "type": "object",
            "description": "评分明细",
            "properties": {
                "price": {"type": "number"},
                "delivery": {"type": "number"},
                "quality": {"type": "number"},
                "credit": {"type": "number"}
            }
        },
        "source": {"type": "string", "enum": ["internal", "web_search"], "description": "数据来源"},
        "contact": {"type": "string", "description": "联系方式（待核实）"},
        "notes": {"type": "string", "description": "备注/补充说明"}
    }
}

# ─── 异常输入拒识关键词 ───
BLACKLIST_PATTERNS = [
    r"(如何|怎么|教程).*攻击",
    r"(武器|毒品|赌博)",
    r"(公司.*机密|内部.*底价|供应商.*底价|竞争对手.*供应商|供应商.*联系方式.*窃取)",
    r"(帮我|我要).*删.*(数据库|供应商|系统)",
    r"(你是谁|你好|今天天气|讲个笑话|聊天)",  # 纯闲聊
]
