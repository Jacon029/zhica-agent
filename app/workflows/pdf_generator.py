"""
工作流 3：PDF 报告生成 (pdf_product)
将供应商推荐结果转换为可下载的 PDF 格式报告

支持两种模式：
  - generate_pdf_report()         旧版兼容：纯文本 Markdown → PDF
  - generate_full_report()        新版：结构化需求单 + 供应商推荐表格 + 采购建议 → 完整 PDF
"""

import os
import datetime
from pathlib import Path

from config import PDF_OUTPUT_DIR


# ─── 品牌色（与 app.py BRAND Token 一致）───
_PDF_BRAND = {
    "navy":   (15, 59, 95),     # #0F3B5F 公文蓝
    "ink":    (30, 41, 59),     # #1E293B 墨色
    "slate":  (100, 116, 139),  # #64748B 石板灰
    "border": (226, 232, 240),  # #E2E8F0 边框
    "green":  (5, 150, 105),    # #059669 审批绿
    "accent": (240, 244, 248),  # #F0F4F8 浅蓝底
}

# 中文字体路径
_CJK_FONT_PATHS = [
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "C:/Windows/Fonts/msyh.ttc",        # 微软雅黑
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    "C:/Windows/Fonts/simkai.ttf",      # 楷体
]


def _load_cjk_font(pdf) -> bool:
    """加载中文字体，返回是否成功"""
    for font_path in _CJK_FONT_PATHS:
        if os.path.exists(font_path):
            try:
                pdf.add_font("CJK", "", font_path)
                pdf.add_font("CJK", "B", font_path)
                return True
            except Exception:
                continue
    return False


def _tc(r, g, b) -> tuple:
    """RGB 0-255 → fpdf2 需要的 0-255 tuple"""
    return (r, g, b)


# ═══════════════════════════════════════════
# 新版：完整结构化报告
# ═══════════════════════════════════════════

def generate_full_report(demand: dict, screening_result: dict) -> str:
    """
    生成完整采购报告 PDF（需求单 + 供应商推荐 + 采购建议）

    Args:
        demand: v2.0 结构化采购需求单
        screening_result: supplier_screening 返回的匹配结果

    Returns:
        PDF 文件路径
    """
    PDF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    item = screening_result.get("item", {})
    category = item.get("category", "采购")
    filename = f"智采Agent_采购报告_{category}_{timestamp}.pdf"
    filepath = PDF_OUTPUT_DIR / filename

    try:
        from fpdf import FPDF
        _build_full_report_fpdf(filepath, demand, screening_result)
    except ImportError:
        html_path = filepath.with_suffix(".html")
        _build_full_report_html(html_path, demand, screening_result)
        return str(html_path)

    return str(filepath)


def _build_full_report_fpdf(filepath: Path, demand: dict, result: dict):
    """fpdf2 构建完整报告"""
    from fpdf import FPDF

    pdf = FPDF(orientation="L")  # 横向 A4，给表格更多空间
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    font_ok = _load_cjk_font(pdf)

    if font_ok:
        cn, cnb = "CJK", "CJK"
    else:
        cn, cnb = "Helvetica", "Helvetica"

    width = pdf.w - 2 * pdf.l_margin  # 可用宽度
    timestamp_str = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M")

    # ── 页眉 ──
    pdf.set_font(cnb, "", 18)
    pdf.set_text_color(*_tc(*_PDF_BRAND["navy"]))
    pdf.cell(0, 12, "智采 Agent · 采购报告", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_tc(*_PDF_BRAND["navy"]))
    pdf.line(pdf.l_margin, pdf.get_y() + 2, pdf.w - pdf.r_margin, pdf.get_y() + 2)
    pdf.ln(6)

    pdf.set_font(cn, "", 8)
    pdf.set_text_color(*_tc(*_PDF_BRAND["slate"]))
    pdf.cell(0, 5, f"生成时间: {timestamp_str}    |    AI 生成 · 仅供参考 · 请以人工核对为准", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # ── §1 采购需求单 ──
    _write_section_heading(pdf, cnb, "§1 采购需求单")
    _write_demand_table(pdf, cn, cnb, demand, width)

    # ── §2 供应商推荐 ──
    _write_section_heading(pdf, cnb, "§2 供应商推荐结果")
    _write_supplier_table(pdf, cn, cnb, result, width)

    # ── §3 采购建议 ──
    top3 = result.get("top3", [])
    if top3:
        _write_section_heading(pdf, cnb, "§3 采购建议")
        _write_advice_section(pdf, cn, cnb, result, width)

    # ── 页脚 ──
    pdf.ln(8)
    pdf.set_draw_color(*_tc(*_PDF_BRAND["border"]))
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)
    pdf.set_font(cn, "", 7)
    pdf.set_text_color(*_tc(*_PDF_BRAND["slate"]))
    pdf.cell(0, 5, "本报告由 AI 自动生成，仅作为采购决策参考，不可替代人工最终审核。所有供应商信息以实际询价确认为准。", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "智采 Agent v1.0 · AI 采购专家智能体", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.output(str(filepath))


def _write_section_heading(pdf, font_name: str, text: str):
    """写入节标题"""
    pdf.ln(4)
    pdf.set_font(font_name, "", 13)
    pdf.set_text_color(*_tc(*_PDF_BRAND["navy"]))
    pdf.cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
    # 下划线
    pdf.set_draw_color(*_tc(*_PDF_BRAND["border"]))
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)


