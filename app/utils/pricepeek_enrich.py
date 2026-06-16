"""
PricePeek 电商比价增强模块（可选独立步骤）

使用 PricePeek MCP 为供应商匹配结果附加电商平台实时价格参考。
此模块独立于内部筛选和联网搜索，由调用方决定是否启用。

用法:
    from utils.pricepeek_enrich import enrich_with_pricepeek
    enriched = enrich_with_pricepeek(matches, demand)
"""

from config import MCP_PRICEPEEK_ENABLED
from utils.scoring import extract_first_item


# MCP 客户端单例（惰性初始化）
_mcp_pricepeek = None


def _get_pricepeek_mcp():
    """获取 PricePeek MCP 客户端单例"""
    global _mcp_pricepeek
    if _mcp_pricepeek is None and MCP_PRICEPEEK_ENABLED:
        from utils.mcp_client import PricePeekMCP
        _mcp_pricepeek = PricePeekMCP()
        if not _mcp_pricepeek.start():
            print("[PricePeek] 启动失败，将跳过电商比价")
            _mcp_pricepeek = None
    return _mcp_pricepeek


def enrich_with_pricepeek(matches: list[dict], demand: dict) -> list[dict]:
    """
    可选的电商比价增强步骤。对匹配结果（内部或联网）附加电商参考价格。

    对每家供应商调用 PricePeek search_products，获取主流电商平台
    （淘宝/京东/拼多多）的实时价格作为比价参考。

    不修改原始评分（score 字段），仅在 ecommerce 字段附加参考信息。

    Args:
        matches: 供应商匹配列表 [{"supplier": ..., "score": ..., "source": ..., ...}, ...]
        demand: v2.0 采购需求单

    Returns:
        增强后的匹配列表（附加 ecommerce.reference_price 等字段）
    """
    if not MCP_PRICEPEEK_ENABLED:
        return matches

    pp = _get_pricepeek_mcp()
    if pp is None:
        return matches

    item = extract_first_item(demand)
    category = item.get("category", "")

    enriched_count = 0
    for result in matches:
        supplier = result.get("supplier", {})

        # 确定搜索关键词：优先用需求品类，其次用主营产品第一个词
        if result.get("source") == "web_search":
            ecom = result.get("ecommerce", {})
            product_name = ecom.get("product_name", "")
            search_query = product_name.split("—")[0].strip()[:30] if product_name else ""
        else:
            main_products = supplier.get("main_products", "")
            search_query = category if category else main_products.split("、")[0].strip()

        if not search_query or len(search_query) < 2:
            continue

        try:
            pp_result = pp.search_products(search_query, platforms=["all"], limit=3)
            if pp_result.get("success") and pp_result.get("data"):
                data = pp_result["data"]
                # 找价格最低的作为参考
                best = min(
                    (d for d in data if d.get("price", 0) > 0),
                    key=lambda d: d.get("price", float("inf")),
                    default=None,
                )
                if best and best.get("price", 0) > 0:
                    ref_price = best.get("price")
                    ref_platform = best.get("platform", "")
                    ref_url = best.get("url", "")

                    # 附加电商参考价格
                    if "ecommerce" not in result:
                        result["ecommerce"] = {}
                    result["ecommerce"]["reference_price"] = ref_price
                    result["ecommerce"]["reference_platform"] = ref_platform
                    result["ecommerce"]["reference_url"] = ref_url

                    # 计算与内部报价的偏差
                    supplier_price = supplier.get("price_range_low")
                    if supplier_price is not None:
                        deviation = ((ref_price - supplier_price) / supplier_price) * 100
                        result["ecommerce"]["price_deviation_pct"] = round(deviation, 1)

                    enriched_count += 1
                    print(f"[PricePeek] 增强: {supplier.get('company_name', 'N/A')[:20]} "
                          f"→ {ref_platform} ¥{ref_price}")
        except Exception as e:
            print(f"[PricePeek] 增强失败 '{search_query}': {e}")

    if enriched_count > 0:
        print(f"[PricePeek] 共增强 {enriched_count}/{len(matches)} 条结果")

    return matches
