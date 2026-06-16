"""
外部联网搜索模块
用于在内部供应商库匹配不足时，从互联网搜索补充供应商信息

搜索优先级：Bocha（博查）→ Tavily → Mock 模拟数据

搜索策略：
  - 关键词必须包含电商属性词（批发/厂家/1688/拼多多等）
  - 仅返回商品详情页，过滤首页/列表页
  - LLM 提取严格按 platform/product_name/url/price_range 结构输出
  - 无匹配结果时返回空数组，禁止编造
"""

import json
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from openai import OpenAI

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    BOCHA_API_KEY,
    TAVILY_API_KEY,
    SEARCH_MAX_RESULTS,
    MCP_BOCHA_ENABLED,
    MCP_PRICEPEEK_ENABLED,
)

# MCP 客户端（惰性导入，按需加载）
_mcp_bocha = None


def _get_bocha_mcp():
    """获取 Bocha MCP 客户端单例（惰性初始化）"""
    global _mcp_bocha
    if _mcp_bocha is None and MCP_BOCHA_ENABLED:
        from utils.mcp_client import BochaSearchMCP
        _mcp_bocha = BochaSearchMCP()
        if not _mcp_bocha.start():
            print("[搜索] Bocha MCP 启动失败，将降级使用 API")
            _mcp_bocha = None
    return _mcp_bocha


# PricePeek MCP 已移至 utils/pricepeek_enrich.py

# prompt 模板：src/prompts/web_search_product_extraction.md
from src.prompts import load_prompt


# ─── 常量 ───

BOCHA_SEARCH_URL = "https://api.bochaai.com/v1/web-search"

# 电商平台域名（用于 include 过滤 + URL 校验）
ECOMMERCE_DOMAINS = [
    "1688.com", "alibaba.com", "taobao.com", "tmall.com",
    "made-in-china.com", "globalsources.com",
    "pinduoduo.com", "yangkeduo.com",
    "jd.com", "china.cn", "b2b.baidu.com",
    "hc360.com", "zgw.com", "qjy.com", "gys.cn",
]

# 电商属性关键词（自动追加到搜索词）
ECOMMERCE_KEYWORDS = ["批发", "厂家直销", "1688", "拼多多"]

# ─── 商品详情页 URL 正则（白名单） ───

PRODUCT_DETAIL_PATTERNS = [
    # 1688（官方详情页 + 店铺子域名商品页 + 供应商产品页 + 火拼商品页）
    re.compile(r"detail\.1688\.com/offer/"),
    re.compile(r"\.1688\.com/(shop/)?(m/)?offer/"),
    re.compile(r"\.1688\.com/huo/detail-"),
    # 淘宝 / 天猫
    re.compile(r"item\.taobao\.com/item\.htm"),
    re.compile(r"detail\.tmall\.com/item\.htm"),
    # 京东
    re.compile(r"item\.jd\.com/"),
    # 拼多多
    re.compile(r"(mobile\.)?yangkeduo\.com/goods"),
    # 阿里巴巴国际站
    re.compile(r"alibaba\.com/product-detail/"),
    # 中国制造网
    re.compile(r"made-in-china\.com/.*products?/"),
    # 慧聪网
    re.compile(r"hc360\.com/.*(product|supply)"),
    # 百度爱采购
    re.compile(r"b2b\.baidu\.com/land\?id="),
    # 全球资源
    re.compile(r"globalsources\.com/.*/product"),
    # 中国供应商
    re.compile(r"china\.cn/.*(product|supply|detail)"),
    # 企业购
    re.compile(r"gys\.cn/"),
    # 志高网
    re.compile(r"zgw\.com/.*detail"),
    # 黄页88 / 勤加缘 / 马可波罗等 B2B 站点
    re.compile(r"huangye88\.com/.*(xinxi|product|supply)"),
    re.compile(r"qjy\.com/.*(product|supply)"),
    re.compile(r"makepolo\.com/.*(product|detail|cpk)"),
    # 1688 火拼/活动商品页
    re.compile(r"\.1688\.com/huo/detail-"),
    # 苏宁 / 国美
    re.compile(r"suning\.com/item/"),
    re.compile(r"gome\.com\.cn/product/"),
    # 更多 B2B 平台
    re.compile(r"china\.cn/.*(product|supply|detail)"),
    re.compile(r"b2b168\.com/.*(detail|product)"),
    re.compile(r"tfsb\.cn/"),
    re.compile(r"21seal\.com/"),
    re.compile(r"zol\.com/detail/"),
    # 通用：任何B2B域名的商品页
    re.compile(r"\.(b2b|made-in-china|globalsources|hc360)\.\w+/.*(product|detail|offer|supply)"),
]


# ─── 公共入口 ───

