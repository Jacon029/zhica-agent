"""
智采-Agent (Smart Procurement Agent) - 主入口
Streamlit 前端 + 工作流编排

基于 Coze 版 Agent 完整复刻
"""

import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.absolute()))

import streamlit as st

from config import DEEPSEEK_API_KEY
from db.database import init_database, save_procurement_history
from workflows.demand_structuring import structurize_demand, format_missing_fields_prompt, format_demand_order
from workflows.supplier_screening import screen_suppliers, format_recommendation_report
from workflows.pdf_generator import generate_pdf_report, generate_full_report
from utils.validators import check_blacklist, classify_input


# ─── 供应商库缓存 ───

@st.cache_data(ttl=3600)
def supplier_list_cache() -> list:
    """缓存供应商列表（1小时刷新），避免每次请求都查库"""
    from db.database import get_all_suppliers
    return get_all_suppliers()


# ─── 页面配置 ───
st.set_page_config(
    page_title="智采-Agent | 企业采购智能助手",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─── 初始化 ───
@st.cache_resource
def setup_database():
    """初始化数据库（仅首次运行）"""
    init_database()


def init_session_state():
    """初始化会话状态"""
    defaults = {
        "messages": [],
        "current_stage": "idle",  # idle | structuring | screening | pdf_ready | done
        "raw_demand": None,
        "structured_demand": None,
        "screening_result": None,
        "report_text": None,
        "pdf_path": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ─── 品牌设计 Token ───
BRAND = {
    "paper":     "#FAFAF9",  # 纸白 — 主背景
    "ink":       "#1E293B",  # 墨色 — 正文
    "navy":      "#0F3B5F",  # 公文蓝 — 标题/品牌
    "link":      "#2563EB",  # 链接蓝 — 交互元素
    "slate":     "#64748B",  # 石板灰 — 辅助文字
    "green":     "#059669",  # 审批绿 — 完成/通过
    "border":    "#E2E8F0",  # 边框 — 卡片/分割线
    "card_bg":   "#FFFFFF",  # 卡片背景
    "accent_1":  "#F0F4F8",  # 浅蓝底 — 阶段卡片背景
    "warning":   "#D97706",  # 琥珀 — 提示/风险
}
BRAND_CSS = f"""
<style>
    /* ── 字体与基础 ── */
    * {{
        font-family: 'Microsoft YaHei', 'PingFang SC', -apple-system, 'Noto Sans SC', sans-serif;
    }}

    :root {{
        --paper: {BRAND['paper']};
        --ink: {BRAND['ink']};
        --navy: {BRAND['navy']};
        --link: {BRAND['link']};
        --slate: {BRAND['slate']};
        --green: {BRAND['green']};
        --border: {BRAND['border']};
        --card: {BRAND['card_bg']};
        --accent: {BRAND['accent_1']};
    }}

    /* ── 全局背景 ── */
    .stApp {{
        background: {BRAND['paper']};
    }}

    /* ── 标题区 ── */
    .console-header {{
        text-align: center;
        padding: 1.2rem 0 0.4rem 0;
        border-bottom: 1px solid {BRAND['border']};
        margin-bottom: 1rem;
    }}
    .console-header .brand {{
        font-size: 1.6rem;
        font-weight: 700;
        color: {BRAND['navy']};
        letter-spacing: 0.02em;
        margin: 0;
    }}
    .console-header .subtitle {{
        font-size: 0.8rem;
        color: {BRAND['slate']};
        font-weight: 400;
        margin-top: 0.15rem;
    }}

    /* ── 三阶段进度条 ── */
    .workflow-stages {{
        display: flex;
        justify-content: center;
        gap: 0;
        margin: 0.8rem 0 1.4rem 0;
        padding: 0;
        list-style: none;
    }}
    .workflow-stages .stage-item {{
        display: flex;
        align-items: center;
        gap: 0;
    }}
    .workflow-stages .stage-card {{
        display: flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.5rem 1rem;
        border-radius: 6px;
        background: {BRAND['card_bg']};
        border: 1px solid {BRAND['border']};
        font-size: 0.82rem;
        font-weight: 500;
        color: {BRAND['slate']};
        white-space: nowrap;
        transition: all 0.2s ease;
    }}
    .workflow-stages .stage-card .step-num {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 22px;
        height: 22px;
        border-radius: 4px;
        background: {BRAND['accent_1']};
        color: {BRAND['slate']};
        font-size: 0.72rem;
        font-weight: 700;
    }}
    .workflow-stages .stage-card.active {{
        background: {BRAND['navy']};
        color: #FFFFFF;
        border-color: {BRAND['navy']};
        box-shadow: 0 2px 8px rgba(15,59,95,0.18);
    }}
    .workflow-stages .stage-card.active .step-num {{
        background: rgba(255,255,255,0.2);
        color: #FFFFFF;
    }}
    .workflow-stages .stage-card.done {{
        background: {BRAND['accent_1']};
        color: {BRAND['green']};
        border-color: {BRAND['green']};
    }}
    .workflow-stages .stage-card.done .step-num {{
        background: {BRAND['green']};
        color: #FFFFFF;
    }}
    .workflow-stages .stage-arrow {{
        color: {BRAND['border']};
        font-size: 1rem;
        margin: 0 -2px;
        z-index: 1;
    }}
    .workflow-stages .stage-arrow.passed {{
        color: {BRAND['green']};
    }}

    /* ── 内容卡片 ── */
    .procurement-card {{
        background: {BRAND['card_bg']};
        border: 1px solid {BRAND['border']};
        border-left: 3px solid {BRAND['slate']};
        border-radius: 6px;
        padding: 1.2rem 1.4rem;
        margin: 0.8rem 0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    .procurement-card.active-card {{
        border-left-color: {BRAND['navy']};
    }}
    .procurement-card.success-card {{
        border-left-color: {BRAND['green']};
    }}
    .procurement-card .card-label {{
        font-size: 0.7rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: {BRAND['slate']};
        margin-bottom: 0.6rem;
    }}

    /* ── 需求单表格 ── */
    .demand-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.85rem;
    }}
    .demand-table th {{
        background: {BRAND['accent_1']};
        color: {BRAND['navy']};
        font-weight: 600;
        padding: 0.5rem 0.7rem;
        text-align: left;
        border-bottom: 2px solid {BRAND['border']};
        font-size: 0.78rem;
    }}
    .demand-table td {{
        padding: 0.45rem 0.7rem;
        border-bottom: 1px solid {BRAND['border']};
        color: {BRAND['ink']};
    }}
    .demand-table .field-extracted {{
        color: {BRAND['green']};
        font-weight: 500;
    }}
    .demand-table .field-missing {{
        color: {BRAND['slate']};
        font-style: italic;
    }}

    /* ── 供应商推荐卡片 ── */
    .supplier-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.83rem;
        margin: 0.6rem 0;
    }}
    .supplier-table th {{
        background: {BRAND['navy']};
        color: #FFFFFF;
        font-weight: 500;
        padding: 0.5rem 0.6rem;
        text-align: left;
        font-size: 0.76rem;
        letter-spacing: 0.03em;
    }}
    .supplier-table td {{
        padding: 0.5rem 0.6rem;
        border-bottom: 1px solid {BRAND['border']};
        color: {BRAND['ink']};
        vertical-align: top;
    }}
    .supplier-table tr:hover td {{
        background: {BRAND['accent_1']};
    }}
    .supplier-table .source-tag {{
        display: inline-block;
        font-size: 0.65rem;
        padding: 1px 6px;
        border-radius: 3px;
        font-weight: 500;
    }}
    .source-tag.internal {{
        background: #DBEAFE;
        color: #1E40AF;
    }}
    .source-tag.external {{
        background: #FEF3C7;
        color: #92400E;
    }}

    /* ── 追问提示 ── */
    .inquiry-banner {{
        background: #FFFBEB;
        border: 1px solid #FCD34D;
        border-left: 3px solid {BRAND['warning']};
        border-radius: 4px;
        padding: 0.7rem 1rem;
        font-size: 0.82rem;
        color: #92400E;
        margin: 0.6rem 0;
    }}

    /* ── 采购建议 ── */
    .advice-box {{
        background: {BRAND['accent_1']};
        border-radius: 6px;
        padding: 1rem 1.2rem;
        margin: 1rem 0;
        font-size: 0.85rem;
        color: {BRAND['ink']};
    }}
    .advice-box strong {{
        color: {BRAND['navy']};
    }}

    /* ── PDF 下载区 ── */
    .pdf-action-bar {{
        display: flex;
        align-items: center;
        gap: 1rem;
        background: {BRAND['accent_1']};
        border-radius: 6px;
        padding: 1rem 1.4rem;
        margin: 0.8rem 0;
    }}

    /* ── 签章式免责 ── */
    .seal-disclaimer {{
        text-align: center;
        margin-top: 1.5rem;
        padding: 1rem;
        border-top: 1px solid {BRAND['border']};
    }}
    .seal-disclaimer .seal-box {{
        display: inline-block;
        border: 1.5px solid {BRAND['slate']};
        border-radius: 4px;
        padding: 0.4rem 1rem;
        color: {BRAND['slate']};
        font-size: 0.7rem;
        letter-spacing: 0.05em;
        transform: rotate(-2deg);
        opacity: 0.7;
    }}

    /* ── 侧边栏 ── */
    [data-testid="stSidebar"] {{
        background: {BRAND['card_bg']};
        border-right: 1px solid {BRAND['border']};
    }}
    [data-testid="stSidebar"] .stMarkdown h2 {{
        color: {BRAND['navy']};
        font-size: 1.1rem;
        font-weight: 600;
    }}
    [data-testid="stSidebar"] .stMarkdown h3 {{
        color: {BRAND['slate']};
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }}

    /* ── 聊天输入框 ── */
    [data-testid="stChatInput"] textarea {{
        border: 1px solid {BRAND['border']} !important;
        border-radius: 8px !important;
        background: {BRAND['card_bg']} !important;
        font-size: 0.9rem !important;
    }}
    [data-testid="stChatInput"] textarea:focus {{
        border-color: {BRAND['navy']} !important;
        box-shadow: 0 0 0 2px rgba(15,59,95,0.1) !important;
    }}

    /* ── 全局按钮 ── */
    .stButton > button {{
        border-radius: 6px !important;
        font-weight: 500 !important;
        font-size: 0.84rem !important;
        transition: all 0.15s ease;
    }}
    .stButton > button[kind="primary"] {{
        background: {BRAND['navy']} !important;
        border-color: {BRAND['navy']} !important;
    }}

    /* ── Streamlit 原生表格覆盖 ── */
    .stMarkdown table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.83rem;
    }}
    .stMarkdown table th {{
        background: {BRAND['navy']} !important;
        color: #FFFFFF !important;
        font-weight: 500 !important;
        padding: 0.5rem 0.6rem !important;
        font-size: 0.76rem;
        letter-spacing: 0.03em;
        border: none !important;
    }}
    .stMarkdown table td {{
        padding: 0.5rem 0.6rem !important;
        border-bottom: 1px solid {BRAND['border']} !important;
        color: {BRAND['ink']};
        vertical-align: top;
    }}
    .stMarkdown table tr:hover td {{
        background: {BRAND['accent_1']};
    }}

    /* ── 用户消息气泡 ── */
    .user-msg-bubble {{
        background: {BRAND['accent_1']};
        border-radius: 8px;
        padding: 0.7rem 1rem;
        margin: 0.5rem 0;
        font-size: 0.9rem;
        color: {BRAND['ink']};
        border-left: 3px solid {BRAND['link']};
    }}
</style>
"""

def apply_custom_css():
    st.markdown(BRAND_CSS, unsafe_allow_html=True)


# ─── 侧边栏 ───
def render_sidebar():
    with st.sidebar:
        st.markdown(f"""
        <div style="text-align: center; padding: 1rem 0 0.5rem 0;">
            <div style="font-size: 1.2rem; font-weight: 700; color: {BRAND['navy']};">🏢 智采 Agent</div>
            <div style="font-size: 0.72rem; color: {BRAND['slate']}; margin-top: 0.15rem;">AI 采购专家智能体 v1.0</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("---")

        # API Key 从 .env 自动加载，UI 中不显示

        # 功能说明
        st.markdown(f'<p style="font-size:0.72rem;font-weight:600;color:{BRAND["slate"]};letter-spacing:0.05em;text-transform:uppercase;margin-bottom:0.3rem;">📋 功能</p>', unsafe_allow_html=True)
        st.markdown(f"""
        <div style="font-size: 0.8rem; line-height: 1.7; color: {BRAND['ink']};">
            <div style="margin-bottom:0.3rem;"><span style="color:{BRAND['navy']};font-weight:600;">01</span>&nbsp; 需求结构化 — 非结构化输入 → 标准采购单</div>
            <div style="margin-bottom:0.3rem;"><span style="color:{BRAND['navy']};font-weight:600;">02</span>&nbsp; 供应商匹配 — 内部供应商库 + 联网搜索</div>
            <div><span style="color:{BRAND['navy']};font-weight:600;">03</span>&nbsp; PDF 报告 — 一键导出推荐报告</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("---")

        # 供应商库信息
        from db.database import get_all_suppliers
        suppliers = get_all_suppliers()
        st.markdown(f'<p style="font-size:0.72rem;font-weight:600;color:{BRAND["slate"]};letter-spacing:0.05em;text-transform:uppercase;margin-bottom:0.3rem;">📦 供应商库</p>', unsafe_allow_html=True)
        st.metric("库内供应商", f"{len(suppliers)} 家")
        with st.expander("查看供应商列表"):
            for s in suppliers:
                st.markdown(f"- **{s['company_name']}** | {s['product_categories']}")

        st.markdown("---")

        # 重置按钮
        if st.button("🔄 重置对话", use_container_width=True):
            st.session_state.clear()
            init_session_state()
            st.rerun()

        st.markdown(
            f'<div style="font-size:0.68rem;color:{BRAND["slate"]};text-align:center;margin-top:1rem;">AI 生成，仅供参考<br>请以人工核对为准</div>',
            unsafe_allow_html=True,
        )


# ─── 阶段指示器（三阶段工作流卡片） ───
def render_stage_indicator():
    stages = [
        ("01", "需求结构化", "structuring"),
        ("02", "供应商匹配", "screening"),
        ("03", "报告生成", "pdf_ready"),
    ]
    current = st.session_state.get("current_stage", "idle")
    stage_order = ["idle", "structuring", "screening", "pdf_ready", "done"]
    current_idx = stage_order.index(current) if current in stage_order else 0

    html = '<div class="workflow-stages">'
    for i, (num, label, stage_id) in enumerate(stages):
        stage_idx = stage_order.index(stage_id)
        is_done = stage_idx < current_idx
        is_current = stage_idx == current_idx

        css_class = ""
        if is_current:
            css_class = "active"
        elif is_done:
            css_class = "done"

        html += f'<div class="stage-item">'
        html += f'<a id="stage-{stage_id}" style="display:block;height:1px;"></a>'
        html += f'<div class="stage-card {css_class}">'
        html += f'<span class="step-num">{num}</span>'
        html += f'<span>{label}</span>'
        html += f'</div>'

        # 箭头分隔（最后一个不需要）
        if i < len(stages) - 1:
            arrow_class = "passed" if stage_idx < current_idx else ""
            html += f'<span class="stage-arrow {arrow_class}">→</span>'
        html += '</div>'

    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


# ─── 消息处理 ───
def handle_user_input(user_input: str) -> dict:
    """
    处理用户输入的核心编排逻辑
    对应 Coze 版 Agent 的完整工作流

    Returns:
        {"content": str, "stage": str | None}
    """
    # Step 0: 输入分类与风控
    input_type = classify_input(user_input)

    # 闲聊拒识
    if input_type == "chitchat":
        return {"content": "👋 您好！我是企业采购智能助手，专注于采购需求处理和供应商推荐。请直接描述您的采购需求，我会帮您完成供应商匹配。", "stage": None}

    # 涉密拒识
    if input_type == "sensitive":
        return {"content": "⚠️ 检测到输入包含敏感内容，已终止处理。请提交合规的采购需求。如有疑问，请联系采购负责人。", "stage": None}

    # 模糊不清
    if input_type == "unclear":
        return {"content": "🤔 未能识别到明确的采购需求。请描述您需要采购的产品、规格、数量等信息，例如：\n\n> \"帮我买5000米6平方的线缆，预算60元/米，3天内交货到长沙。\"", "stage": None}

    # ─── 确认类指令处理 ───
    if input_type == "confirmation":
        # PDF 生成确认
        if any(kw in user_input for kw in ["需要", "是", "好的", "可以", "导出", "生成", "生成报告", "确认", "要", "对", "行", "嗯", "好"]):
            if st.session_state.get("screening_result"):
                st.session_state["current_stage"] = "pdf_ready"
                return {"content": "__GENERATE_PDF__", "stage": "pdf_ready"}
            else:
                return {"content": "请先提交采购需求，我为您匹配供应商后再生成报告。", "stage": None}
        # 拒绝 PDF
        elif any(kw in user_input for kw in ["不需要", "不用", "算了", "不要", "不了", "免了", "取消"]):
            st.session_state["current_stage"] = "done"
            return {"content": "好的，感谢使用智采-Agent。如有新的采购需求，随时找我。祝您工作顺利！🙏", "stage": "done"}

    # ─── 采购需求处理 ───
    # Step 1: 黑名单检查
    blocked, reason = check_blacklist(user_input)
    if blocked:
        return {"content": f"⚠️ {reason}", "stage": None}

    # Step 2: 采购需求单生成（调用 LLM）
    st.session_state["current_stage"] = "structuring"
    st.session_state["raw_demand"] = user_input

    result = structurize_demand(user_input)

    # ── 处理异常/错误 ──
    if result.get("blocked"):
        return {"content": f"⚠️ {result['message']}", "stage": None}

    if result.get("error"):
        suggestion = result.get("suggestion", "")
        msg = f"❌ {result['message']}"
        if suggestion:
            msg += f"\n\n💡 {suggestion}"
        return {"content": msg, "stage": "structuring"}

    # ── 成功：展示采购需求单 ──
    demand = result["data"]
    st.session_state["structured_demand"] = demand

    # 格式1: 表格展示（含字段提取状态）
    formatted = format_demand_order(demand)

    # 保存 JSON 供 expander 使用
    st.session_state["last_demand_json"] = json.dumps(demand, ensure_ascii=False, indent=2)

    # 分隔标记：表格后面插入 expander
    formatted += "\n\n<!--SPLIT-->"

    # 补充建议
    suggestion = result.get("suggestion")
    if suggestion:
        formatted += f"\n\n⚠️ {suggestion}"

    # ── 判断是否可以进入供应商筛选 ──
    items = demand.get("采购物品清单", [])
    first_item = items[0] if items else {}
    has_basics = bool(first_item.get("品类")) and (first_item.get("数量") not in (None, "", 0))

    if has_basics:
        item_category = first_item.get("品类", "")
        item_qty = first_item.get("数量", "")
        item_budget = first_item.get("预算", "")

        # 步骤推理
        formatted += f"\n\n---\n\n> 需求结构化完成。接下来调用智能供应商筛选工具，"
        formatted += f"参数：productCategory=\"{item_category}\"、quantity={item_qty}、"
        formatted += f"budget=\"{item_budget or '未指定'}\"。"
        formatted += f"先在内部供应商库（{len(supplier_list_cache())}家）中匹配..."

        st.session_state["current_stage"] = "screening"
        try:
            screening_result = screen_suppliers(demand)
            st.session_state["screening_result"] = screening_result

            # 匹配诊断
            internal_count = screening_result.get("internal_count", 0)
            valid_count = screening_result.get("valid_internal_count", 0)
            web_count = screening_result.get("web_count", 0)
            need_web = screening_result.get("need_web_search", False)

            diagnostic = ""
            if valid_count == 0 and internal_count > 0:
                diagnostic = (f"内部库中有 {internal_count} 家供应商，但品类不匹配或硬性门槛未通过，"
                              f"判定为库内匹配不足。")
            elif need_web:
                diagnostic = screening_result.get("search_reason", "库内匹配不足")
            else:
                diagnostic = screening_result.get("search_reason", "库内匹配充足")

            if need_web and web_count == 0:
                diagnostic += " 已执行联网搜索进行补充，未找到匹配结果。"
            elif web_count > 0:
                diagnostic += f" 已执行联网搜索补充 {web_count} 家外部供应商。"

            formatted += f"\n\n> {diagnostic}"

            # 追加供应商推荐报告
            report = format_recommendation_report(screening_result)
            st.session_state["report_text"] = report
            formatted += f"\n\n---\n\n{report}"
            formatted += "\n\n---\n\n*以上是本次的供应商推荐结果。点击下方按钮即可生成 PDF 报告。*"
            formatted += "\n\n🤖 *AI生成，仅供参考，请以人工核对为准*"

            st.session_state["current_stage"] = "pdf_ready"

        except Exception as e:
            formatted += f"\n\n---\n\n⚠️ 供应商筛选暂时不可用：{e}"
            st.session_state["current_stage"] = "structuring"
    else:
        st.session_state["current_stage"] = "structuring"

    # 保存历史（含推荐结果）
    try:
        recommendations_json = ""
        if st.session_state.get("screening_result"):
            screening = st.session_state["screening_result"]
            recommendations_json = json.dumps(
                [{
                    "name": item_data["supplier"].get("company_name", ""),
                    "score": item_data["score"]["overall"],
                    "source": item_data["source"],
                } for item_data in screening.get("top3", [])],
                ensure_ascii=False,
            )
        save_procurement_history(
            demand_raw=user_input,
            structured_demand=json.dumps(demand, ensure_ascii=False),
            recommendations=recommendations_json,
        )
    except Exception:
        pass

    return {"content": formatted, "stage": st.session_state["current_stage"]}


def handle_pdf_generation() -> dict:
    """处理 PDF 生成请求（使用新版完整报告）"""
    screening_result = st.session_state.get("screening_result")
    demand = st.session_state.get("structured_demand", {})

    if not screening_result:
        return {"content": "⚠️ 暂无可生成的报告内容，请先完成供应商匹配。", "stage": None}

    try:
        pdf_path = generate_full_report(demand, screening_result)
        st.session_state["pdf_path"] = pdf_path
        st.session_state["current_stage"] = "done"
        return {"content": "done_with_pdf", "stage": "done"}
    except Exception as e:
        return {"content": f"❌ PDF 生成失败：{str(e)}", "stage": None}


# ─── 主界面 ───
def main():
    setup_database()
    init_session_state()
    apply_custom_css()

    # 标题
    st.markdown(f"""
    <div class="console-header">
        <div class="brand">🏢 智采 Agent</div>
        <div class="subtitle">AI 采购专家智能体 · 需求结构化 · 供应商匹配 · 合规报告交付</div>
    </div>
    """, unsafe_allow_html=True)

    # 阶段指示器
    render_stage_indicator()
    st.markdown("---")

    # 渲染侧边栏
    render_sidebar()

    # ─── 消息区（卡片式布局）───
    for idx, msg in enumerate(st.session_state.get("messages", [])):
        role = msg.get("role", "")
        content = msg.get("content", "")
        stage = msg.get("stage")
        json_str = msg.get("json_data")

        if stage:
            st.markdown(
                f'<a id="stage-{stage}" style="display:block;height:1px;"></a>',
                unsafe_allow_html=True,
            )

        if role == "user":
            st.markdown(f'<div class="user-msg-bubble">{content}</div>', unsafe_allow_html=True)
        else:
            if "<!--SPLIT-->" in content and json_str:
                part1, part2 = content.split("<!--SPLIT-->", 1)
                st.markdown(f'<div class="procurement-card active-card"><div class="card-label">📋 采购需求单</div>{part1}</div>', unsafe_allow_html=True)
                with st.expander("查看结构化 JSON"):
                    st.code(json_str, language="json")
                if part2.strip():
                    st.markdown(part2)
            elif content == "done_with_pdf":
                # PDF 已生成，仅显示下载
                pass
            else:
                st.markdown(content)
                if json_str:
                    with st.expander("查看结构化 JSON"):
                        st.code(json_str, language="json")

    # ── PDF 生成按钮（stage=pdf_ready 时显示）──
    current_stage = st.session_state.get("current_stage", "idle")
    pdf_path = st.session_state.get("pdf_path")

    if current_stage == "done" and pdf_path and Path(pdf_path).exists():
        # PDF 已生成：显示下载按钮
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            with open(pdf_path, "rb") as f:
                st.download_button(
                    label="📥 下载 PDF 报告",
                    data=f,
                    file_name=Path(pdf_path).name,
                    mime="application/pdf" if pdf_path.endswith(".pdf") else "text/html",
                    key=f"download_{len(st.session_state.get('messages', []))}",
                    use_container_width=True,
                )

    elif current_stage == "pdf_ready" and st.session_state.get("screening_result"):
        # 匹配完成，等待用户生成 PDF
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button("📄 生成 PDF 报告", use_container_width=True, type="primary"):
                with st.spinner("🔄 正在生成 PDF 报告..."):
                    result = handle_pdf_generation()
                    response = result["content"]
                    stage = result.get("stage")
                    st.session_state["messages"].append({"role": "assistant", "content": response, "stage": stage})
                    st.rerun()

    # 聊天输入框
    if prompt := st.chat_input("请描述您的采购需求，例如：「帮我采购 50 箱 A4 复印纸，70g，下周送到上海办公室」"):
        st.session_state["messages"].append({"role": "user", "content": prompt})
        st.markdown(f'<div class="user-msg-bubble">{prompt}</div>', unsafe_allow_html=True)

        with st.spinner("🔄 正在处理采购需求..."):
            result = handle_user_input(prompt)
            response = result["content"]
            stage = result.get("stage")

            if response == "__GENERATE_PDF__":
                result = handle_pdf_generation()
                response = result["content"]
                stage = result.get("stage")

            json_data = st.session_state.pop("last_demand_json", None)
            if stage:
                st.markdown(
                    f'<a id="stage-{stage}" style="display:block;height:1px;"></a>',
                    unsafe_allow_html=True,
                )
            if "<!--SPLIT-->" in response:
                part1, part2 = response.split("<!--SPLIT-->", 1)
                st.markdown(f'<div class="procurement-card active-card"><div class="card-label">📋 采购需求单</div>{part1}</div>', unsafe_allow_html=True)
                if json_data:
                    with st.expander("查看结构化 JSON"):
                        st.code(json_data, language="json")
                if part2.strip():
                    st.markdown(part2)
            elif response == "done_with_pdf":
                pass
            else:
                st.markdown(response)
                if json_data:
                    with st.expander("查看结构化 JSON"):
                        st.code(json_data, language="json")

        msg_data = {"role": "assistant", "content": response, "stage": stage}
        if json_data:
            msg_data["json_data"] = json_data
        st.session_state["messages"].append(msg_data)

        # 如果是 PDF 刚生成完，显示下载按钮
        if response == "done_with_pdf":
            st.rerun()

        st.rerun()

    # 页脚
    st.markdown(f"""
    <div class="seal-disclaimer">
        <div class="seal-box">🤖 AI 生成 · 仅供参考 · 请以人工核对为准</div>
        <div style="margin-top: 0.5rem; font-size: 0.68rem; color: {BRAND['slate']};">
            智采 Agent v1.0 · Powered by DeepSeek · 不做最终采购决策
        </div>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
