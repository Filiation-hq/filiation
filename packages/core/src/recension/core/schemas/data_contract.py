"""SQL DDL and table contracts for DuckDB and Parquet storage."""

CREATE_TABLE_DEVICE_NODES = """
CREATE TABLE IF NOT EXISTS device_nodes (
    k_number VARCHAR PRIMARY KEY,
    applicant VARCHAR,
    device_name VARCHAR,
    product_code VARCHAR,
    decision_date DATE,
    review_panel VARCHAR,
    statement_or_summary VARCHAR,
    expedited_review_flag VARCHAR,
    third_party_flag VARCHAR
);
"""

CREATE_TABLE_PREDICATE_EDGES = """
CREATE TABLE IF NOT EXISTS predicate_edges (
    device_k VARCHAR NOT NULL,
    predicate_k VARCHAR NOT NULL,
    relationship_type VARCHAR NOT NULL,
    source_page_num INTEGER,
    confidence_score DOUBLE NOT NULL,
    extraction_method VARCHAR NOT NULL,
    raw_text_snippet VARCHAR,
    section_heading VARCHAR,
    extracted_at TIMESTAMP NOT NULL,
    PRIMARY KEY (device_k, predicate_k, relationship_type)
);
"""

CREATE_TABLE_RECALLS = """
CREATE TABLE IF NOT EXISTS recalls (
    product_res_number VARCHAR PRIMARY KEY,
    res_event_number VARCHAR NOT NULL,
    classification VARCHAR,
    k_numbers VARCHAR[],
    product_description VARCHAR,
    root_cause_description VARCHAR,
    reason_for_recall VARCHAR,
    recall_status VARCHAR,
    event_date_initiated DATE,
    event_date_posted DATE,
    is_design_related BOOLEAN
);
"""

CREATE_TABLE_DEVICE_RECALL_LINKS = """
CREATE TABLE IF NOT EXISTS device_recall_links (
    k_number VARCHAR NOT NULL,
    product_res_number VARCHAR NOT NULL,
    res_event_number VARCHAR NOT NULL,
    classification VARCHAR,
    event_date_initiated DATE,
    is_design_related BOOLEAN,
    PRIMARY KEY (k_number, product_res_number)
);
"""