def search_suppliers_online(demand: dict, query: str = None) -> list[dict]:
    """
    联网搜索外部供应商（多轮下放）

    第1轮: {品类} 1688 → Bocha
    第2轮: {品类} 供应商 → Bocha（放宽）
    第3轮: LLM 兜底生成 → 基于品类知识推荐参考供应商

    Args:
        demand: v2.0 采购需求单 dict（必填）
        query: 可选的手动搜索词覆盖

    Returns:
        结构化供应商列表（兼容 supplier_screening 格式）
    """
    if demand is None:
        print("[搜索] 错误：缺少采购需求单 JSON")
        return []

    # 从需求单 JSON 自动构建搜索词（仅品类核心词）
    if query is None:
        query = _build_query_from_demand(demand)

    if not query:
        print("[搜索] 无法构建搜索词")
        return []

    # ── 多轮搜索（逐级下放，MCP 优先） ──

    def _try_search(q: str, round_label: str) -> list[dict]:
        """尝试一轮搜索，优先 MCP → API → 无"""
        # 优先级 1: Bocha MCP（无需 API Key，自动可用）
        if MCP_BOCHA_ENABLED:
            print(f"[搜索] 第{round_label}轮 Bocha MCP: {q}")
            results = _search_with_bocha_mcp(q, demand)
            if results:
                return results
            print(f"[搜索] 第{round_label}轮 MCP 无结果，尝试 API")

        # 优先级 2: Bocha HTTP API
        if BOCHA_API_KEY:
            print(f"[搜索] 第{round_label}轮 Bocha API: {q}")
            results = _search_with_bocha(q, demand)
            if results:
                return results
            print(f"[搜索] 第{round_label}轮 API 无结果，下放")

        # 优先级 3: Tavily
        elif TAVILY_API_KEY and TAVILY_API_KEY != "your-tavily-api-key":
            print(f"[搜索] 第{round_label}轮 Tavily: {q}")
            results = _search_with_tavily(q, demand)
            if results:
                return results
            print(f"[搜索] 第{round_label}轮无结果，下放")
        return []

    # 第1轮: 品类 + 1688
    results = _try_search(f"{query} 1688", "1")
    if results:
        return results

    # 第2轮: 品类 + 供应商（更通用）
    results = _try_search(f"{query} 供应商", "2")
    if results:
        return results

    # 第3轮: LLM 兜底
    print(f"[搜索] 第3轮 LLM兜底生成: {query}")
    return _llm_fallback_suppliers(query, demand)


# ─── 从需求单 JSON 自动构建搜索词 ───

def _build_query_from_demand(demand: dict) -> str:
    """
    从 v2.0 采购需求单 JSON 中提取搜索关键词

    策略：品类核心词 + 关键规格参数（短数字+单位组合），不含电商词/地点/预算。
    """
    parts = []

    # v2.0 格式
    if "采购物品清单" in demand:
        items = demand.get("采购物品清单", [])
        if items and isinstance(items, list):
            item = items[0] if items else {}
            category = (item.get("品类") or "").strip()
            spec = (item.get("规格") or "").strip()
            if category:
                parts.append(category)
            # 规格中提取关键参数（如 220V、2500W），跳过中文描述
            if spec:
                params = re.findall(r'\d{2,6}\s*(?:W|V|A|mm|cm|m|kg|g|L|ml|Hz|℃|度)', spec, re.IGNORECASE)
                if params:
                    # 去重取前2个
                    seen = set()
                    unique = []
                    for p in params:
                        normalized = p.upper().replace(' ', '')
                        if normalized not in seen:
                            seen.add(normalized)
                            unique.append(p)
                    parts.extend(unique[:2])

    # 兼容旧格式
    if not parts:
        category = demand.get("productCategory", "").strip()
        if category:
            parts.append(category)

    return " ".join(parts) if parts else ""


# ─── 关键词增强 ───

def _enrich_query(query: str) -> str:
    """如果搜索词缺少电商属性关键词，自动追加"""
    if not query:
        return query

    # 已包含任一电商关键词则跳过
    ecommerce_indicators = [
        "批发", "厂家", "1688", "拼多多", "淘宝", "天猫",
        "京东", "alibaba", "taobao", "jd.com", "pdd",
        "直销", "一件代发", "货源", "拿货",
    ]
    q_lower = query.lower()
    has_ecom = any(indicator.lower() in q_lower for indicator in ecommerce_indicators)

    if not has_ecom:
        # 追加电商关键词：取前两个
        suffix = " ".join(ECOMMERCE_KEYWORDS[:2])
        query = f"{query} {suffix}"

    return query


# ─── Bocha MCP 搜索 ───

def _search_with_bocha_mcp(query: str, demand: dict = None) -> list[dict]:
    """
    通过 Bocha MCP 服务器搜索电商商品页

    与 _search_with_bocha() 功能相同，但走 MCP 协议而非直接 HTTP 调用。
    MCP 的优势：
      - 复用 Claude Code 已验证的连接
      - 无需单独管理 API Key
      - 搜索结果格式统一
    """
    mcp = _get_bocha_mcp()
    if mcp is None:
        return []

    print(f"[搜索] Bocha MCP: {query}")
    raw_results = mcp.search(query, count=max(SEARCH_MAX_RESULTS, 10))

    if not raw_results:
        print("[搜索] Bocha MCP 无结果")
        return []

    print(f"[搜索] Bocha MCP 返回 {len(raw_results)} 条")

    # 转换为内部格式（兼容现有的 URL 过滤 + LLM 提取流水线）
    web_pages = []
    for r in raw_results:
        web_pages.append({
            "name": r.get("title", ""),
            "url": r.get("url", ""),
            "summary": r.get("summary", ""),
            "snippet": r.get("summary", ""),
            "siteName": r.get("site_name", ""),
        })

    # ── URL 过滤：必须是商品详情页 ──
    product_pages = []
    non_product_pages = []
    for page in web_pages:
        url = page.get("url", "")
        if _is_product_detail_url(url):
            product_pages.append(page)
        else:
            non_product_pages.append({
                "name": page.get("name", ""),
                "url": url,
            })

    if non_product_pages:
        print(f"[搜索] 已过滤 {len(non_product_pages)} 条非商品详情页")

    if not product_pages:
        print("[搜索] 无商品详情页结果")
        return []

    print(f"[搜索] 保留 {len(product_pages)} 条商品详情页，LLM 提取中...")

    # ── LLM 结构化提取 ──
    search_results = []
    for page in product_pages:
        search_results.append({
            "title": page.get("name", ""),
            "content": page.get("summary", page.get("snippet", "")),
            "url": page.get("url", ""),
            "site_name": page.get("siteName", ""),
        })

    raw_items = _extract_product_items(search_results, demand)
    valid_items = _validate_extracted_items(raw_items, search_results)
    if len(valid_items) < len(raw_items):
        print(f"[搜索] LLM 校验: {len(raw_items)} -> {len(valid_items)} (丢弃 {len(raw_items) - len(valid_items)} 条)")

    suppliers = _convert_to_supplier_format(valid_items, demand)

    # ── 🔗 PricePeek 电商比价增强（委托给独立模块）──
    if MCP_PRICEPEEK_ENABLED and suppliers:
        from utils.pricepeek_enrich import enrich_with_pricepeek
        suppliers = enrich_with_pricepeek(suppliers, demand)

    return suppliers

