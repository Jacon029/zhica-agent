"""
工作流 1：采购需求单生成 (procurement_demand_structuring)
将非结构化采购需求 → 标准采购需求单 JSON（v2.0 专业版）

Prompt 来源：src/prompts/demand_structuring_system.md
"""

import json
import re
from datetime import date, timedelta
from typing import Optional, Tuple

from openai import OpenAI

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_TEMPERATURE,
    DEMAND_SCHEMA,
)
from utils.validators import check_blacklist

# prompt 模板：src/prompts/demand_structuring_system.md
from src.prompts import load_prompt

# ─── 流水号计数器（内存中，进程重启后重置） ───
_seq_counter: dict = {}  # { "YYYYMMDD": int }


def _generate_order_number() -> str:
    """生成采购需求单编号：PR-YYYYMMDD-XXX"""
    today = date.today().strftime("%Y%m%d")
    if today not in _seq_counter:
        _seq_counter[today] = 0
    _seq_counter[today] += 1
    return f"PR-{today}-{_seq_counter[today]:03d}"


# ─── 异常输出识别 ───

_EXCEPTION_PATTERNS = [
    (re.compile(r"需求信息有误"), "error_conflict"),
    (re.compile(r"需求模糊"), "error_vague"),
]

_EXCEPTION_MAP = {
    "需求信息有误，请复核": "error_conflict",
    "需求模糊，按照优先级进行需求补充": "error_vague",
}


def _is_exception_response(text: str) -> Tuple[bool, Optional[str]]:
    """检测 LLM 是否返回异常文本而非 JSON"""
    text_stripped = text.strip()
    if text_stripped in _EXCEPTION_MAP:
        return True, _EXCEPTION_MAP[text_stripped]
    for pattern, error_type in _EXCEPTION_PATTERNS:
        if pattern.search(text_stripped):
            return True, error_type
    return False, None


# ─── 核心字段优先级 ───

_FIELD_PRIORITY = [
    ("品类", "品类"),
    ("规格", "规格/型号"),
    ("数量", "数量"),
    ("预算", "预算"),
    ("期望送达日期", "期望送达日期"),
    ("所选择的供应商", "供应商"),
    ("整体特殊要求", "整体特殊要求"),
]


def _generate_supplement_suggestions(demand: dict) -> Optional[str]:
    """
    检查采购需求单中缺失的核心字段，按优先级生成补充建议。
    规则：
    - 单个缺失：缺少[信息名称]信息，请提供
    - 多个缺失：缺少[信息1]、[信息2]和[信息3]信息，请提供
    - 完整：返回 None
    """
    missing = []

    # 检查采购物品清单中的第一个物品
    items = demand.get("采购物品清单", [])
    if not items or not isinstance(items, list):
        return "缺少品类、数量和规格信息，请提供完整的采购需求"

    item = items[0] if items else {}

    for field_key, field_label in _FIELD_PRIORITY:
        if field_key == "整体特殊要求":
            val = demand.get(field_key, [])
            if not val:
                missing.append(field_label)
        elif field_key == "所选择的供应商":
            val = demand.get(field_key, "")
            if not val:
                missing.append(field_label)
        elif field_key == "期望送达日期":
            val = demand.get(field_key, "")
            if not val:
                missing.append(field_label)
        else:
            val = item.get(field_key)
            if val is None or val == "":
                missing.append(field_label)

    if not missing:
        return None

    # 过滤：只关注前5个核心字段（品类/规格/数量/预算/日期）
    core_missing = [m for m in missing if m in [
        "品类", "规格/型号", "数量", "预算", "期望送达日期"
    ]]

    if not core_missing:
        return None

    if len(core_missing) == 1:
        return f"缺少{core_missing[0]}信息，请提供"
    else:
        # 多个：用 、 和 连接
        *rest, last = core_missing
        return f"缺少{'、'.join(rest)}和{last}信息，请提供"


# ─── 主函数 ───

def structurize_demand(raw_demand: str) -> dict:
    """
    将非结构化采购需求转换为标准采购需求单 JSON

    Args:
        raw_demand: 用户输入的原始采购需求文本

    Returns:
        {
            "success": bool,
            "data": dict | None,        # 成功时的采购需求单
            "error": bool,
            "message": str,             # 错误/异常信息
            "suggestion": str | None,   # 信息补充建议
            "blocked": bool,
        }
    """
    # Step 0: 输入校验
    is_blocked, reason = check_blacklist(raw_demand)
    if is_blocked:
        return {"success": False, "data": None, "error": True, "message": reason, "suggestion": None, "blocked": True}

    if not DEEPSEEK_API_KEY:
        return {"success": False, "data": None, "error": True, "message": "未配置 DEEPSEEK_API_KEY，请在 .env 文件中设置", "suggestion": None, "blocked": False}

    # 准备 prompt（注入当前日期）
    today_str = date.today().isoformat()
    system_prompt = load_prompt("demand_structuring_system.md", current_date=today_str)

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )

    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            max_tokens=DEEPSEEK_MAX_TOKENS,
            temperature=DEEPSEEK_TEMPERATURE,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请根据采购需求生成标准采购需求单：\n\n{raw_demand}"},
            ],
        )

        content = response.choices[0].message.content.strip()

        # ── 检查是否异常响应 ──
        is_exception, exception_type = _is_exception_response(content)
        if is_exception:
            if exception_type == "error_conflict":
                return {
                    "success": False, "data": None, "error": True,
                    "message": "需求信息有误，请复核。请确认采购需求描述是否准确。",
                    "suggestion": None, "blocked": False,
                }
            elif exception_type == "error_vague":
                return {
                    "success": False, "data": None, "error": True,
                    "message": "需求模糊，请补充更多采购细节（品类、规格、数量等）。",
                    "suggestion": "请提供：品类、规格/型号、数量、预算、期望送达日期",
                    "blocked": False,
                }

        # ── 解析 JSON ──
        # 清理可能的 markdown 代码块标记
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        demand = json.loads(content)

        # ── 修复/补充编号 ──
        order_no = demand.get("采购需求单编号", "")
        if not order_no or order_no == "PR-YYYYMMDD-XXX":
            demand["采购需求单编号"] = _generate_order_number()

        # ── 标准化字段 ──
        demand.setdefault("所选择的供应商", "")
        demand.setdefault("期望送达日期", "")
        demand.setdefault("整体特殊要求", [])

        items = demand.get("采购物品清单", [])
        if not isinstance(items, list):
            demand["采购物品清单"] = []
            items = []

        for i, item in enumerate(items):
            if not isinstance(item, dict):
                items[i] = {}
                continue
            item.setdefault("序号", i + 1)
            item.setdefault("品类", "")
            item.setdefault("数量", "")
            item.setdefault("单位", "")
            item.setdefault("规格", "")
            item.setdefault("预算", "")
            item.setdefault("物品特殊要求", [])

        # ── 生成补充建议 ──
        suggestion = _generate_supplement_suggestions(demand)

        return {
            "success": True,
            "data": demand,
            "error": False,
            "message": "",
            "suggestion": suggestion,
            "blocked": False,
        }

    except json.JSONDecodeError as e:
        return {
            "success": False, "data": None, "error": True,
            "message": f"结构化失败：LLM 输出无法解析为 JSON",
            "suggestion": None, "blocked": False,
            "raw_output": content[:500] if 'content' in dir() else "",
        }
    except Exception as e:
        return {
            "success": False, "data": None, "error": True,
            "message": f"结构化失败：{str(e)}",
            "suggestion": None, "blocked": False,
        }