def _write_demand_table(pdf, cn, cnb, demand: dict, width: float):
    """写入采购需求单（Key-Value 表格）"""
    items = demand.get("采购物品清单", [])
    first_item = items[0] if items else {}
    delivery_date = demand.get("期望送达日期", "未指定")
    overall_reqs = demand.get("整体特殊要求", [])

    rows = [
        ("品类", first_item.get("品类", "-")),
        ("规格", first_item.get("规格", "-")),
        ("数量", f'{first_item.get("数量", "-")} {first_item.get("单位", "")}'.strip()),
        ("预算", first_item.get("预算", "-")),
        ("期望送达", delivery_date),
    ]
    if overall_reqs:
        rows.append(("整体要求", "；".join(overall_reqs)))

    # 计算列宽
    col_w = [width * 0.22, width * 0.78]
    row_h = 7

    pdf.set_font(cn, "", 9)
    for i, (label, value) in enumerate(rows):
        # 交替底色
        if i % 2 == 0:
            pdf.set_fill_color(*_tc(*_PDF_BRAND["accent"]))
        else:
            pdf.set_fill_color(255, 255, 255)

        y_before = pdf.get_y()

        # Label 列
        pdf.set_font(cnb, "", 9)
        pdf.set_text_color(*_tc(*_PDF_BRAND["navy"]))
        pdf.cell(col_w[0], row_h, label, border=0, fill=True, align="R", new_x="RIGHT", new_y="LAST")
        # 右边距
        pdf.cell(3, row_h, "", new_x="RIGHT", new_y="LAST")

        # Value 列
        pdf.set_font(cn, "", 9)
        pdf.set_text_color(*_tc(*_PDF_BRAND["ink"]))
        pdf.cell(col_w[1], row_h, str(value), border=0, fill=True, align="L", new_x="LMARGIN", new_y="NEXT")

        # 行底部分割线
        pdf.set_draw_color(*_tc(*_PDF_BRAND["border"]))
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())

    pdf.ln(2)