# PricePeek 价格增强已移至 utils/pricepeek_enrich.py
# web_search.py 内部通过延迟 import 调用: from utils.pricepeek_enrich import enrich_with_pricepeek

# ─── Bocha 搜索实现（HTTP API，MCP 不可用时降级使用） ───

def _search_with_bocha(query: str, demand: dict = None) -> list[dict]:
    """使用博查 Web Search API 搜索电商商品页"""
    try:
        headers = {
            "Authorization": f"Bearer {BOCHA_API_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "query": query,
            "count": max(SEARCH_MAX_RESULTS, 10),  # 多取一些，后续会过滤
            "freshness": "noLimit",
            "summary": True,
        }

        resp = requests.post(
            BOCHA_SEARCH_URL,
            headers=headers,
            json=payload,
            timeout=15,
        )

        if resp.status_code != 200:
            print(f"[搜索] Bocha API 返回 {resp.status_code}: {resp.text[:200]}")
            if TAVILY_API_KEY and TAVILY_API_KEY != "your-tavily-api-key":
                return _search_with_tavily(query, demand)
            return _search_with_mock(query, demand)

        data = resp.json()
        web_pages = data.get("data", {}).get("webPages", {}).get("value", [])

        if not web_pages:
            print("[搜索] Bocha 未返回结果")
            return _search_with_mock(query, demand)

        print(f"[搜索] Bocha 原始结果 {len(web_pages)} 条")

        # ── 第1轮过滤：URL 必须是商品详情页 ──
        product_pages = []
        non_product_pages = []
        for page in web_pages:
            url = page.get("url", "")
            if _is_product_detail_url(url):
                product_pages.append(page)
            else:
                non_product_pages.append({
                    "name": page.get("name", ""),
                    "url": url,
                })

        if non_product_pages:
            print(f"[搜索] 已过滤 {len(non_product_pages)} 条非商品详情页:")
            for p in non_product_pages[:3]:
                print(f"  [X] {p['name'][:50]} | {p['url'][:80]}")

        if not product_pages:
            print("[搜索] 无商品详情页结果，返回空")
            return []

        print(f"[搜索] 保留 {len(product_pages)} 条商品详情页，开始 LLM 提取...")

        # ── 第2轮：LLM 结构化提取 ──
        search_results = []
        for page in product_pages:
            search_results.append({
                "title": page.get("name", ""),
                "content": page.get("summary", page.get("snippet", "")),
                "url": page.get("url", ""),
                "site_name": page.get("siteName", ""),
            })

        raw_items = _extract_product_items(search_results, demand)
        valid_items = _validate_extracted_items(raw_items, search_results)
        if len(valid_items) < len(raw_items):
            print(f"[搜索] LLM 校验: {len(raw_items)} -> {len(valid_items)} (丢弃 {len(raw_items) - len(valid_items)} 条)")
        return _convert_to_supplier_format(valid_items, demand)

    except requests.exceptions.Timeout:
        print("[搜索] Bocha 请求超时，降级")
        if TAVILY_API_KEY and TAVILY_API_KEY != "your-tavily-api-key":
            return _search_with_tavily(query, demand)
        return _search_with_mock(query, demand)
    except requests.exceptions.ConnectionError:
        print("[搜索] Bocha 连接失败，降级")
        if TAVILY_API_KEY and TAVILY_API_KEY != "your-tavily-api-key":
            return _search_with_tavily(query, demand)
        return _search_with_mock(query, demand)
    except Exception as e:
        print(f"[搜索] Bocha 搜索异常: {e}，降级")
        if TAVILY_API_KEY and TAVILY_API_KEY != "your-tavily-api-key":
            return _search_with_tavily(query, demand)
        return _search_with_mock(query, demand)


# ─── Tavily 搜索（备用） ───