# ─── 格式化输出 ───

def _get_field_extraction_status(demand: dict) -> dict:
    """
    检查采购需求单各字段的提取状态

    Returns:
        {"extracted": [...], "missing": [...]}
    """
    items = demand.get("采购物品清单", [])
    item = items[0] if items else {}

    checks = [
        ("品类", item.get("品类") not in (None, "")),
        ("规格", item.get("规格") not in (None, "")),
        ("数量", item.get("数量") not in (None, "", 0)),
        ("单位", item.get("单位") not in (None, "")),
        ("预算", item.get("预算") not in (None, "")),
        ("期望送达日期", demand.get("期望送达日期") not in (None, "")),
    ]

    extracted = [name for name, ok in checks if ok]
    missing = [name for name, ok in checks if not ok]
    return {"extracted": extracted, "missing": missing}


def format_demand_order(demand: dict) -> str:
    """
    将采购需求单 JSON 格式化为可读的 Markdown 展示

    Args:
        demand: 采购需求单 dict

    Returns:
        格式化的 Markdown 字符串
    """
    lines = []
    lines.append(f"## 采购需求单")
    lines.append("")

    # 推理说明
    status = _get_field_extraction_status(demand)
    lines.append(f"> 已调用采购需求结构化工具，从非结构化输入中提取关键字段。"
                 f"成功提取 {len(status['extracted'])} 项：{'、'.join(status['extracted'])}。"
                 + (f" 缺失 {len(status['missing'])} 项：{'、'.join(status['missing'])}。" if status['missing'] else " 字段完整。"))
    lines.append("")

    lines.append(f"**编号**: `{demand.get('采购需求单编号', '-')}`")
    lines.append(f"**期望送达日期**: {demand.get('期望送达日期', '未指定')}")
    lines.append(f"**供应商选择**: {demand.get('所选择的供应商', '待匹配')}")
    lines.append("")

    # 整体特殊要求
    overall_reqs = demand.get("整体特殊要求", [])
    if overall_reqs:
        lines.append("**整体特殊要求**:")
        for req in overall_reqs:
            lines.append(f"- {req}")
        lines.append("")

    # 采购物品清单
    items = demand.get("采购物品清单", [])
    if items:
        lines.append("### 采购物品清单")
        lines.append("")
        lines.append("| 序号 | 品类 | 规格 | 数量 | 单位 | 预算 | 特殊要求 |")
        lines.append("|------|------|------|------|------|------|----------|")
        for item in items:
            seq = item.get("序号", "-")
            category = item.get("品类", "-") or "-"
            spec = item.get("规格", "-") or "-"
            qty = item.get("数量", "-")
            unit = item.get("单位", "-") or "-"
            budget = item.get("预算", "-") or "-"
            reqs = item.get("物品特殊要求", [])
            reqs_str = "; ".join(reqs) if reqs else "-"
            lines.append(f"| {seq} | {category} | {spec} | {qty} | {unit} | {budget} | {reqs_str} |")
        lines.append("")

    return "\n".join(lines)


def format_missing_fields_prompt(demand: dict) -> Optional[str]:
    """
    兼容旧接口：返回补充建议字符串
    """
    return _generate_supplement_suggestions(demand)


if __name__ == "__main__":
    # 快速测试
    test_inputs = [
        "帮我买5000米6平方的线缆，预算60元/米，3天内交货到长沙。",
        "车间要一批6205轴承，尽快交货",
        "办公室要10包A4纸，70g的，下周前要用。",
        "买一些办公用品",
    ]
    for t in test_inputs:
        print(f"\n{'='*60}")
        print(f"输入: {t}")
        result = structurize_demand(t)
        if result["success"]:
            print(format_demand_order(result["data"]))
            if result.get("suggestion"):
                print(f"💡 补充建议: {result['suggestion']}")
        else:
            print(f"❌ {result['message']}")
            if result.get("suggestion"):
                print(f"💡 {result['suggestion']}")