def _write_supplier_table(pdf, cn, cnb, result: dict, width: float):
    """写入供应商推荐横向表格（长文本自动换行）"""
    top3 = result.get("top3", [])
    if not top3:
        pdf.set_font(cn, "", 9)
        pdf.set_text_color(*_tc(*_PDF_BRAND["slate"]))
        pdf.cell(0, 7, "未找到匹配的供应商", align="C", new_x="LMARGIN", new_y="NEXT")
        return

    item = result.get("item", {})
    demand_unit = item.get("unit", "")
    qty = item.get("qty", "")

    # 列表头
    th_data = ["供应商名称", "主营产品", f"报价（{demand_unit}）" if demand_unit else "报价", "交付周期", "优势说明"]
    col_pcts = [0.22, 0.18, 0.15, 0.15, 0.30]
    col_w = [width * p for p in col_pcts]
    row_h = 6.5

    # --- 表头 ---
    pdf.set_fill_color(*_tc(*_PDF_BRAND["navy"]))
    pdf.set_text_color(255, 255, 255)
    pdf.set_font(cnb, "", 8)
    x_start = pdf.l_margin
    for j, (th, cw) in enumerate(zip(th_data, col_w)):
        pdf.set_xy(x_start + sum(col_w[:j]), pdf.get_y())
        pdf.cell(cw, row_h + 1, th, border=0, fill=True, align="C")
    pdf.ln(row_h + 1)

    # --- 数据行 ---
    for i, ranked in enumerate(top3):
        supplier = ranked["supplier"]
        source = ranked["source"]
        ecom = ranked.get("ecommerce", {})
        llm = ranked.get("llm_analysis", {})

        # 数据
        name = supplier.get("company_name", "-")
        if source == "web_search":
            main_product = ecom.get("product_name", supplier.get("main_products", "-"))
        else:
            main_product = supplier.get("main_products", "-")

        # 报价
        if source == "web_search" and ecom:
            pn = ecom.get("price_num")
            price = f"¥{pn}/{demand_unit}" if pn and demand_unit else (f"¥{pn}" if pn else "见商品页")
        else:
            low = supplier.get("price_range_low")
            high = supplier.get("price_range_high")
            unit = supplier.get("price_unit", "元")
            if low is None:
                price = "待询价"
            elif low == high:
                price = f"{low}{unit}"
            else:
                price = f"{low}-{high}{unit}"

        # 交付
        delivery = supplier.get("delivery_cycle", "-")

        # 优势说明
        if source == "web_search":
            advantage = "; ".join(filter(None, [
                f"平台{ecom.get('platform', '')}",
                f"发货{ecom.get('delivery_from', '')}" if ecom.get("delivery_from") else "",
                "包邮" if ecom.get("free_shipping") else "",
                f"好评{int(ecom['rating'])}%" if ecom.get("rating") else "",
            ])) or "见商品页"
        elif llm:
            advantage = llm.get("推荐理由", "") or "；".join(llm.get("核心匹配优势", [])[:2])
        else:
            advantage = supplier.get("notes", "-")

        # 截断过长文本（PDF 中用换行处理，这里限制初始长度）
        row_data = [name, main_product, price, delivery, advantage]

        # 计算本行所需高度（预估最长文本换行数）
        pdf.set_font(cn, "", 7.5)
        lines_needed = 1
        for j, (text, cw) in enumerate(zip(row_data, col_w)):
            text_w = pdf.get_string_width(str(text))
            line_count = max(1, int(text_w / (cw - 1.5)) + 1)
            lines_needed = max(lines_needed, line_count)
        actual_row_h = max(row_h, lines_needed * 4.5)

        # 交替底色
        if i % 2 == 0:
            pdf.set_fill_color(*_tc(*_PDF_BRAND["accent"]))
        else:
            pdf.set_fill_color(255, 255, 255)

        pdf.set_text_color(*_tc(*_PDF_BRAND["ink"]))

        # 检查是否需要换页
        if pdf.get_y() + actual_row_h > pdf.h - pdf.b_margin:
            pdf.add_page()

        y_row_start = pdf.get_y()
        x_positions = [pdf.l_margin + sum(col_w[:j]) for j in range(len(col_w))]

        # 绘制单元格 (multi_cell 自动换行)
        for j, (text, cw, x) in enumerate(zip(row_data, col_w, x_positions)):
            pdf.set_xy(x, y_row_start)
            if j == 0:
                pdf.set_font(cnb, "", 7.5)
            else:
                pdf.set_font(cn, "", 7.5)
            pdf.multi_cell(cw - 1.5, 4.5, str(text), border=0, fill=False, align="L" if j >= 3 else "L")

        # 行底部分割线
        pdf.set_y(y_row_start + actual_row_h)
        pdf.set_draw_color(*_tc(*_PDF_BRAND["border"]))
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())

    pdf.ln(3)

    # 数据来源标注
    internal_count = result.get("valid_internal_count", 0)
    web_count = result.get("web_count", 0)
    pdf.set_font(cn, "", 7)
    pdf.set_text_color(*_tc(*_PDF_BRAND["slate"]))
    pdf.cell(0, 5, f"数据来源: 内部供应商库 ({internal_count} 家) + 联网搜索 ({web_count} 家)", align="R", new_x="LMARGIN", new_y="NEXT")