def _search_with_tavily(query: str, demand: dict = None) -> list[dict]:
    """使用 Tavily API 搜索供应商"""
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)

        response = client.search(
            query=query,
            search_depth="advanced",
            max_results=SEARCH_MAX_RESULTS,
            include_domains=ECOMMERCE_DOMAINS,
        )

        raw_results = response.get("results", [])
        search_results = [
            {"title": r.get("title", ""), "content": r.get("content", ""), "url": r.get("url", ""), "site_name": ""}
            for r in raw_results
        ]

        raw_items = _extract_product_items(search_results, demand)
        valid_items = _validate_extracted_items(raw_items, search_results)
        return _convert_to_supplier_format(valid_items, demand)

    except ImportError:
        print("[搜索] tavily-python 未安装，降级使用模拟搜索")
        return _search_with_mock(query, demand)
    except Exception as e:
        print(f"[搜索] Tavily 搜索出错: {e}，降级使用模拟搜索")
        return _search_with_mock(query, demand)


# ─── URL 校验：商品详情页 vs 首页/列表页 ───

def _is_product_detail_url(url: str) -> bool:
    """
    三阶段 URL 过滤：黑名单 → 白名单 → 通用商品页特征

    拒绝：首页 / 搜索列表页 / 店铺首页 / 资讯页 / 文档页
    接受：已知电商产品页 + 含通用商品路径特征的页面
    """
    if not url:
        return False

    url_lower = url.lower()

    # ── 第一阶段：黑名单（非商品页） ──
    _blacklist = [
        re.compile(r"^https?://(www\.)?[^/]+/?$"),            # 纯域名首页
        re.compile(r"(selloffer|s\.html|search|list|chanpin|changjia|cp/)"),  # 搜索/列表/频道页
        re.compile(r"(zhidao|wenwen|zhihu|baike|wiki|doc88|doc\.)"),  # 问答/百科/文档
        re.compile(r"(news|article|blog|post)/?\d"),           # 新闻/博客
        re.compile(r"pconline\.com\.cn/p2/"),                  # PConline 列表
        re.compile(r"huangye88\.com/xinxi/"),                  # 黄页信息页（非商品）
    ]
    for pattern in _blacklist:
        if pattern.search(url_lower):
            return False

    # ── 第二阶段：白名单（已知电商产品页） ──
    for pattern in PRODUCT_DETAIL_PATTERNS:
        if pattern.search(url_lower):
            return True

    # ── 第三阶段：通用商品页特征 ──
    # URL 路径中包含商品相关关键词 + 有具体 ID/参数
    _generic_product = [
        re.compile(r"/(product|detail|offer|item|goods|supply)/?\d"),
        re.compile(r"/(product|detail|offer|item|goods|supply)\.(html?|jsp|php|asp)"),
        re.compile(r"/(product|detail|offer|item|goods)/[a-zA-Z0-9_-]{6,}"),
    ]
    for pattern in _generic_product:
        if pattern.search(url_lower):
            return True

    return False


# ─── LLM 结构化提取（严格电商格式） ───

def _extract_product_items(search_results: list, demand: dict = None) -> list[dict]:
    """
    使用 LLM 从搜索结果中提取商品信息

    输出严格 JSON 数组，每个元素:
      - platform: 电商平台名称
      - product_name: 商品标题
      - url: 商品详情页链接
      - price_range: 价格区间（如有）
    """
    if not search_results:
        return []

    # 格式化搜索结果
    results_text = ""
    for i, r in enumerate(search_results, 1):
        title = r.get("title", "")
        content = r.get("content", r.get("snippet", ""))
        url = r.get("url", "")
        site = r.get("site_name", "")
        results_text += f"[{i}] 标题: {title}\n"
        if site:
            results_text += f"    来源: {site}\n"
        results_text += f"    摘要: {content}\n"
        results_text += f"    链接: {url}\n\n"

    demand_context = ""
    if demand:
        # 兼容 v2.0 采购需求单 + 旧格式
        if "采购物品清单" in demand:
            from utils.scoring import extract_first_item
            item = extract_first_item(demand)
            demand_context = f"""
采购需求上下文：
- 产品: {item.get('category', '-')}
- 规格: {item.get('spec', '-')}
- 数量: {item.get('qty', '-')} {item.get('unit', '')}
- 预算: {item.get('budget', '-')}
"""
        else:
            demand_context = f"""
采购需求上下文：
- 产品: {demand.get('productCategory', '-')}
- 规格: {demand.get('specification', '-')}
- 数量: {demand.get('quantity', '-')} {demand.get('unit', '')}
- 预算: {demand.get('budget', '-')} {demand.get('budgetUnit', '')}
"""

    prompt = load_prompt(
        "web_search_product_extraction.md",
        context=demand_context,
        results=results_text[:6000],
    )

    try:
        if not DEEPSEEK_API_KEY:
            return []

        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            max_tokens=2000,
            temperature=0.0,  # 零温度保证输出确定性
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.choices[0].message.content.strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        items = json.loads(content)

        if not isinstance(items, list):
            print(f"[搜索] LLM 返回非数组: {type(items)}")
            return []

        # 字段标准化
        valid_items = []
        for item in items:
            if isinstance(item, dict) and item.get("url"):
                shop = str(item.get("shop_name", item.get("shopName", ""))).strip()
                if not shop or shop in ("null", "None", "") or len(shop) < 2:
                    shop = "商家名称待核实"
                # 价格标准化：解析数字
                price_raw = item.get("price_range", item.get("priceRange"))
                try:
                    price_num = float(price_raw) if price_raw is not None else None
                except (ValueError, TypeError):
                    price_num = None
                # 好评率标准化
                rating_raw = item.get("rating")
                try:
                    rating_num = float(rating_raw) if rating_raw is not None else None
                except (ValueError, TypeError):
                    rating_num = None
                # 起售量标准化
                moq_raw = item.get("moq")
                try:
                    moq_num = int(float(moq_raw)) if moq_raw is not None else None
                except (ValueError, TypeError):
                    moq_num = None

                # 匹配原始搜索结果的摘要（按URL），供后续公司名提取使用
                _matched_summary = ""
                for _sr in search_results:
                    if _sr.get("url", "") == str(item.get("url", "")):
                        _matched_summary = _sr.get("content", _sr.get("snippet", ""))
                        break

                valid_items.append({
                    "platform": str(item.get("platform", "未知")),
                    "shop_name": shop,
                    "product_name": str(item.get("product_name", item.get("productName", ""))),
                    "url": str(item.get("url", "")),
                    "price_range": price_raw,       # 原始值保留
                    "price_num": price_num,          # 数值（用于排序/比较）
                    "delivery_from": str(item.get("delivery_from", "")).strip() if item.get("delivery_from") else None,
                    "free_shipping": bool(item.get("free_shipping")) if item.get("free_shipping") is not None else False,
                    "moq": moq_num,
                    "rating": rating_num,
                    "returns_policy": str(item.get("returns_policy", "")).strip() if item.get("returns_policy") else None,
                    "summary": _matched_summary,     # 原始摘要（供公司名兜底提取）
                })

        # ── 兜底：LLM 未提取到商家名时，正则从标题/摘要中提取 ──
        for _item in valid_items:
            if _item["shop_name"] in ("商家名称待核实", "null", "", None):
                # 先尝试从标题提取
                _extracted = _extract_company_from_title(_item.get("product_name", ""))
                if not _extracted:
                    # 再从摘要提取
                    _extracted = _extract_company_from_summary(_item.get("summary", ""))
                if _extracted:
                    _item["shop_name"] = _extracted

        print(f"[搜索] LLM 提取 {len(valid_items)} 条商品")
        return valid_items

    except json.JSONDecodeError as e:
        print(f"[搜索] LLM 返回非 JSON: {e}")
        return []
    except Exception as e:
        print(f"[搜索] LLM 提取失败: {e}")
        return []


