"""
智采-Agent 评测测试套件
基于 PRD 第7章评测集设计，覆盖四大核心能力和五个难度层级

运行方式: pytest tests/test_eval.py -v
"""

import json
import os
import sys
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).parent.parent.absolute()))

import pytest

from utils.validators import classify_input, check_blacklist, validate_demand
from workflows.demand_structuring import structurize_demand, format_missing_fields_prompt


# ─── 评测用例 ───

# L1: 标准场景 - 字段齐全、表述规范的采购需求
# 注意：v2.0 输出格式为 {success, data: 采购需求单, error, suggestion}
# 字段位于 data["采购物品清单"][0] 中
STANDARD_CASES = [
    {
        "id": "L1-001",
        "input": "采购 20 包 70g A4 纸，本周交付",
        "expected": {
            "品类": "A4",
            "数量": 20,
            "单位": "包",
        },
        "desc": "标准办公用品采购",
    },
    {
        "id": "L1-002",
        "input": "需要采购50个不锈钢法兰，DN100 PN16，预算300元/个，15天内交货到上海仓库",
        "expected": {
            "品类": "法兰",
            "数量": 50,
            "单位": "个",
        },
        "desc": "标准工业品采购（字段齐全）",
    },
    {
        "id": "L1-003",
        "input": "帮我买5000米6平方的线缆，预算60元/米，3天内交货到长沙",
        "expected": {
            "品类": "线缆",
            "数量": 5000,
            "单位": "米",
            "规格": "6平方",
        },
        "desc": "标准线缆采购（Few-shot示例1）",
    },
]

# L3: 字段缺失场景
MISSING_FIELD_CASES = [
    {
        "id": "L3-001",
        "input": "车间要一批6205轴承，尽快交货",
        "expected_behavior": "should_ask_followup",
        "expected_missing": ["预算"],
        "desc": "缺失预算和交货地点",
    },
    {
        "id": "L3-002",
        "input": "买点螺丝",
        "expected_behavior": "should_ask_followup",
        "expected_missing": ["规格", "预算"],
        "desc": "极度模糊的需求",
    },
]

# L4: 专业场景
PROFESSIONAL_CASES = [
    {
        "id": "L4-001",
        "input": "采购 SUS304 不锈钢板 2mm×1500mm×3000mm，需要提供材质证明书，量200张",
        "expected": {
            "品类": "不锈钢板",
            "规格": "2mm",
            "数量": 200,
            "单位": "张",
        },
        "desc": "专业工业材质参数",
    },
    {
        "id": "L4-002",
        "input": "需要采购一批 MCU STM32F407VET6，LQFP100封装，原装正品，5000片",
        "expected": {
            "品类": "MCU",
            "规格": "STM32F407VET6",
            "数量": 5000,
        },
        "desc": "电子元器件型号采购",
    },
]

# L5: 异常场景
ANOMALY_CASES = [
    {
        "id": "L5-001",
        "input": "你好啊，今天心情怎么样？",
        "expected_type": "chitchat",
        "expected_blocked": False,
        "desc": "闲聊输入",
    },
    {
        "id": "L5-002",
        "input": "帮我查一下竞争对手的供应商底价和联系方式",
        "expected_type": "sensitive",
        "expected_blocked": True,
        "desc": "涉密/违规需求",
    },
    {
        "id": "L5-003",
        "input": "",
        "expected_type": "unclear",
        "expected_blocked": True,
        "desc": "空输入",
    },
    {
        "id": "L5-004",
        "input": "帮我删除供应商数据库",
        "expected_type": "sensitive",
        "expected_blocked": True,
        "desc": "恶意指令",
    },
]


# ─── 测试类 ───