def _write_advice_section(pdf, cn, cnb, result: dict, width: float):
    """写入采购建议"""
    top3 = result.get("top3", [])
    pdf.set_font(cn, "", 9)
    pdf.set_text_color(*_tc(*_PDF_BRAND["ink"]))

    if len(top3) >= 2:
        best = top3[0]
        second = top3[1]
        score_diff = best["score"]["overall"] - second["score"]["overall"]
        best_name = best["supplier"]["company_name"]

        if score_diff >= 15:
            advice = f"推荐优先联系「{best_name}」，综合条件显著领先。"
        elif score_diff >= 5:
            advice = f"「{best_name}」与「{second['supplier']['company_name']}」差距不大，建议同时询价比较后决策。"
        else:
            advice = "Top3 供应商条件接近，建议全部询价，综合商务谈判后决策。"
        pdf.multi_cell(width, 6, advice)

    # 风险提示
    if any(r["source"] == "web_search" for r in top3):
        pdf.ln(2)
        pdf.set_font(cn, "", 8)
        pdf.set_text_color(*_tc(*_PDF_BRAND["slate"]))
        pdf.multi_cell(width, 5, "⚠️ 部分推荐来自联网电商搜索，供应商信息未经验证，建议优先核实资质后再合作。")

    if result.get("insufficient"):
        pdf.set_text_color(*_tc(*_PDF_BRAND["slate"]))
        pdf.multi_cell(width, 5, f"⚠️ 当前仅匹配到 {len(top3)} 家供应商（不足3家），建议扩大搜索范围或放宽参数。")


# ═══════════════════════════════════════════
# HTML 降级方案
# ═══════════════════════════════════════════

def _build_full_report_html(filepath: Path, demand: dict, result: dict):
    """HTML 降级：完整报告"""
    from html import escape as he

    items = demand.get("采购物品清单", [])
    first_item = items[0] if items else {}
    top3 = result.get("top3", [])
    item = result.get("item", {})
    demand_unit = item.get("unit", "")
    gen_time = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M")

    # 构建需求单行
    demand_rows = ""
    for label, key in [("品类", "品类"), ("规格", "规格"), ("数量", "数量"), ("单位", "单位"), ("预算", "预算")]:
        val = first_item.get(key, "-")
        demand_rows += f"<tr><td class='dl'>{label}</td><td>{he(str(val))}</td></tr>"
    delivery = demand.get("期望送达日期", "未指定")
    demand_rows += f"<tr><td class='dl'>期望送达</td><td>{he(str(delivery))}</td></tr>"
    reqs = demand.get("整体特殊要求", [])
    if reqs:
        demand_rows += f"<tr><td class='dl'>整体要求</td><td>{he('；'.join(reqs))}</td></tr>"

    # 构建供应商行
    supplier_rows = ""
    for ranked in top3:
        s = ranked["supplier"]
        src = ranked["source"]
        ecom = ranked.get("ecommerce", {})
        name = s.get("company_name", "-")
        mp = ecom.get("product_name", s.get("main_products", "-")) if src == "web_search" else s.get("main_products", "-")
        if src == "web_search":
            pn = ecom.get("price_num")
            price = f"¥{pn}/{demand_unit}" if pn and demand_unit else (f"¥{pn}" if pn else "见商品页")
        else:
            low, high, unit = s.get("price_range_low"), s.get("price_range_high"), s.get("price_unit", "元")
            price = "待询价" if low is None else (f"{low}{unit}" if low == high else f"{low}-{high}{unit}")
        delivery = s.get("delivery_cycle", "-")
        tag = "internal" if src != "web_search" else "external"
        tag_label = "内部库" if src != "web_search" else "联网"
        supplier_rows += f"<tr><td>{he(name)} <span class='tag {tag}'>{tag_label}</span></td><td>{he(mp[:25])}</td><td>{he(str(price))}</td><td>{he(delivery[:20])}</td><td class='adv'>{he(s.get('notes', '-')[:60])}</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>智采Agent · 采购报告</title>
