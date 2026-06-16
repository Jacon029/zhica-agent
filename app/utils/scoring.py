"""
共享评分引擎模块

提供供应商筛选和联网搜索共用的核心函数：
  - extract_first_item: 从 v2.0 采购需求单提取第一个采购物品信息
  - parse_budget: 解析预算字段
  - calculate_match_score: 计算供应商与采购需求的综合匹配度

消除 supplier_screening ↔ web_search 之间的循环引用。
"""

import re
from datetime import date, datetime


# ─── 采购物品信息提取 ───

def extract_first_item(demand: dict) -> dict:
    """
    从 v2.0 采购需求单中提取第一个采购物品的信息

    v2.0 格式:
      {"采购物品清单": [{"品类": "...", "规格": "...", "数量": ..., "单位": "...", "预算": "..."}]}

    Returns:
        {"category": str, "spec": str, "qty": ..., "unit": str, "budget": str}
    """
    items = demand.get("采购物品清单", [])
    if not items or not isinstance(items, list):
        return {"category": "", "spec": "", "qty": None, "unit": "", "budget": ""}

    item = items[0] if items else {}
    return {
        "category": (item.get("品类") or "").strip(),
        "spec": (item.get("规格") or "").strip(),
        "qty": item.get("数量"),
        "unit": (item.get("单位") or "").strip(),
        "budget": (item.get("预算") or "").strip(),
    }


# ─── 预算解析（v2.0 预算字段） ───

def parse_budget(budget_str: str) -> dict:
    """
    解析 v2.0 采购需求单的预算字段

    v2.0 格式示例：
      - "≤6000 元" → {"low": None, "high": 6000, "type": "cap"}
      - "5000-8000 元" → {"low": 5000, "high": 8000, "type": "range"}
      - "不限" → {"low": None, "high": None, "type": "unlimited"}
      - "" → {"low": None, "high": None, "type": "unspecified"}
      - "≥1000 元" → {"low": 1000, "high": None, "type": "floor"}

    Returns:
        {"low": float|None, "high": float|None, "type": str}
    """
    if not budget_str or budget_str == "不限":
        return {"low": None, "high": None, "type": "unspecified" if not budget_str else "unlimited"}

    # 清理"元"后缀及空格
    cleaned = budget_str.replace("元", "").replace(" ", "").strip()

    if not cleaned:
        return {"low": None, "high": None, "type": "unspecified"}

    # "≤6000" / "<=6000"
    m = re.match(r"[≤<]\s*([\d.]+)", cleaned)
    if m:
        return {"low": None, "high": float(m.group(1)), "type": "cap"}

    # "≥1000" / ">=1000"
    m = re.match(r"[≥>]\s*([\d.]+)", cleaned)
    if m:
        return {"low": float(m.group(1)), "high": None, "type": "floor"}

    # "5000-8000"
    m = re.match(r"([\d.]+)\s*[-–—]\s*([\d.]+)", cleaned)
    if m:
        return {"low": float(m.group(1)), "high": float(m.group(2)), "type": "range"}

    # 纯数字（视为上限）
    try:
        return {"low": None, "high": float(cleaned), "type": "cap"}
    except ValueError:
        return {"low": None, "high": None, "type": "unspecified"}


# ─── 评分引擎 ───

