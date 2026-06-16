"""
工作流 2：供应商智能匹配与推荐 (Intelligent_supplier_screening)
基于 v2.0 结构化采购需求单，LLM 驱动硬性门槛筛选 + 多维度排序 + 联网电商补充，输出 Top3 推荐
"""

import json
import math
import re
from typing import Optional

from openai import OpenAI

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_MAX_TOKENS,
    MATCH_MIN_COUNT,
    MATCH_MIN_SCORE,
    MATCH_LOW_THRESHOLD,
    SEARCH_MAX_RESULTS,
)
from db.database import search_suppliers, get_all_suppliers
from utils.web_search import search_suppliers_online
from utils.scoring import extract_first_item, parse_budget, calculate_match_score
from src.prompts import load_prompt


# ─── 供应商数据格式化（供 LLM 使用） ───

def _format_suppliers_for_llm(suppliers: list[dict]) -> str:
    """
    将供应商列表格式化为 LLM 可读的文本表格

    包含所有可用于硬性门槛筛选和多维度排序的字段
    """
    if not suppliers:
        return "（无供应商数据）"

    lines = []
    lines.append(f"## 内部供应商数据库（共 {len(suppliers)} 家）")
    lines.append("")

    for i, s in enumerate(suppliers, 1):
        sid = s.get("id", f"S{i:03d}")
        lines.append(f"### [{sid}] {s.get('company_name', '未知')}")
        lines.append(f"- 企业规模（注册资本）: {s.get('registered_capital', '未公开')}")
        lines.append(f"- 成立年份: {s.get('established_year', '未公开')}")
        lines.append(f"- 员工人数: {s.get('employee_count', '未公开')}")
        lines.append(f"- 主营产品: {s.get('main_products', '')}")
        lines.append(f"- 产品分类: {s.get('product_categories', '')}")
        lines.append(f"- 规格参数: {s.get('specifications', '')}")
        lines.append(f"- 报价范围: {s.get('price_range_low', '-')} - {s.get('price_range_high', '-')} {s.get('price_unit', '元')}")
        lines.append(f"- 交付周期: {s.get('delivery_cycle', '未公开')}")
        lines.append(f"- 可交付区域: {s.get('delivery_regions', '未公开')}")
        lines.append(f"- 质量评分: {s.get('quality_rating', '-')}/100")
        lines.append(f"- 信用评分: {s.get('credit_rating', '-')}/100")
        lines.append(f"- 服务评分: {s.get('service_rating', '-')}/100")
        lines.append(f"- 资质认证: {s.get('certifications', '未公开')}")
        lines.append(f"- 备注: {s.get('notes', '无')}")
        lines.append(f"- 联系方式: {s.get('contact_info', '未公开')}")
        lines.append(f"- 官网: {s.get('website', '无')}")
        lines.append("")

    return "\n".join(lines)


# ─── LLM 驱动供应商筛选 ───

def _llm_screen_suppliers(demand: dict, suppliers: list[dict]) -> dict:
    """
    使用 LLM（DeepSeek）+ system prompt 进行硬性门槛筛选 + 多维度排序

    Args:
        demand: v2.0 采购需求单
        suppliers: 内部供应商列表

    Returns:
        LLM 输出的结构化 JSON（筛选结果说明 + 推荐供应商列表 + 未推荐原因说明）
        或 {"error": True, "message": str}
    """
    if not DEEPSEEK_API_KEY:
        return {"error": True, "message": "未配置 DEEPSEEK_API_KEY"}

    if not suppliers:
        return {
            "筛选结果说明": "共筛选出0家符合要求的供应商",
            "推荐供应商列表": [],
            "未推荐原因说明": "供应商数据库为空",
        }

    # 加载 system prompt
    system_prompt = load_prompt("supplier_screening_system.md")

    # 格式化供应商数据
    supplier_text = _format_suppliers_for_llm(suppliers)

    # 格式化采购需求单
    demand_json = json.dumps(demand, ensure_ascii=False, indent=2)

    user_prompt = f"""以下是采购需求单和供应商数据库，请按照规则进行筛选。

## 采购需求单
```json
{demand_json}
```

{supplier_text}
"""

    try:
        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            max_tokens=DEEPSEEK_MAX_TOKENS,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        content = response.choices[0].message.content.strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        result = json.loads(content)

        # 检查是否是异常响应
        if isinstance(result, dict) and "推荐供应商列表" in result:
            return result
        else:
            return {"error": True, "message": "LLM 返回格式异常", "raw": content[:500]}

    except json.JSONDecodeError as e:
        return {"error": True, "message": f"LLM 输出非 JSON: {e}", "raw": content[:500] if 'content' in dir() else ""}
    except Exception as e:
        return {"error": True, "message": f"LLM 筛选异常: {e}"}