# ─── 结果校验 ───

def _validate_extracted_items(items: list[dict], raw_results: list[dict]) -> list[dict]:
    """
    校验 LLM 提取结果：
    1. URL 必须在原始搜索结果中存在
    2. URL 必须是商品详情页
    3. product_name 不能为空或明显编造
    """
    # 收集所有原始 URL
    raw_urls = {r.get("url", "") for r in raw_results}

    valid = []
    for item in items:
        url = item.get("url", "")

        # 校验 1: URL 来源真实
        if url not in raw_urls:
            print(f"[搜索] 丢弃 -- URL 不在原始结果中: {url[:80]}")
            continue

        # 校验 2: URL 是商品详情页
        if not _is_product_detail_url(url):
            print(f"[搜索] 丢弃 -- 非商品详情页: {url[:80]}")
            continue

        # 校验 3: 有产品名且不过短
        product_name = (item.get("product_name") or "").strip()
        if len(product_name) < 3:
            print(f"[搜索] 丢弃 -- 产品名为空或过短: '{product_name}'")
            continue

        # 校验 4: 平台字段——缺失时根据URL推断，不再拒绝非标平台
        platform = item.get("platform", "")
        if not platform or platform == "其他":
            item["platform"] = _guess_platform(url) or "企业官网"

        # 校验 5: 商家名规范化
        shop_name = item.get("shop_name", "")
        if not shop_name or shop_name in ("null", "None", ""):
            item["shop_name"] = "商家名称待核实"

        # 校验 6: 发货地有效性
        delivery_from = item.get("delivery_from")
        if delivery_from and delivery_from in ("null", "None", ""):
            item["delivery_from"] = None

        # 校验 7: 价格数值有效性（≤0 视为无价格）
        if item.get("price_num") is not None and item["price_num"] <= 0:
            item["price_num"] = None
            item["price_range"] = None

        valid.append(item)

    return valid


def _guess_platform(url: str) -> str:
    """根据 URL 域名推断电商平台"""
    url_lower = url.lower()
    if "1688.com" in url_lower: return "1688"
    if "tmall.com" in url_lower: return "天猫"
    if "taobao.com" in url_lower: return "淘宝"
    if "jd.com" in url_lower: return "京东"
    if "yangkeduo.com" in url_lower or "pinduoduo.com" in url_lower: return "拼多多"
    if "alibaba.com" in url_lower: return "阿里巴巴国际站"
    if "made-in-china.com" in url_lower: return "中国制造网"
    if "hc360.com" in url_lower: return "慧聪网"
    if "b2b.baidu.com" in url_lower: return "百度爱采购"
    if "globalsources.com" in url_lower: return "全球资源"
    if "china.cn" in url_lower: return "中国供应商"
    return "其他"


# ─── 公司名提取 ───

