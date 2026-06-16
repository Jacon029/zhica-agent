-- 智采-Agent 供应商数据库表结构
-- SQLite 数据库

-- 供应商主表
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,           -- 公司名称
    main_products TEXT NOT NULL,           -- 主营产品（逗号分隔）
    product_categories TEXT,               -- 产品分类（逗号分隔）
    specifications TEXT,                   -- 规格参数描述
    price_range_low REAL,                  -- 报价范围（低）
    price_range_high REAL,                 -- 报价范围（高）
    price_unit TEXT DEFAULT '元',           -- 报价单位
    delivery_cycle TEXT,                   -- 交付周期
    delivery_regions TEXT,                 -- 可交付区域
    quality_rating REAL DEFAULT 0,         -- 质量评分 0-100
    credit_rating REAL DEFAULT 0,          -- 信用评分 0-100
    service_rating REAL DEFAULT 0,         -- 服务评分 0-100
    contact_info TEXT,                     -- 联系方式（脱敏）
    website TEXT,                          -- 官网
    cooperation_status TEXT DEFAULT 'active', -- 合作状态: active/suspended/blacklist
    established_year INTEGER,              -- 成立年份
    registered_capital TEXT,               -- 注册资本
    employee_count INTEGER,                -- 员工人数
    certifications TEXT,                   -- 资质认证
    notes TEXT,                            -- 备注
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 采购历史记录表
CREATE TABLE IF NOT EXISTS procurement_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    demand_json TEXT NOT NULL,             -- 原始采购需求 JSON
    structured_demand TEXT NOT NULL,        -- 结构化需求 JSON
    recommended_suppliers TEXT,            -- 推荐的供应商列表 JSON
    selected_supplier_id INTEGER,          -- 最终选定的供应商 ID
    status TEXT DEFAULT 'pending',         -- pending/confirmed/cancelled
    user_feedback TEXT,                    -- 用户反馈
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (selected_supplier_id) REFERENCES suppliers(id)
);

-- 供应商历史评分记录
CREATE TABLE IF NOT EXISTS supplier_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL,
    procurement_id INTEGER,
    price_score REAL,
    delivery_score REAL,
    quality_score REAL,
    service_score REAL,
    overall_score REAL,
    comment TEXT,
    rated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (procurement_id) REFERENCES procurement_history(id)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_suppliers_products ON suppliers(main_products);
CREATE INDEX IF NOT EXISTS idx_suppliers_categories ON suppliers(product_categories);
CREATE INDEX IF NOT EXISTS idx_suppliers_status ON suppliers(cooperation_status);
CREATE INDEX IF NOT EXISTS idx_history_created ON procurement_history(created_at);
