"""
采购需求单生成 + 供应商筛选 -- 全链路调试脚本

用法：
    python debug_demand.py                        # 交互式输入调试
    python debug_demand.py "帮我买5000米线缆"      # 命令行参数调试
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.absolute()))

from workflows.demand_structuring import structurize_demand, format_demand_order
from workflows.supplier_screening import screen_suppliers, format_recommendation_report


def debug(input_text: str, full_chain: bool = True):
    """调试采购需求全链路"""
    print("=" * 60)
    print(f"[INPUT] {input_text}")
    print("=" * 60)

    # ── Step 1: 采购需求单生成 ──
    result = structurize_demand(input_text)

    print(f"\n[STEP 1: Demand Structuring]")
    print(f"   success   : {result['success']}")
    print(f"   error     : {result['error']}")
    print(f"   blocked   : {result.get('blocked', False)}")
    print(f"   message   : {result.get('message', '')}")
    print(f"   suggestion: {result.get('suggestion', '')}")

    if result["success"] and result["data"]:
        print(f"\n{'='*40}")
        print(f"[TABLE VIEW]")
        print(f"{'='*40}")
        print(format_demand_order(result["data"]))
        print(f"\n{'='*40}")
        print(f"[JSON VIEW]")
        print(f"{'='*40}")
        print(json.dumps(result["data"], ensure_ascii=False, indent=2))
        if result.get("suggestion"):
            print(f"\n[TIP] {result['suggestion']}")

        # ── Step 2: 供应商筛选 ──
        if full_chain:
            demand = result["data"]
            items = demand.get("采购物品清单", [])
            first_item = items[0] if items else {}
            has_basics = bool(first_item.get("品类")) and (first_item.get("数量") not in (None, "", 0))

            if has_basics:
                print(f"\n{'='*60}")
                print(f"[STEP 2: Supplier Screening]")
                print(f"{'='*60}")
                try:
                    screening = screen_suppliers(demand)
                    print(f"   internal_count: {screening['internal_count']}")
                    print(f"   valid_count   : {screening['valid_internal_count']}")
                    print(f"   web_count     : {screening['web_count']}")
                    print(f"   top3          : {len(screening['top3'])}")
                    print(f"   reason        : {screening['search_reason']}")
                    print()
                    try:
                        print(format_recommendation_report(screening))
                    except UnicodeEncodeError:
                        print('(report contains emoji, skipped for terminal compatibility)')
                except Exception as e:
                    print(f"   [ERROR] Supplier screening failed: {e}")
            else:
                print(f"\n   [SKIP] Demand too incomplete for screening")

    elif result.get("error"):
        print(f"\n[ERROR] {result['message']}")
        if result.get("suggestion"):
            print(f"[TIP] {result['suggestion']}")
        if result.get("raw_output"):
            print(f"\n[LLM RAW OUTPUT]")
            print(result["raw_output"])

    print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # 命令行参数模式
        debug(sys.argv[1])
    else:
        # 交互模式
        print("=" * 60)
        print("  采购需求单生成 -- 独立调试")
        print("  输入 'exit' 退出")
        print("=" * 60)
        print()

        while True:
            try:
                user_input = input("请输入采购需求: ").strip()
                if user_input.lower() in ("exit", "quit", "q", "退出"):
                    print("退出调试")
                    break
                if not user_input:
                    continue
                print()
                debug(user_input)
            except KeyboardInterrupt:
                print("\n退出调试")
                break
            except Exception as e:
                print(f"\n[EXCEPTION] {e}\n")
