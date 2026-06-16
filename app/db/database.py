"""
数据库初始化与管理模块
"""

import sqlite3
import os
from pathlib import Path

from config import DATABASE_PATH, ROOT_DIR
from db.seed_data import SEED_SUPPLIERS


def get_db_path() -> Path:
    """获取数据库文件路径，确保目录存在"""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_database():
    """初始化数据库：建表 + 插入种子数据"""
    db_path = get_db_path()
    schema_path = ROOT_DIR / "db" / "schema.sql"

    conn = get_connection()
    cursor = conn.cursor()

    # 执行建表 SQL
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    cursor.executescript(schema_sql)

    # 检查是否已有数据
    cursor.execute("SELECT COUNT(*) FROM suppliers")
    count = cursor.fetchone()[0]

    if count == 0:
        # 插入种子数据
        for supplier in SEED_SUPPLIERS:
            columns = ", ".join(supplier.keys())
            placeholders = ", ".join(["?" for _ in supplier])
            sql = f"INSERT INTO suppliers ({columns}) VALUES ({placeholders})"
            cursor.execute(sql, list(supplier.values()))

        conn.commit()
        print(f"[DB] 初始化完成：已插入 {len(SEED_SUPPLIERS)} 条供应商数据")
    else:
        print(f"[DB] 数据库已存在，当前 {count} 条供应商记录")

    conn.close()
    return db_path


def search_suppliers(
    keyword: str = None,
    category: str = None,
    min_quality: float = 0,
    min_credit: float = 0,
    max_price: float = None,
    limit: int = 20
) -> list[dict]:
    """
    内部供应商库搜索
    支持按关键词、分类、质量/信用评分、最高价格筛选
    """
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["cooperation_status = 'active'"]
    params = []

    if keyword:
        conditions.append(
            "(main_products LIKE ? OR product_categories LIKE ? OR specifications LIKE ? OR company_name LIKE ?)"
        )
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw, kw])

    if category:
        conditions.append("product_categories LIKE ?")
        params.append(f"%{category}%")

    if min_quality > 0:
        conditions.append("quality_rating >= ?")
        params.append(min_quality)

    if min_credit > 0:
        conditions.append("credit_rating >= ?")
        params.append(min_credit)

    if max_price is not None:
        conditions.append("price_range_low <= ?")
        params.append(max_price)

    where_clause = " AND ".join(conditions)
    sql = f"SELECT * FROM suppliers WHERE {where_clause} ORDER BY quality_rating DESC, credit_rating DESC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_all_suppliers(limit: int = 50) -> list[dict]:
    """获取所有活跃供应商"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM suppliers WHERE cooperation_status = 'active' ORDER BY quality_rating DESC LIMIT ?",
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_procurement_history(demand_raw: str, structured_demand: str, recommendations: str) -> int:
    """保存采购处理历史"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO procurement_history (demand_json, structured_demand, recommended_suppliers, status) VALUES (?, ?, ?, 'pending')",
        (demand_raw, structured_demand, recommendations)
    )
    conn.commit()
    pid = cursor.lastrowid
    conn.close()
    return pid


if __name__ == "__main__":
    init_database()
    # 测试搜索
    results = search_suppliers(keyword="线缆")
    print(f"\n搜索 '线缆' 结果数: {len(results)}")
    for r in results:
        print(f"  - {r['company_name']} | {r['main_products']} | 质量:{r['quality_rating']} | 信用:{r['credit_rating']}")