# ─── LLM 结果转 supplier_screening 格式 ───

def _convert_llm_result(llm_result: dict, suppliers: list[dict], demand: dict) -> list[dict]:
    """
    将 LLM 筛选结果转换为 supplier_screening 兼容格式

    Args:
        llm_result: LLM 返回的 {"推荐供应商列表": [...], ...}
        suppliers: 原始供应商列表（用于查找完整数据）
        demand: v2.0 采购需求单（用于计算真实匹配评分）

    Returns:
        [{"supplier": {...}, "score": {...}, "source": "internal", "llm_analysis": {...}}, ...]
    """
    ranked = llm_result.get("推荐供应商列表", [])
    if not ranked:
        return []

    # 建立供应商查找索引（按名称 + ID）
    supplier_by_name = {}
    supplier_by_id = {}
    for i, s in enumerate(suppliers):
        name = s.get("company_name", "")
        sid = str(s.get("id", f"S{i+1:03d}"))
        supplier_by_name[name] = s
        supplier_by_id[sid] = s

    formatted = []
    for item in ranked:
        name = item.get("供应商名称", "")
        sid = item.get("供应商ID", "")

        # 查找原始供应商数据
        supplier = supplier_by_id.get(sid) or supplier_by_name.get(name)
        if not supplier:
            # LLM 返回的供应商不在数据库中，跳过
            continue

        # 使用真实评分引擎计算匹配度（替代硬编码的 rank-based 分数）
        rank = item.get("排名", len(formatted) + 1)
        score = calculate_match_score(supplier, demand)

        formatted.append({
            "supplier": supplier,
            "score": score,
            "source": "internal",
            "llm_analysis": {
                "排名": rank,
                "供应商ID": sid,
                "供应商名称": name,
                "核心匹配优势": item.get("核心匹配优势", []),
                "潜在风险": item.get("潜在风险", []),
                "推荐理由": item.get("推荐理由", ""),
                "企业规模": item.get("企业规模", ""),
            },
        })

    return formatted


# ─── 采购物品信息提取（委托给 utils.scoring） ───
# extract_first_item / parse_budget / calculate_match_score 已移至 utils/scoring.py


# PricePeek 增强已移至 utils/pricepeek_enrich.py — 作为可选独立步骤调用:
#   from utils.pricepeek_enrich import enrich_with_pricepeek


# ─── 独立工作流函数 ───

def screen_internal_suppliers(demand: dict) -> dict:
    """
    仅内部供应商库筛选（不触发联网搜索，不调用 PricePeek）。

    Args:
        demand: v2.0 结构化采购需求单 dict

    Returns:
        {
            "matches": [SupplierMatch, ...],   # 评分排序后的内部匹配
            "total_searched": int,              # 内部库搜索到的候选数
            "valid_count": int,                 # 有效匹配数
            "llm_result": dict | None,          # LLM 原始返回
        }
    """
    item = extract_first_item(demand)
    category = item["category"]

    # Step 1: 内部供应商库搜索
    internal_results = search_suppliers(
        keyword=category,
        category=category,
        limit=20,
    )

    # Step 2: LLM 筛选或规则引擎降级
    llm_result = None
    matches = []

    if internal_results:
        print(f"[筛选] 内部库找到 {len(internal_results)} 家，调用 LLM 筛选...")
        llm_result = _llm_screen_suppliers(demand, internal_results)

        if llm_result.get("error"):
            print(f"[筛选] LLM 筛选失败: {llm_result['message']}，降级使用规则引擎")
            internal_scored = []
            for supplier in internal_results:
                score = calculate_match_score(supplier, demand)
                internal_scored.append({
                    "supplier": supplier,
                    "score": score,
                    "source": "internal",
                })
            internal_scored.sort(key=lambda x: x["score"]["overall"], reverse=True)
            matches = [s for s in internal_scored if s["score"]["overall"] >= MATCH_LOW_THRESHOLD]
        else:
            matches = _convert_llm_result(llm_result, internal_results, demand)
            print(f"[筛选] LLM 推荐 {len(matches)} 家: {llm_result.get('筛选结果说明', '')}")

    return {
        "matches": matches,
        "total_searched": len(internal_results),
        "valid_count": len(matches),
        "llm_result": llm_result,
    }