# 公司名正则模式（用于从标题/摘要中提取真实企业名）
# 注意：所有模式都必须用非捕获组 (?:...)，match.group(0) 返回完整公司名
_COMPANY_NAME_PATTERNS = [
    # 优先：完整公司名格式（如"广州市欧朵日用品有限公司"、"佛山市南海区大沥优纯牛奶商行"）
    re.compile(r'(?:[一-鿿]{2,20}(?:市|省|县|区))?[一-鿿a-zA-Z]{2,20}(?:科技|实业|工贸|商贸|贸易|电子商务|电子|机电|机械|电器|线缆|电缆|包装|印刷|塑料|五金|建材|食品|日用品|办公|文具|玩具|服装|服饰|信息|网络|软件)(?:有限|股份|集团)?公司'),
    # 厂/商行/经营部/旗舰店/专营店/专卖店
    re.compile(r'[一-鿿a-zA-Z]{2,30}(?:厂|商行|经营部|旗舰店|专营店|专卖店|批发部|经销部|门市部)'),
    # 简单公司名（XX有限公司）- 较短模式
    re.compile(r'[一-鿿a-zA-Z]{3,15}(?:有限|股份|集团)公司'),
]


def _extract_company_from_title(title: str) -> str:
    """
    从商品标题中提取真实的企业/公司名称（非品牌+品类拼凑）。

    1688 标题常见格式：
      - "广州市欧朵日用品有限公司 抽纸 纸巾 餐巾纸-阿里巴巴"
      - "抽纸厂家批发 整箱装-广州市欧朵日用品有限公司-阿里巴巴"
      - "产品名 公司名 1688"

    Returns:
        提取到的公司名，或空字符串
    """
    if not title:
        return ""

    title_clean = title.strip()
    # 去掉末尾的平台后缀（"-阿里巴巴"、"1688"等）
    for suffix in ["-阿里巴巴", "-1688", "1688", "-淘宝", "-天猫", "-京东"]:
        if title_clean.endswith(suffix):
            title_clean = title_clean[:-len(suffix)].strip()

    for pattern in _COMPANY_NAME_PATTERNS:
        for match in pattern.finditer(title_clean):
            name = match.group(0).strip()
            # 过滤过短或明显不是公司名的结果（如"抽纸厂"只有3字→跳过）
            if len(name) >= 4 and not name.endswith("产品") and not name.endswith("商品"):
                return name

    return ""


def _extract_company_from_summary(summary: str) -> str:
    """
    从搜索摘要中提取公司名。

    1688 摘要中"综合服务"前的文本往往是公司名。
    """
    if not summary:
        return ""

    # 尝试从"综合服务"前提取
    if "综合服务" in summary:
        before = summary.split("综合服务")[0].strip()
        for pattern in _COMPANY_NAME_PATTERNS:
            for match in pattern.finditer(before):
                name = match.group(0).strip()
                if len(name) >= 4:
                    return name

    # 全量匹配
    for pattern in _COMPANY_NAME_PATTERNS:
        for match in pattern.finditer(summary):
            name = match.group(0).strip()
            if len(name) >= 4:
                return name

    return ""


# ─── 品牌提取（保留用于优势说明等辅助用途，不再用于供应商名称） ───

# 常见品牌关键词（食品饮料类 + 工业品类 + 日用品类）
_KNOWN_BRANDS = [
    # 食品饮料
    "蒙牛", "伊利", "光明", "三元", "君乐宝", "旺仔", "娃哈哈", "康师傅",
    "统一", "农夫山泉", "可口可乐", "百事", "雀巢", "达利园", "盼盼",
    "三只松鼠", "良品铺子", "百草味", "德芙", "费列罗",
    # 日用品/纸品
    "维达", "清风", "心相印", "洁柔", "得宝", "五月花", "植护", "斑布",
    "泉林本色", "妮飘", "舒洁",
    # 工业品
    "公牛", "德力西", "正泰", "施耐德", "西门子", "松下", "飞利浦",
    "华为", "小米", "联想", "戴尔", "华硕",
]


def _extract_brand_from_title(title: str, platform: str = "") -> str:
    """
    从商品标题中提取品牌+品类关键词（仅供辅助信息使用，不替代供应商名称）。

    例如:
      "蒙牛 3.2g蛋白质纯牛奶专享装 200ml*24" -> "蒙牛纯牛奶"
      "维达超韧抽纸 3层130抽" -> "维达抽纸"
    """
    if not title:
        return ""
    title = title.strip()

    # 1. 匹配已知品牌
    found_brand = ""
    for brand in _KNOWN_BRANDS:
        if brand in title:
            found_brand = brand
            break

    # 2. 提取品类关键词（2-4个汉字的通用品类词）
    import re as _re
    category_words = _re.findall(r'[一-鿿]{2,4}(?:牛奶|电缆|电线|轴承|纸|笔|墨|箱|盒|袋|纸巾|抽纸|卷纸|面巾)', title)
    if not category_words:
        # fallback: 取标题前6个中文字符作为品类
        chinese_chars = _re.findall(r'[一-鿿]+', title)
        if chinese_chars:
            cat = chinese_chars[0][:6]
        else:
            cat = ""
    else:
        cat = category_words[0]

    # 3. 组合命名
    if found_brand and cat:
        name = f"{found_brand}{cat}"
    elif found_brand:
        name = f"{found_brand}"
    elif cat:
        name = f"{cat}"
    else:
        return ""

    return name


# ─── 格式转换（兼容 supplier_screening） ───