<style>
    body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; max-width: 900px; margin: 0 auto; padding: 30px; color: #1E293B; background: #FAFAF9; }}
    h1 {{ text-align: center; color: #0F3B5F; font-size: 1.5rem; border-bottom: 2px solid #0F3B5F; padding-bottom: 10px; }}
    h2 {{ color: #0F3B5F; font-size: 1.1rem; margin-top: 28px; border-bottom: 1px solid #E2E8F0; padding-bottom: 4px; }}
    .meta {{ text-align: center; color: #64748B; font-size: 0.75rem; margin-bottom: 20px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 0.85rem; }}
    th {{ background: #0F3B5F; color: #fff; padding: 8px 10px; font-weight: 500; font-size: 0.8rem; }}
    td {{ padding: 7px 10px; border-bottom: 1px solid #E2E8F0; }}
    tr:nth-child(even) td {{ background: #F0F4F8; }}
    .dl {{ font-weight: 600; color: #0F3B5F; width: 20%; }}
    .tag {{ font-size: 0.7rem; padding: 1px 6px; border-radius: 3px; margin-left: 4px; }}
    .tag.internal {{ background: #DBEAFE; color: #1E40AF; }}
    .tag.external {{ background: #FEF3C7; color: #92400E; }}
    .adv {{ font-size: 0.8rem; color: #64748B; }}
    .advice {{ background: #F0F4F8; padding: 12px 16px; border-radius: 6px; font-size: 0.88rem; margin: 10px 0; }}
    .warning {{ color: #92400E; font-size: 0.82rem; }}
    .footer {{ text-align: center; color: #64748B; font-size: 0.7rem; margin-top: 30px; border-top: 1px solid #E2E8F0; padding-top: 12px; }}
</style></head>
<body>
<h1>智采 Agent · 采购报告</h1>
<p class="meta">生成时间: {gen_time} &nbsp;|&nbsp; AI 生成 · 仅供参考 · 请以人工核对为准</p>
<h2>§1 采购需求单</h2>
<table>{demand_rows}</table>
<h2>§2 供应商推荐结果</h2>
<table><tr><th>供应商名称</th><th>主营产品</th><th>报价</th><th>交付周期</th><th>优势说明</th></tr>{supplier_rows}</table>
<p class="footer">本报告由 AI 自动生成 · 智采 Agent v1.0</p>
</body></html>"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)


# ═══════════════════════════════════════════
# 旧版兼容（保留给旧代码路径）
# ═══════════════════════════════════════════

def generate_pdf_report(recommendation_text: str, demand: dict = None) -> str:
    """旧版兼容：纯文本 Markdown → PDF"""
    PDF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    product = demand.get("productCategory", "采购") if demand else "采购"
    filename = f"供应商推荐报告_{product}_{timestamp}.pdf"
    filepath = PDF_OUTPUT_DIR / filename
    try:
        from fpdf import FPDF
        _generate_with_fpdf(filepath, recommendation_text, demand)
    except ImportError:
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            _generate_with_reportlab(filepath, recommendation_text, demand)
        except ImportError:
            html_path = filepath.with_suffix(".html")
            _generate_html_report(html_path, recommendation_text, demand)
            return str(html_path)
    return str(filepath)


def _generate_with_fpdf(filepath: Path, text: str, demand: dict = None):
    """使用 fpdf2 库生成 PDF"""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()

    # 添加中文字体支持
    # 尝试使用系统自带中文字体
    font_paths = [
        "C:/Windows/Fonts/simhei.ttf",     # 黑体
        "C:/Windows/Fonts/simsun.ttc",     # 宋体
        "C:/Windows/Fonts/msyh.ttc",        # 微软雅黑
        "C:/Windows/Fonts/simkai.ttf",      # 楷体
    ]

    font_loaded = False
    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                pdf.add_font("CJK", "", font_path)
                pdf.add_font("CJK", "B", font_path)
                font_loaded = True
                break
            except Exception:
                continue

    # 标题
    if font_loaded:
        pdf.set_font("CJK", "B", 18)
    else:
        pdf.set_font("Helvetica", "B", 16)

    pdf.cell(0, 15, "供应商推荐报告", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(5)

    # 生成时间
    if font_loaded:
        pdf.set_font("CJK", "", 10)
    else:
        pdf.set_font("Helvetica", "", 10)
    gen_time = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M")
    pdf.cell(0, 8, f"生成时间: {gen_time}", new_x="LMARGIN", new_y="NEXT", align="R")

    if demand:
        pdf.cell(0, 8, f"采购品类: {demand.get('productCategory', '-')}", new_x="LMARGIN", new_y="NEXT", align="R")
    pdf.ln(10)

    # 正文
    if font_loaded:
        pdf.set_font("CJK", "", 10)
    else:
        pdf.set_font("Helvetica", "", 9)

    # 简单处理 markdown 文本转为纯文本
    plain_text = _markdown_to_plain_text(text)
    for line in plain_text.split("\n"):
        line = line.strip()
        if not line:
            pdf.ln(3)
            continue

        # 标题行
        if line.startswith("## "):
            if font_loaded:
                pdf.set_font("CJK", "B", 13)
            else:
                pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 10, line[3:], new_x="LMARGIN", new_y="NEXT")
            if font_loaded:
                pdf.set_font("CJK", "", 10)
            else:
                pdf.set_font("Helvetica", "", 9)
            pdf.ln(2)
        elif line.startswith("### "):
            if font_loaded:
                pdf.set_font("CJK", "B", 11)
            else:
                pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 8, line[4:], new_x="LMARGIN", new_y="NEXT")
            if font_loaded:
                pdf.set_font("CJK", "", 10)
            else:
                pdf.set_font("Helvetica", "", 9)
            pdf.ln(1)
        else:
            # 处理长行自动换行
            pdf.multi_cell(0, 5, line)
            pdf.ln(0)

    # 免责声明
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 8, "AI生成，仅供参考，请以人工核对为准", new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.output(str(filepath))


def _generate_with_reportlab(filepath: Path, text: str, demand: dict = None):
    """使用 reportlab 库生成 PDF (备选方案)"""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    doc = SimpleDocTemplate(str(filepath), pagesize=A4)
    styles = getSampleStyleSheet()

    story = []

    # 标题
    title_style = ParagraphStyle(
        "CustomTitle", parent=styles["Heading1"], fontSize=18, spaceAfter=10
    )
    story.append(Paragraph("供应商推荐报告", title_style))
    story.append(Spacer(1, 10))

    # 处理文本
    plain_text = _markdown_to_plain_text(text)
    for line in plain_text.split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 5))
            continue
        if line.startswith("## "):
            story.append(Paragraph(line[3:], styles["Heading2"]))
        elif line.startswith("### "):
            story.append(Paragraph(line[4:], styles["Heading3"]))
        else:
            story.append(Paragraph(line, styles["Normal"]))

    story.append(Spacer(1, 20))
    story.append(Paragraph(
        "<i>AI生成，仅供参考，请以人工核对为准</i>", styles["Normal"]
    ))

    doc.build(story)


def _generate_html_report(filepath: Path, text: str, demand: dict = None):
    """降级方案：生成 HTML 格式报告"""
    import html

    gen_time = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M")

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>供应商推荐报告</title>
    <style>
        body {{ font-family: "Microsoft YaHei", "SimHei", sans-serif; max-width: 800px; margin: 0 auto; padding: 30px; color: #333; }}
        h1 {{ text-align: center; color: #1a5276; border-bottom: 2px solid #2980b9; padding-bottom: 10px; }}
        h2 {{ color: #2c3e50; margin-top: 25px; }}
        h3 {{ color: #2980b9; }}
        table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
        th {{ background-color: #2980b9; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f9ff; }}
        .meta {{ text-align: right; color: #888; font-size: 14px; }}
        .disclaimer {{ text-align: center; color: #999; font-size: 12px; margin-top: 30px; border-top: 1px solid #eee; padding-top: 15px; }}
        .warning {{ color: #e67e22; font-weight: bold; }}
        .insufficient {{ color: #c0392b; }}
    </style>
</head>
<body>
    <h1>供应商推荐报告</h1>
    <p class="meta">生成时间: {gen_time}</p>
    {text}
    <p class="disclaimer">AI生成，仅供参考，请以人工核对为准</p>
</body>
</html>"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)


def _markdown_to_plain_text(md_text: str) -> str:
    """简单的 Markdown 转纯文本"""
    import re

    text = md_text
    # 移除加粗标记
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    # 移除斜体标记
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    # 移除行内代码标记
    text = re.sub(r"`(.*?)`", r"\1", text)
    # 移除链接但保留文字
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    # 移除 emoji（简单处理）
    text = re.sub(r"[\U0001F300-\U0001F9FF]", "", text)

    return text


if __name__ == "__main__":
    test_text = """## 采购需求确认
| 产品 | 线缆 |
|------|------|
| 规格 | 6平方 |

## 供应商推荐 Top3
### 推荐 1：华中线缆科技有限公司 [内部库]
| 维度 | 详情 |
|------|------|
| 主营产品 | 电力电缆 |

### 推荐 2：广州恒通线缆有限公司 [内部库]
| 维度 | 详情 |
|------|------|
| 主营产品 | 电力电缆, 通信电缆 |
"""

    path = generate_pdf_report(test_text, {"productCategory": "线缆"})
    print(f"报告已生成: {path}")
