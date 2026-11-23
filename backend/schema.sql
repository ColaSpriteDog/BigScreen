PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS screen_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    timezone TEXT NOT NULL,
    safety_start_date TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS process_node (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    image_path TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    manual_status TEXT NOT NULL DEFAULT 'normal'
        CHECK (manual_status IN ('normal', 'stopped', 'maintenance', 'fault'))
);

CREATE TABLE IF NOT EXISTS production_line (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    standard_qty INTEGER CHECK (standard_qty IS NULL OR standard_qty >= 0),
    process_id INTEGER NOT NULL REFERENCES process_node(id),
    show_in_trend INTEGER NOT NULL CHECK (show_in_trend IN (0, 1)),
    sort_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS production_daily (
    line_id INTEGER NOT NULL REFERENCES production_line(id),
    work_date TEXT NOT NULL,
    actual_qty INTEGER CHECK (actual_qty IS NULL OR actual_qty >= 0),
    defect_qty INTEGER CHECK (defect_qty IS NULL OR defect_qty >= 0),
    planned_hours REAL NOT NULL DEFAULT 24 CHECK (planned_hours >= 0 AND planned_hours <= 24),
    CHECK (defect_qty IS NULL OR (actual_qty IS NOT NULL AND defect_qty <= actual_qty)),
    PRIMARY KEY (line_id, work_date)
);
CREATE INDEX IF NOT EXISTS production_by_date ON production_daily(work_date, line_id);

CREATE TABLE IF NOT EXISTS line_downtime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id INTEGER NOT NULL REFERENCES production_line(id),
    start_at TEXT NOT NULL,
    end_at TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('stopped', 'maintenance', 'fault')),
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    CHECK (end_at IS NULL OR end_at > start_at)
);
CREATE INDEX IF NOT EXISTS downtime_period ON line_downtime(line_id, start_at, end_at);

CREATE TABLE IF NOT EXISTS raw_material_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    specification TEXT NOT NULL,
    position TEXT NOT NULL DEFAULT '',
    quantity REAL NOT NULL CHECK (quantity >= 0),
    unit TEXT NOT NULL,
    weight_kg REAL CHECK (weight_kg IS NULL OR weight_kg >= 0),
    cuttable_qty INTEGER CHECK (cuttable_qty IS NULL OR cuttable_qty >= 0),
    sort_order INTEGER NOT NULL,
    UNIQUE(material, warehouse, specification, position, unit)
);

CREATE TABLE IF NOT EXISTS semi_finished_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    material TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    specification TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    unit TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    UNIQUE(category, material, warehouse, specification, unit)
);

CREATE TABLE IF NOT EXISTS sales_month (
    month TEXT PRIMARY KEY,
    order_qty INTEGER CHECK (order_qty IS NULL OR order_qty >= 0)
);

CREATE TABLE IF NOT EXISTS shipment_month_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL REFERENCES sales_month(month) ON DELETE CASCADE,
    part_no TEXT NOT NULL COLLATE NOCASE,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    name TEXT NOT NULL DEFAULT '',
    shipment_date TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_overview (
    work_date TEXT PRIMARY KEY,
    planned_qty INTEGER CHECK (planned_qty IS NULL OR planned_qty >= 0),
    actual_qty INTEGER CHECK (actual_qty IS NULL OR actual_qty >= 0),
    oee_percent REAL CHECK (oee_percent IS NULL OR oee_percent BETWEEN 0 AND 100),
    quality_percent REAL CHECK (quality_percent IS NULL OR quality_percent BETWEEN 0 AND 100),
    average_output REAL CHECK (average_output IS NULL OR average_output >= 0)
);

CREATE TABLE IF NOT EXISTS auth_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    secret_key TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employee (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    password TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    auth_version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS record_attribution (
    record_type TEXT NOT NULL,
    record_key TEXT NOT NULL,
    created_by_id INTEGER,
    created_by_name TEXT,
    created_at TEXT,
    modified_by_id INTEGER NOT NULL,
    modified_by_name TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    PRIMARY KEY (record_type, record_key)
);

PRAGMA user_version = 4;