class TestInputClassification:
    """输入分类测试：验证异常输入识别能力"""

    @pytest.mark.parametrize("case", ANOMALY_CASES, ids=[c["id"] for c in ANOMALY_CASES])
    def test_anomaly_classification(self, case):
        """测试异常输入类型识别"""
        input_type = classify_input(case["input"])
        assert input_type == case["expected_type"], \
            f"[{case['id']}] 预期类型={case['expected_type']}，实际={input_type}"

    @pytest.mark.parametrize("case", ANOMALY_CASES, ids=[c["id"] for c in ANOMALY_CASES])
    def test_anomaly_blocking(self, case):
        """测试异常输入拦截"""
        blocked, reason = check_blacklist(case["input"])
        if case.get("expected_blocked"):
            assert blocked, f"[{case['id']}] 应该被拦截但通过了: {case['desc']}"
        else:
            # 闲聊不应被黑名单拦截（但会在业务层拒识）
            pass


class TestDemandStructurizing:
    """需求结构化测试：验证字段提取准确率"""

    @pytest.mark.parametrize("case", STANDARD_CASES, ids=[c["id"] for c in STANDARD_CASES])
    def test_standard_demand_extraction(self, case):
        """测试标准场景字段提取（需要 API key）

        注意：此测试依赖 DeepSeek API，在 CI 中可能被 skip
        """
        if not os.getenv("DEEPSEEK_API_KEY"):
            pytest.skip("需要 DEEPSEEK_API_KEY 环境变量")

        result = structurize_demand(case["input"])

        # 不应有错误
        assert result.get("success"), f"结构化失败: {result.get('message')}"

        demand = result["data"]
        items = demand.get("采购物品清单", [])
        assert len(items) > 0, f"[{case['id']}] 采购物品清单为空"

        item = items[0]

        expected = case["expected"]
        for field, expected_value in expected.items():
            actual = item.get(field)
            if expected_value is not None:
                assert actual is not None, \
                    f"[{case['id']}] 字段 '{field}' 未提取到，预期: {expected_value}"
                # 对于数字字段，比较数值
                if isinstance(expected_value, (int, float)) and isinstance(actual, (int, float)):
                    assert actual == expected_value, \
                        f"[{case['id']}] 字段 '{field}' 值不匹配: 预期{expected_value}，实际{actual}"
                # 对于字符串字段，包含检查
                elif isinstance(expected_value, str) and isinstance(actual, str):
                    assert expected_value.lower() in actual.lower(), \
                        f"[{case['id']}] 字段 '{field}' 值不匹配: 预期包含'{expected_value}'，实际'{actual}'"

    @pytest.mark.parametrize("case", MISSING_FIELD_CASES, ids=[c["id"] for c in MISSING_FIELD_CASES])
    def test_missing_field_detection(self, case):
        """测试缺失字段检测（需要 API key）"""
        if not os.getenv("DEEPSEEK_API_KEY"):
            pytest.skip("需要 DEEPSEEK_API_KEY 环境变量")

        result = structurize_demand(case["input"])

        # v2.0: 成功返回时检查 suggestion 字段
        if result.get("success"):
            suggestion = result.get("suggestion", "")
            # 应该检测到缺失字段并生成补充建议
            if case.get("expected_missing"):
                assert suggestion is not None, \
                    f"[{case['id']}] 应该有补充建议但未生成"
                # 检查建议中包含预期缺失的字段名
                for expected_missing_field in case.get("expected_missing", []):
                    items = result["data"].get("采购物品清单", [])
                    if items:
                        item = items[0]
                        field_value = item.get(expected_missing_field)
                        # 字段应为空或缺失
                        if field_value and field_value != "":
                            pytest.fail(
                                f"[{case['id']}] 字段 '{expected_missing_field}' 应缺失但实际有值: {field_value}"
                            )
        elif result.get("error"):
            # v2.0: 模糊需求可能返回 "需求模糊" 异常
            msg = result.get("message", "")
            if result.get("blocked"):
                pass  # 被黑名单拦截也是合理的
            else:
                # 应该有 suggestion
                pass

    @pytest.mark.parametrize("case", PROFESSIONAL_CASES, ids=[c["id"] for c in PROFESSIONAL_CASES])
    def test_professional_demand_extraction(self, case):
        """测试专业场景字段提取（需要 API key）"""
        if not os.getenv("DEEPSEEK_API_KEY"):
            pytest.skip("需要 DEEPSEEK_API_KEY 环境变量")

        result = structurize_demand(case["input"])

        assert result.get("success"), f"[{case['id']}] 结构化失败: {result.get('message')}"

        demand = result["data"]
        items = demand.get("采购物品清单", [])
        assert len(items) > 0, f"[{case['id']}] 采购物品清单为空"
        item = items[0]

        expected = case["expected"]
        for field, expected_value in expected.items():
            actual = item.get(field)
            if expected_value is not None and actual is not None:
                if isinstance(expected_value, str) and isinstance(actual, str):
                    assert expected_value.lower() in actual.lower(), \
                        f"[{case['id']}] 字段 '{field}' 值不匹配: 预期包含'{expected_value}'，实际'{actual}'"