def search_web_suppliers(demand: dict, query: str = None) -> list[dict]:
    """
    仅联网电商搜索（不依赖内部库，不调用 PricePeek）。

    Args:
        demand: v2.0 采购需求单 dict
        query: 可选手动搜索词覆盖

    Returns:
        [SupplierMatch, ...] — 结构化供应商列表
    """
    return search_suppliers_online(demand, query=query)


def merge_and_rank(
    internal_matches: list[dict],
    web_matches: list[dict],
    top_n: int = 3,
) -> list[dict]:
    """
    合并内部 + 联网结果，去重，按评分降序排列，取 Top N。

    Args:
        internal_matches: screen_internal_suppliers 返回的 matches
        web_matches: search_web_suppliers 返回的列表
        top_n: 返回数量

    Returns:
        排序去重后的 Top N 列表
    """
    # 名称归一化：剥离 [平台] 前缀用于去重比较
    import re as _re
    def _normalize(name: str) -> str:
        return _re.sub(r'^\[.*?\]\s*', '', name).strip()

    all_results = list(internal_matches)
    existing_names = {_normalize(r["supplier"]["company_name"]) for r in all_results}
    existing_urls = {r.get("ecommerce", {}).get("url", "") for r in all_results if r.get("ecommerce", {}).get("url")}

    for wr in web_matches:
        cn = wr["supplier"]["company_name"]
        normalized = _normalize(cn)
        ecom_url = wr.get("ecommerce", {}).get("url", "")

        # 名称去重（归一化后精确匹配）
        is_dup = normalized in existing_names
        # URL 去重
        if not is_dup and ecom_url and ecom_url in existing_urls:
            is_dup = True
        # 模糊去重：短名称包含检查
        if not is_dup:
            for en in existing_names:
                if len(normalized) >= 4 and len(en) >= 4:
                    if normalized in en or en in normalized:
                        is_dup = True
                        break

        if not is_dup:
            all_results.append(wr)
            existing_names.add(normalized)
            if ecom_url:
                existing_urls.add(ecom_url)

    all_results.sort(key=lambda x: x["score"]["overall"], reverse=True)
    return all_results[:top_n]


def _should_trigger_web_search(internal_result: dict) -> bool:
    """判断是否需要触发联网搜索"""
    matches = internal_result.get("matches", [])
    if len(matches) < MATCH_MIN_COUNT:
        return True
    if matches:
        avg_score = sum(m["score"]["overall"] for m in matches) / len(matches)
        if avg_score < MATCH_MIN_SCORE:
            return True
    return False


# ─── 编排函数（向后兼容 app.py） ───

def screen_suppliers(demand: dict) -> dict:
    """
    供应商智能匹配与推荐主流程（编排层）

    保持与 app.py 的向后兼容。如需独立调用，请使用：
      - screen_internal_suppliers(demand)
      - search_web_suppliers(demand)
      - merge_and_rank(internal, web)

    Returns:
        包含匹配结果和推荐列表的 dict（与旧版格式一致）
    """
    item = extract_first_item(demand)

    # 内部筛选
    internal = screen_internal_suppliers(demand)
    llm_screened = internal["matches"]

    # 判断是否需要联网搜索
    need_web_search = _should_trigger_web_search(internal)
    reason = ""
    if need_web_search:
        if len(llm_screened) == 0:
            reason = "库内无匹配供应商"
        elif len(llm_screened) < MATCH_MIN_COUNT:
            reason = f"库内有效匹配仅 {len(llm_screened)} 家（不足 {MATCH_MIN_COUNT} 家）"
        else:
            avg_score = sum(s["score"]["overall"] for s in llm_screened) / len(llm_screened)
            reason = f"平均匹配度 {avg_score:.1f}%（低于 {MATCH_MIN_SCORE}%）"
    else:
        reason = f"库内匹配充足（{len(llm_screened)} 家，均分 {sum(s['score']['overall'] for s in llm_screened) / len(llm_screened):.1f}%）"

    # 联网搜索
    web_results = []
    if need_web_search:
        web_results = search_web_suppliers(demand)

    # 合并去重
    top3 = merge_and_rank(llm_screened, web_results, top_n=3)

    return {
        "demand": demand,
        "item": item,
        "internal_count": internal["total_searched"],
        "valid_internal_count": internal["valid_count"],
        "web_count": len(web_results),
        "need_web_search": need_web_search,
        "search_reason": reason,
        "llm_result": internal["llm_result"],
        "top3": top3,
        "total_found": len(llm_screened) + len(web_results),
        "insufficient": len(top3) < 3,
    }


