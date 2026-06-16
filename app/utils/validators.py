"""
输入校验与风控模块
负责：异常输入拒识、字段完整性校验、敏感内容过滤
"""

import re
from typing import Tuple

from config import BLACKLIST_PATTERNS


# ─── 黑名单检测 ───

def check_blacklist(user_input: str) -> Tuple[bool, str]:
    """
    检查用户输入是否命中黑名单规则

    Returns:
        (is_blocked, reason)
    """
    if not user_input or not user_input.strip():
        return True, "输入为空，请输入采购需求。"

    input_lower = user_input.strip().lower()

    # 过短输入
    if len(input_lower) < 3:
        return True, "输入过短，请提供更详细的采购需求描述。"

    # 匹配黑名单模式
    for pattern in BLACKLIST_PATTERNS:
        if re.search(pattern, user_input):
            return True, "抱歉，输入内容超出采购业务范围，请提交与采购相关的需求。"

    return False, ""


# ─── 采购需求单字段校验 ───

def validate_demand(demand: dict):
    """
    校验结构化采购需求单的字段完整性（v2.0）
    兼容旧版和新版采购需求单格式

    Returns:
        (is_valid, error_messages)
    """
    errors = []

    if not isinstance(demand, dict):
        return False, ["需求数据格式无效"]

    # ── 新版采购需求单格式 ──
    if "采购需求单编号" in demand or "采购物品清单" in demand:
        items = demand.get("采购物品清单", [])
        if not items:
            errors.append("采购物品清单不能为空")

        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"物品 {i+1} 数据格式无效")
                continue
            if not item.get("品类"):
                errors.append(f"物品 {i+1}: 缺失品类")
            if item.get("数量") == "" or item.get("数量") is None:
                errors.append(f"物品 {i+1}: 缺失数量")

        return len(errors) == 0, errors

    # ── 旧版格式兼容 ──
    if not demand.get("productCategory"):
        errors.append("缺失必填字段: productCategory")
    if demand.get("quantity") is None:
        errors.append("缺失必填字段: quantity")

    return len(errors) == 0, errors


# ─── 异常输入类型识别 ───

def classify_input(user_input: str) -> str:
    """
    识别用户输入的类型

    Returns:
        "procurement" - 采购需求
        "confirmation" - 确认/否定指令（如"需要"、"不用了"）
        "chitchat" - 闲聊
        "sensitive" - 涉密/敏感
        "unclear" - 模糊不清
    """
    input_stripped = user_input.strip()

    # 确认类指令
    confirmation_patterns = [
        r"^(需要|是|好的|可以|ok|yes|导出|生成|生成报告|确认|要|对|行|嗯|好)",
        r"^(不需要|不用|算了|不要|不了|免了|取消|否|no|拒绝)",
    ]
    for pattern in confirmation_patterns:
        if re.search(pattern, input_stripped.lower()):
            return "confirmation"

    # 涉密/敏感
    sensitive_keywords = [
        "公司机密", "内部底价", "供应商电话", "窃取", "黑客",
        "删库", "drop table", "密码", "工资", "商业秘密",
        "竞争对手", "供应商底价", "窃取.*供应商",
    ]
    for kw in sensitive_keywords:
        if kw in input_stripped:
            return "sensitive"

    # 额外敏感模式（组合关键词）
    if "删除" in input_stripped and ("数据库" in input_stripped or "供应商" in input_stripped):
        return "sensitive"

    # 闲聊
    chitchat_patterns = [
        r"^(你好|hi|hello|嗨|在吗|你是谁)",
        r"(天气|笑话|故事|诗词|唱歌|聊天|今天.*怎么样)",
        r"^(你是|你能|你会).*(吗|呢|\?|？)$",
    ]
    for pattern in chitchat_patterns:
        if re.search(pattern, input_stripped.lower()):
            return "chitchat"

    # 如果包含采购相关关键词，判定为采购需求
    procurement_keywords = [
        "买", "采购", "要", "需要", "订", "购买", "订购",
        "报价", "询价", "供应商", "交货", "预算", "规格",
        "批", "包", "件", "个", "台", "米", "kg", "吨",
    ]
    for kw in procurement_keywords:
        if kw in input_stripped:
            return "procurement"

    # 默认：模糊不清
    return "unclear"


# ─── 供应商信息校验 ───

def validate_supplier_result(supplier_data: dict) -> Tuple[bool, list[str]]:
    """
    校验供应商推荐结果的数据完整性

    Returns:
        (is_valid, warnings)
    """
    warnings = []

    if not isinstance(supplier_data, dict):
        return False, ["供应商数据格式无效"]

    name = supplier_data.get("company_name", "")
    if not name or len(name.strip()) < 2:
        warnings.append("供应商名称异常")

    quality = supplier_data.get("quality_rating", 0)
    if quality == 0 or quality is None:
        warnings.append("缺少质量评分数据")

    price_low = supplier_data.get("price_range_low")
    price_high = supplier_data.get("price_range_high")
    if price_low is None and price_high is None:
        warnings.append("缺少报价信息")

    is_valid = len(warnings) <= 2  # 允许少量警告
    return is_valid, warnings


if __name__ == "__main__":
    # 测试
    test_inputs = [
        "帮我买5000米6平方的线缆",
        "你好呀",
        "今天天气怎么样",
        "帮我窃取竞争对手的供应商底价",
        "",
        "需",
    ]
    for t in test_inputs:
        blocked, reason = check_blacklist(t)
        input_type = classify_input(t)
        print(f"输入: '{t}' → 拦截:{blocked} ({reason}) | 类型:{input_type}")