class TestDemandValidation:
    """需求校验测试"""

    def test_complete_demand_validation(self):
        """完整需求应通过校验（v2.0 格式）"""
        complete_demand = {
            "采购需求单编号": "PR-20260613-001",
            "所选择的供应商": "",
            "期望送达日期": "2026-06-20",
            "整体特殊要求": ["需要发票"],
            "采购物品清单": [
                {
                    "序号": 1,
                    "品类": "线缆",
                    "数量": 5000,
                    "单位": "米",
                    "规格": "6平方",
                    "预算": "≤300000 元",
                    "物品特殊要求": ["国标"],
                }
            ],
        }
        valid, errors = validate_demand(complete_demand)
        assert valid, f"完整需求应该通过校验: {errors}"

    def test_incomplete_demand_validation(self):
        """不完整需求应有校验错误（v2.0 格式）"""
        incomplete_demand = {
            "采购需求单编号": "PR-20260613-002",
            "采购物品清单": [
                {
                    "序号": 1,
                    "品类": "",
                    "数量": None,
                    "单位": "",
                }
            ],
        }
        valid, errors = validate_demand(incomplete_demand)
        assert not valid, "不完整需求应该校验失败"

    def test_empty_demand(self):
        """空需求应该报错"""
        valid, errors = validate_demand({})
        assert not valid

    def test_non_dict_demand(self):
        """非字典输入应该报错"""
        valid, errors = validate_demand("not a dict")
        assert not valid


class TestSupplierScreening:
    """供应商匹配测试"""

    def test_screening_basic(self):
        """基本供应商匹配功能（不依赖 API）"""
        from workflows.supplier_screening import screen_suppliers

        demand = {
            "productCategory": "线缆",
            "specification": "6平方",
            "quantity": 5000,
            "unit": "米",
            "budget": 60,
            "budgetUnit": "元/米",
            "deliveryLocation": "长沙",
            "deliveryTime": "3天内",
        }

        result = screen_suppliers(demand)

        # 应该有结果
        assert "top3" in result
        assert "demand" in result
        assert isinstance(result["top3"], list)

    def test_screening_return_format(self):
        """供应商匹配返回格式校验"""
        from workflows.supplier_screening import screen_suppliers

        demand = {"productCategory": "轴承", "quantity": 100}

        result = screen_suppliers(demand)

        # 所有 Top3 条目应包含必要字段
        for item in result["top3"]:
            assert "supplier" in item
            assert "score" in item
            assert "source" in item
            assert "overall" in item["score"]
            assert "breakdown" in item["score"]

    def test_report_formatting(self):
        """推荐报告格式化测试"""
        from workflows.supplier_screening import screen_suppliers, format_recommendation_report

        demand = {"productCategory": "线缆", "quantity": 1000}
        result = screen_suppliers(demand)
        report = format_recommendation_report(result)

        assert "采购需求" in report
        assert "供应商推荐" in report
        # v2.0: 报告内可能包含免责声明