def _convert_to_supplier_format(items: list[dict], demand: dict = None) -> list[dict]:
    """
    将电商商品格式转换为 supplier_screening 模块所需的供应商格式

    外部搜索结果基准分 60（物流网络可实现全国主要城市次日/隔日达）。
    LLM 后续会根据交货地+时限二次筛选。
    """
    # 提取交货地信息用于备注
    delivery_city = ""
    if demand:
        overall_reqs = demand.get("整体特殊要求", [])
        for req in overall_reqs:
            cities = re.findall(r'(?:交货至|送达|到|至)\s*([一-鿿]{2,4})', req)
            if cities:
                delivery_city = cities[0]
                break
        if not delivery_city:
            delivery_city = demand.get("deliveryLocation", "").strip()

    formatted = []
    for item in items:
        product_name = item.get("product_name", "")
        platform = item.get("platform", "")
        url = item.get("url", "")
        price_range = item.get("price_range")
        price_num = item.get("price_num")
        shop_name = item.get("shop_name", "")
        delivery_from = item.get("delivery_from")
        free_shipping = item.get("free_shipping", False)
        moq = item.get("moq")
        rating = item.get("rating")
        returns_policy = item.get("returns_policy")

        # 清理报价格式
        if price_range and isinstance(price_range, str):
            price_range = price_range.strip()
            if len(price_range) > 30:
                price_range = price_range[:27] + "..."
        elif not price_range:
            price_range = "见商品页"

        # ── 供应商名称生成策略（优先级递减）──
        # 策略1: LLM 提取到真实商家/店铺名 → 直接使用
        if shop_name and shop_name not in ("商家名称待核实", "null", ""):
            company_name = shop_name if len(shop_name) <= 40 else shop_name[:37] + "..."
        else:
            # 策略2: 从商品标题中匹配公司名（如"广州市欧朵日用品有限公司"）
            company_name = _extract_company_from_title(product_name)

            # 策略3: 从摘要中匹配公司名（1688摘要"综合服务"前常有公司名）
            if not company_name:
                company_name = _extract_company_from_summary(item.get("summary", ""))

            # 策略4: 兜底 — [平台]电商商家（品类）
            if not company_name:
                platform_label = {
                    "1688": "1688", "淘宝": "淘宝", "天猫": "天猫",
                    "京东": "京东", "拼多多": "拼多多",
                }.get(platform, platform)
                # 尝试从需求中提取品类关键词
                category_hint = ""
                if demand:
                    try:
                        from utils.scoring import extract_first_item
                        demand_item = extract_first_item(demand)
                        category_hint = demand_item.get("category", "")
                    except Exception:
                        pass
                if category_hint:
                    company_name = f"[{platform_label}] 电商商家（{category_hint}）"
                else:
                    company_name = f"[{platform_label}] 电商商家"

        # ── 智能评分（基于提取到的真实信号） ──

        # 品质评分：有好评率→直接用；有退货保障→+10
        quality_score = 60
        if rating is not None:
            quality_score = min(rating, 100)
        elif returns_policy:
            quality_score = 70

        # 信用评分
        credit_score = 60
        if platform in ("京东", "天猫"):
            credit_score = 70
        if returns_policy:
            credit_score += 5
        credit_score = min(credit_score, 100)

        # 交付评分：有发货地→可评估距离
        delivery_score = 60
        delivery_note = "见商品页"
        if delivery_from:
            delivery_score = 70
            delivery_note = f"发货地{delivery_from}"
            if delivery_city and delivery_from in delivery_city:
                delivery_score = 85
                delivery_note = f"本地发货（{delivery_from}）"
            elif delivery_city:
                delivery_note = f"从{delivery_from}发往{delivery_city}（约2-5天）"
            else:
                delivery_note = f"发货地{delivery_from}（全国可达）"
        elif delivery_city:
            delivery_note = f"物流可达{delivery_city}（时效见商品页）"

        # 价格评分
        price_score = 50
        if price_num is not None and price_num > 0:
            price_score = 65
            if free_shipping:
                price_score += 5
        price_score = min(price_score, 100)

        # 综合评分
        overall = round(
            quality_score * 0.20 +
            credit_score * 0.15 +
            delivery_score * 0.20 +
            price_score * 0.25 +
            60 * 0.20,
            1
        )

        # ── 构建备注 ──
        notes_parts = [f"来自{platform}"]
        if price_num is not None:
            notes_parts.append(f"报价¥{price_num}")
        if delivery_from:
            notes_parts.append(f"发货地{delivery_from}")
        if free_shipping:
            notes_parts.append("包邮")
        if rating is not None:
            notes_parts.append(f"好评率{int(rating)}%")
        if returns_policy:
            notes_parts.append(returns_policy)
        if moq is not None:
            # 推断单位
            unit_hint = ""
            if demand:
                try:
                    from utils.scoring import extract_first_item
                    demand_item = extract_first_item(demand)
                    unit_hint = demand_item.get("unit", "")
                except Exception:
                    pass
            notes_parts.append(f"起订{moq}{unit_hint or '件'}")
        notes_parts.append("信息以商品页为准")

        # 起订量不满足扣分
        if moq is not None and demand:
            try:
                from utils.scoring import extract_first_item
                demand_item = extract_first_item(demand)
                demand_qty = demand_item.get("qty", 0) or 0
                if demand_qty > 0 and moq > demand_qty:
                    notes_parts.append(f"⚠起订量{moq}>采购量{demand_qty}")
                    overall = max(overall - 10, 30)
            except Exception:
                pass

        formatted.append({
            "supplier": {
                "company_name": company_name,
                "main_products": product_name,
                "price_range_low": price_num,
                "price_range_high": None,
                "priceRange": price_range,
                "delivery_cycle": delivery_note,
                "deliveryCycle": delivery_note,
                "quality_rating": quality_score,
                "credit_rating": credit_score,
                "service_rating": 70 if returns_policy else 60,
                "contact_info": url,
                "notes": "；".join(notes_parts) + "。",
            },
            "score": {
                "overall": overall,
                "breakdown": {
                    "product": 60,
                    "price": price_score,
                    "delivery": delivery_score,
                    "quality": quality_score,
                    "credit": credit_score,
                },
            },
            "source": "web_search",
            "ecommerce": {
                "platform": platform,
                "product_name": product_name,
                "url": url,
                "price_range": price_range,
                "price_num": price_num,
                "delivery_from": delivery_from,
                "free_shipping": free_shipping,
                "moq": moq,
                "rating": rating,
                "returns_policy": returns_policy,
            },
        })
    return formatted