def calculate_match_score(supplier: dict, demand: dict) -> dict:
    """
    计算供应商与 v2.0 采购需求的综合匹配度评分

    权重分配：
      - 产品匹配度: 35%
      - 价格匹配度: 25%
      - 交付匹配度: 15%
      - 质量评分:   15%
      - 信用/服务:  10%

    Args:
        supplier: 供应商数据 dict（至少含 main_products, price_range_low/high, price_unit 等字段）
        demand: v2.0 采购需求单 dict

    Returns:
        {"overall": float, "breakdown": {"product": int, "price": int, ...}}
    """
    scores = {}
    item = extract_first_item(demand)
    category = item["category"]
    spec = item["spec"]

    # 1. 产品匹配（品类关键词 + 规格匹配）
    product_match = 0
    main_products = supplier.get("main_products", "").lower()
    product_categories = supplier.get("product_categories", "").lower()
    specs = supplier.get("specifications", "").lower()

    for kw in category.split():
        kw = kw.lower()
        if kw in main_products or kw in product_categories:
            product_match += 40
        elif kw in specs:
            product_match += 25

    if spec and spec.lower() in specs:
        product_match += 30

    scores["product"] = min(product_match, 100)

    # 2. 价格匹配（解析 v2.0 预算）
    budget_info = parse_budget(item["budget"])

    if budget_info["type"] not in ("unspecified", "unlimited") and supplier.get("price_range_low") is not None:
        supplier_low = float(supplier["price_range_low"])
        supplier_high = float(supplier["price_range_high"]) if supplier.get("price_range_high") is not None else None

        if budget_info["type"] == "cap":
            # 预算上限：供应商报价 ≤ 上限得分高
            if supplier_high and supplier_high <= budget_info["high"]:
                scores["price"] = 100
            elif supplier_low <= budget_info["high"]:
                ratio = budget_info["high"] / supplier_high if supplier_high else 0.5
                scores["price"] = max(30, int(ratio * 100))
            else:
                scores["price"] = max(20, int((budget_info["high"] / supplier_low) * 100))

        elif budget_info["type"] == "floor":
            # 预算下限：供应商报价 ≥ 下限即可
            if supplier_high and supplier_high >= budget_info["low"]:
                scores["price"] = 80
            else:
                scores["price"] = 40

        elif budget_info["type"] == "range":
            if supplier_low <= budget_info["high"] and (supplier_high or 0) >= (budget_info["low"] or 0):
                # 供应商报价在预算范围内
                mid_point = (budget_info["low"] + budget_info["high"]) / 2
                sup_mid = (supplier_low + (supplier_high or supplier_low)) / 2
                if sup_mid > 0:
                    deviation = abs(sup_mid - mid_point) / mid_point
                    scores["price"] = max(40, int((1 - deviation) * 100))
                else:
                    scores["price"] = 60
            else:
                scores["price"] = 30
    else:
        scores["price"] = 50  # 无预算信息，中性分

    # 3. 交付匹配
    delivery_date = demand.get("期望送达日期", "").strip()
    supplier_delivery = supplier.get("delivery_cycle", "")

    if delivery_date and supplier_delivery:
        # 判断是否紧急（日期是否在 3 天内）
        urgent = False
        try:
            if delivery_date:
                target = datetime.strptime(delivery_date, "%Y-%m-%d").date()
                days_left = (target - date.today()).days
                urgent = days_left <= 3
        except (ValueError, TypeError):
            pass

        if urgent:
            if any(kw in supplier_delivery for kw in ["1-", "2-", "3个工作日", "次日", "现货"]):
                scores["delivery"] = 95
            elif "24小时" in supplier_delivery or "48小时" in supplier_delivery:
                scores["delivery"] = 90
            else:
                scores["delivery"] = 45
        else:
            scores["delivery"] = 75
    else:
        scores["delivery"] = 60

    # 4. 质量评分
    scores["quality"] = supplier.get("quality_rating", 50)

    # 5. 信用/服务综合
    scores["credit"] = (
        supplier.get("credit_rating", 50) * 0.6 + supplier.get("service_rating", 50) * 0.4
    )

    # 加权总分
    weights = {
        "product": 0.35,
        "price": 0.25,
        "delivery": 0.15,
        "quality": 0.15,
        "credit": 0.10,
    }

    overall = sum(scores.get(k, 0) * v for k, v in weights.items())
    overall = round(min(overall, 100), 1)

    return {
        "overall": overall,
        "breakdown": {
            "product": scores.get("product", 0),
            "price": scores.get("price", 0),
            "delivery": scores.get("delivery", 0),
            "quality": scores.get("quality", 0),
            "credit": scores.get("credit", 0),
        },
    }