# ─── 报价单位归一化 ───

def _normalize_price_display(supplier: dict, demand_unit: str, ecom: dict = None) -> str:
    """
    将供应商报价格式化为统一显示格式，匹配需求单位。

    Args:
        supplier: 供应商数据 dict
        demand_unit: 采购需求单中的单位（如 "米"、"个"、"件"）
        ecom: 电商数据（仅 web_search 来源使用 priceRange 字段）

    Returns:
        格式化后的报价字符串
    """
    # 联网结果：使用 LLM 提取的价格 + 需求单位
    if ecom:
        price_num = ecom.get("price_num")
        # 确保数值类型（LLM 可能返回字符串）
        if price_num is not None:
            try:
                price_num = float(price_num)
            except (ValueError, TypeError):
                price_num = None
        price_raw = ecom.get("price_range")
        if price_num is not None and price_num > 0:
            unit_label = f"元/{demand_unit}" if demand_unit else "元"
            if price_num == int(price_num):
                price_num = int(price_num)
            return f"{price_num}{unit_label}"
        elif price_raw and price_raw != "见商品页":
            raw = str(price_raw).strip()
            if len(raw) > 30:
                raw = raw[:27] + "..."
            return raw
        else:
            return "见商品页"

    # 内部供应商：从 price_range_low/high + price_unit 构建
    low = supplier.get("price_range_low")
    high = supplier.get("price_range_high")
    unit = supplier.get("price_unit", "元")

    if low is None:
        return "待询价"

    # 提取供应商报价中的计量单位（从 "元/米" 中取 "米"）
    supplier_measure = str(unit)
    if "/" in supplier_measure:
        supplier_measure = supplier_measure.split("/", 1)[1]

    demand_measure = demand_unit.strip() if demand_unit else ""

    # 构建报价字符串
    if low == high:
        price_str = f"{low}{unit}"
    else:
        # 处理浮点数显示（整数值不显示小数点）
        if isinstance(low, float) and low == int(low):
            low = int(low)
        if isinstance(high, float) and high == int(high):
            high = int(high)
        price_str = f"{low}-{high}{unit}"

    # 单位不一致时加标记
    if demand_measure and supplier_measure != demand_measure:
        price_str += " ⚠️"

    return price_str


# ─── 推荐报告格式化 ───