# ─── LLM 兜底生成 ───

def _llm_fallback_suppliers(query: str, demand: dict = None) -> list[dict]:
    """
    第3轮兜底：所有搜索无果后，由 LLM 根据品类知识推荐参考供应商。

    明确标注"LLM建议，未经核实"，评分降至 35。
    """
    if not DEEPSEEK_API_KEY:
        print("[搜索] LLM兜底跳过：无 DEEPSEEK_API_KEY")
        return []

    # 提取交货地
    delivery_city = ""
    if demand:
        overall_reqs = demand.get("整体特殊要求", [])
        for req in overall_reqs:
            cities = re.findall(r'(?:交货至|送达|到|至)\s*([一-鿿]{2,4})', req)
            if cities:
                delivery_city = cities[0]
                break

    prompt = f"""你是供应链采购专家。请为"{query}"推荐2家中国供应商（可以是知名品牌或行业常见供应商）。

要求：
1. 供应商名称必须是真实存在的企业（知名品牌或行业头部厂商）
2. 不确定的信息留空
3. 仅输出 JSON 数组，格式：
[{{"company_name":"企业全称","main_products":"主营产品","location":"总部城市","notes":"推荐理由（15字内）"}}]"""

    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            max_tokens=800,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content.strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        items = json.loads(content)

        formatted = []
        for item in items[:2]:  # 最多2家
            if not isinstance(item, dict):
                continue
            name = item.get("company_name", "")
            if not name:
                continue
            location = item.get("location", "未知")
            delivery_note = f"LLM建议，未经核实"
            if delivery_city:
                delivery_note = f"LLM建议（{location}→{delivery_city}物流可达），未经核实"

            formatted.append({
                "supplier": {
                    "company_name": f"[参考] {name}",
                    "main_products": item.get("main_products", query),
                    "price_range_low": None,
                    "price_range_high": None,
                    "priceRange": "需询价",
                    "delivery_cycle": delivery_note,
                    "deliveryCycle": delivery_note,
                    "quality_rating": 35,
                    "credit_rating": 35,
                    "service_rating": 35,
                    "contact_info": "",
                    "notes": f"LLM参考建议：{item.get('notes', '请自行核实')}。建议通过1688或行业黄页进一步搜索确认。",
                },
                "score": {
                    "overall": 35.0,
                    "breakdown": {"product": 40, "price": 25, "delivery": 35, "quality": 35, "credit": 35},
                },
                "source": "llm_fallback",
                "ecommerce": {},
            })
        print(f"[搜索] LLM兜底生成 {len(formatted)} 家参考供应商")
        return formatted
    except Exception as e:
        print(f"[搜索] LLM兜底失败: {e}")
        return []


# ─── Mock 模拟搜索（已废弃——由多轮搜索+LLM兜底替代） ───

def _search_with_mock(query: str, demand: dict = None) -> list[dict]:
    """
    模拟搜索结果（已废弃——由多轮搜索+LLM兜底替代）
    严格遵循规则：无真实搜索时返回空数组，不编造商品页
    """
    print("[搜索] Mock（已废弃）: 当前无可用搜索 API")
    return []


# ─── 自测 ───

if __name__ == "__main__":
    # 测试关键词增强
    print("=== 关键词增强测试 ===")
    for q in ["线缆", "6平方线缆 供应商", "轴承 1688批发"]:
        print(f"  '{q}' -> '{_enrich_query(q)}'")

    # 测试从 JSON 构建搜索词
    print("\n=== JSON -> 搜索词 ===")
    d1 = {"采购物品清单": [{"品类": "轴承", "规格": "6205深沟球"}]}
    d2 = {"采购物品清单": [{"品类": "线缆"}]}
    d3 = {"productCategory": "办公用品", "specification": "A4纸"}  # 旧格式兼容
    print(f"  v2.0: {_build_query_from_demand(d1)}")
    print(f"  v2.0无规格: {_build_query_from_demand(d2)}")
    print(f"  旧格式: {_build_query_from_demand(d3)}")

    # 测试搜索（仅传 demand JSON）
    print("\n=== 搜索测试 ===")
    results = search_suppliers_online({"采购物品清单": [{"品类": "线缆", "规格": "6平方"}]})
    print(f"返回 {len(results)} 条结果")
    if results:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        print("[]  <-- 正确：无 API Key 时不编造数据")