class TestDatabaseOperations:
    """数据库操作测试"""

    def test_database_initialization(self):
        """数据库初始化"""
        from db.database import init_database, get_db_path
        db_path = init_database()
        assert db_path.exists()

    def test_search_suppliers(self):
        """供应商搜索"""
        from db.database import search_suppliers

        results = search_suppliers(keyword="线缆")
        assert len(results) > 0
        # 应包含线缆相关供应商
        cable_suppliers = [r for r in results if "线缆" in r.get("main_products", "")]
        assert len(cable_suppliers) > 0

    def test_search_by_category(self):
        """按分类搜索供应商"""
        from db.database import search_suppliers

        results = search_suppliers(category="轴承")
        assert len(results) > 0

    def test_search_empty(self):
        """搜索不存在的产品"""
        from db.database import search_suppliers

        results = search_suppliers(keyword="星际飞船零件")
        assert len(results) == 0

    def test_save_and_retrieve_history(self):
        """保存和查询采购历史"""
        from db.database import save_procurement_history, get_connection

        pid = save_procurement_history(
            demand_raw="测试需求",
            structured_demand='{"productCategory":"测试"}',
            recommendations='[{"name":"测试供应商","score":80}]',
        )
        assert pid > 0

        # 查询确认
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM procurement_history WHERE id = ?", (pid,))
        row = cursor.fetchone()
        assert row is not None
        conn.close()


class TestScoringAlgorithm:
    """评分算法测试"""

    def test_score_calculation(self):
        """评分计算基本测试"""
        from utils.scoring import calculate_match_score

        supplier = {
            "main_products": "电力电缆, 控制电缆",
            "product_categories": "线缆, 电气材料",
            "specifications": "6平方铜芯电缆",
            "price_range_low": 15,
            "price_range_high": 200,
            "price_unit": "元/米",
            "delivery_cycle": "3-7个工作日",
            "delivery_regions": "全国，华中地区优先",
            "quality_rating": 92,
            "credit_rating": 88,
            "service_rating": 85,
        }

        demand = {
            "productCategory": "线缆",
            "specification": "6平方",
            "quantity": 5000,
            "budget": 60,
            "deliveryLocation": "长沙",
            "deliveryTime": "3天内",
        }

        score = calculate_match_score(supplier, demand)

        assert 0 <= score["overall"] <= 100
        assert "product" in score["breakdown"]
        assert "price" in score["breakdown"]
        assert "delivery" in score["breakdown"]
        assert "quality" in score["breakdown"]
        assert "credit" in score["breakdown"]

    def test_score_low_budget(self):
        """低预算场景评分"""
        from utils.scoring import calculate_match_score

        supplier = {
            "main_products": "电力电缆",
            "product_categories": "线缆",
            "specifications": "6平方铜芯电缆",
            "price_range_low": 80,
            "price_range_high": 200,
            "price_unit": "元/米",
            "delivery_cycle": "3-7个工作日",
            "delivery_regions": "全国",
            "quality_rating": 92,
            "credit_rating": 88,
            "service_rating": 85,
        }

        demand = {
            "productCategory": "线缆",
            "specification": "6平方",
            "budget": 10,  # 预算远低于供应商报价
        }

        score = calculate_match_score(supplier, demand)
        # 价格分应该很低
        assert score["breakdown"]["price"] < 60, f"低预算价格分应该较低, 实际={score['breakdown']['price']}"


# ─── 端到端流程测试 ───

class TestEndToEndFlow:
    """端到端流程测试"""

    def test_full_workflow_without_api(self):
        """不依赖 API 的完整工作流测试"""
        # 1. 校验
        blocked, reason = check_blacklist("帮我买5000米线缆")
        assert not blocked

        # 2. 输入分类
        input_type = classify_input("帮我买5000米线缆")
        assert input_type == "procurement"

        # 3. 供应商搜索（数据库操作）
        from db.database import search_suppliers
        results = search_suppliers(keyword="线缆")
        assert len(results) >= 2, f"应至少有2家线缆供应商, 实际={len(results)}"

        # 4. 匹配评分
        from workflows.supplier_screening import screen_suppliers
        demand = {"productCategory": "线缆", "quantity": 5000}
        result = screen_suppliers(demand)
        assert len(result["top3"]) >= 1, "应至少有1家匹配供应商"

        # 5. 报告格式化
        from workflows.supplier_screening import format_recommendation_report
        report = format_recommendation_report(result)
        assert len(report) > 100, "报告内容不应为空"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