def format_recommendation_report(result: dict) -> str:
    """
    将匹配结果格式化为可读的推荐报告（Markdown）

    参考格式：紧凑对比表格 + 优势说明 + 专业建议
    """
    demand = result["demand"]
    item = result.get("item", {})
    top3 = result["top3"]

    lines = []

    # ── 采购需求摘要 ──
    items = demand.get("采购物品清单", [])
    first_item = items[0] if items else {}
    category = first_item.get("品类", "-")
    spec = first_item.get("规格", "-")
    qty = first_item.get("数量", "-")
    unit = first_item.get("单位", "-")
    budget = first_item.get("预算", "-")
    delivery_date = demand.get("期望送达日期", "未指定")

    lines.append("## 📋 采购需求")
    lines.append("")
    lines.append(f"**{category}** | {spec} | {qty}{unit} | 预算: {budget} | 期望送达: {delivery_date}")

    overall_reqs = demand.get("整体特殊要求", [])
    if overall_reqs:
        lines.append(f"整体要求: {'; '.join(overall_reqs)}")
    lines.append("")

    # ── 匹配来源 ──
    lines.append(
        f"🔍 内部库 {result['internal_count']} 家 → 有效 {result['valid_internal_count']} 家"
        f"{' + 联网补充 ' + str(result['web_count']) + ' 家' if result.get('need_web_search') else ''}"
    )
    lines.append("")

    if not top3:
        lines.append("### ⚠️ 未找到匹配的供应商")
        lines.append("")
        lines.append("建议：扩大搜索范围，或调整采购需求参数后重新查询。")
        return "\n".join(lines)

    # ── 提取交货地城市（用于优势说明）──
    demand_city = ""
    overall_reqs = demand.get("整体特殊要求", [])
    for req in overall_reqs:
        cities = re.findall(r'(?:交货至|送达|到|至)\s*([一-鿿]{2,4})', req)
        if cities:
            demand_city = cities[0]
            break

    # ── 供应商推荐对比表 ──
    lines.append("## 🏆 供应商推荐")
    lines.append("")

    # 根据采购物品确定报价列标题
    price_header = f"报价（{unit}）" if unit and unit != "-" else "报价"
    lines.append(f"| 供应商名称 | 主营产品 | {price_header} | 交付周期 | 优势说明 |")
    lines.append(f"|------------|----------|--------|----------|----------|")

    for ranked_item in top3:
        supplier = ranked_item["supplier"]
        score = ranked_item["score"]
        source = ranked_item["source"]
        llm = ranked_item.get("llm_analysis", {})
        ecom = ranked_item.get("ecommerce", {})

        # ── 供应商名称 ──
        if source == "web_search":
            # 联网结果：使用实际商家/店铺名称
            name = supplier.get("company_name", "商家名称待核实")
            # 如果名字太长，截断
            if len(name) > 28:
                name = name[:25] + "..."
        else:
            # 内部供应商：只显示公司名，注册资本/规模移到优势列
            name = supplier.get("company_name", "未知供应商")
            if len(name) > 28:
                name = name[:25] + "..."

        # ── 主营产品 ──
        if source == "web_search" and ecom:
            # 电商结果：显示商品标题作为主营产品
            raw = ecom.get("product_name", "-")
            main_product = raw[:22] + ("..." if len(raw) > 22 else "")
        else:
            main_product = supplier.get("main_products", "-")
            if len(main_product) > 22:
                main_product = main_product[:19] + "..."

        # ── 报价 ──
        # 获取需求单中的单位
        demand_unit = item.get("unit", "")
        if source == "web_search":
            price = _normalize_price_display(supplier, demand_unit, ecom=ecom)
        else:
            price = _normalize_price_display(supplier, demand_unit)

        # ── 交付周期 ──
        if source == "web_search":
            # 使用智能构建的交付描述（含发货地+物流预估）
            delivery = supplier.get("delivery_cycle", "见商品页")
            if len(delivery) > 25:
                delivery = delivery[:22] + "..."
        else:
            delivery = supplier.get("delivery_cycle", "-")

        # ── 优势说明 ──
        if source == "web_search" and ecom:
            platform = ecom.get("platform", "")
            advantage_parts = []

            # 发货地 + 时效
            delivery_from = ecom.get("delivery_from", "")
            if delivery_from:
                if demand_city and demand_city in delivery_from:
                    advantage_parts.append(f"{demand_city}本地发货")
                else:
                    advantage_parts.append(f"发货地{delivery_from}")

            # 价格 + 预算优势
            price_val = ecom.get("price_num")
            if price_val is not None:
                try:
                    price_val = float(price_val)
                except (ValueError, TypeError):
                    price_val = None
            if price_val is not None and price_val > 0:
                demand_unit = item.get("unit", "")
                unit_label = f"/{demand_unit}" if demand_unit else ""
                try:
                    _qty = int(qty) if qty and qty not in ("-", "") else 0
                except (ValueError, TypeError):
                    _qty = 0
                total_price = price_val * _qty if _qty > 0 else price_val
                budget_info = parse_budget(item.get("budget", ""))
                budget_high = budget_info.get("high")
                if budget_high and total_price < budget_high:
                    advantage_parts.append(f"单价¥{price_val}{unit_label}，总价¥{total_price:.0f}远低于预算¥{budget_high:.0f}")
                else:
                    advantage_parts.append(f"单价¥{price_val}{unit_label}")

            # 包邮
            if ecom.get("free_shipping"):
                advantage_parts.append("包邮")
            # 起订量
            moq = ecom.get("moq")
            if moq is not None:
                try:
                    moq = int(float(moq))
                except (ValueError, TypeError):
                    moq = None
            if moq is not None and moq > 0:
                if _qty > 0 and moq <= _qty:
                    advantage_parts.append(f"起订{moq}件（满足采购量）")
                else:
                    advantage_parts.append(f"起订{moq}件")
            # 好评率
            rating_val = ecom.get("rating")
            if rating_val is not None:
                try:
                    rating_val = float(rating_val)
                except (ValueError, TypeError):
                    rating_val = None
            if rating_val is not None and rating_val > 0:
                if rating_val >= 95:
                    advantage_parts.append(f"好评率{int(rating_val)}%")
                else:
                    advantage_parts.append(f"好评{int(rating_val)}%")
            # 售后保障
            returns = ecom.get("returns_policy")
            if returns:
                advantage_parts.append(returns)
            # 品牌提示
            product_name = ecom.get("product_name", "")
            for brand in ["蒙牛", "伊利", "光明", "三元", "君乐宝", "旺仔", "娃哈哈"]:
                if brand in product_name:
                    advantage_parts.append(f"{brand}品牌保障")
                    break

            advantage = "，".join(advantage_parts) if advantage_parts else f"{platform}平台商家，报价见商品页"
        elif llm:
            advantage = llm.get("推荐理由", "")
            if not advantage:
                advantages = llm.get("核心匹配优势", [])
                advantage = "；".join(advantages[:3]) if advantages else "-"
        else:
            advantage = supplier.get("notes", "-")

        # ── 统一附加电商参考价（PricePeek 增强数据）──
        ecom_ref = ranked_item.get("ecommerce", {})
        ref_price = ecom_ref.get("reference_price")
        if ref_price and source != "web_search":
            ref_platform = ecom_ref.get("reference_platform", "电商")
            ref_dev = ecom_ref.get("price_deviation_pct")
            if ref_dev is not None:
                direction = "高于" if ref_dev > 0 else "低于"
                advantage += f" [电商参考: {ref_platform} ¥{ref_price}，{direction}内部报价{abs(ref_dev):.0f}%]"
            else:
                advantage += f" [电商参考: {ref_platform} ¥{ref_price}]"
        # 限制总长
        if len(advantage) > 65:
            advantage = advantage[:62] + "..."

        lines.append(
            f"| {name} "
            f"| {main_product} "
            f"| {price} "
            f"| {delivery} "
            f"| {advantage} |"
        )

    lines.append("")

    # ── 专业采购建议 ──
    if len(top3) >= 2:
        best = top3[0]
        second = top3[1]
        score_diff = best["score"]["overall"] - second["score"]["overall"]

        lines.append("### 💼 采购建议")
        lines.append("")

        best_name = best["supplier"]["company_name"]
        if score_diff >= 15:
            lines.append(f"推荐优先联系 **{best_name}**，综合条件显著领先。")
        elif score_diff >= 5:
            lines.append(f"**{best_name}** 和 **{second['supplier']['company_name']}** 差距不大，建议同时询价比较后决策。")
        else:
            lines.append(f"Top3 供应商条件接近，建议全部询价，综合商务谈判后决策。")
        lines.append("")

    # ── 风险提示 ──
    if any(r["source"] == "web_search" for r in top3):
        lines.append("> ⚠️ 部分推荐来自联网电商搜索，供应商信息未经验证，建议优先核实资质后再合作。")
        lines.append("")

    if result.get("insufficient"):
        lines.append(f"> ⚠️ 当前仅匹配到 {len(top3)} 家供应商（不足3家），建议扩大搜索范围或放宽参数。")
        lines.append("")

    # ── 页脚说明 ──
    lines.append("---")
    lines.append("")
    lines.append("*以上是本次的供应商推荐结果。AI生成内容仅供参考，请以人工核对为准。如需生成可下载的PDF格式报告，请进一步告知。*")

    return "\n".join(lines)


# ─── 快速测试 ───

if __name__ == "__main__":
    # v2.0 格式的测试需求
    test_demand = {
        "采购需求单编号": "PR-20260613-001",
        "所选择的供应商": "",
        "期望送达日期": "2026-06-16",
        "整体特殊要求": ["需含增值税发票", "送货上门"],
        "采购物品清单": [
            {
                "序号": 1,
                "品类": "线缆",
                "数量": 5000,
                "单位": "米",
                "规格": "6平方铜芯",
                "预算": "≤60 元",
                "物品特殊要求": ["阻燃等级V0"],
            }
        ],
    }

    result = screen_suppliers(test_demand)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:3000])
    print("\n" + "=" * 60)
    print(format_recommendation_report(result))
