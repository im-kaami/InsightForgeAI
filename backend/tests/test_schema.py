def test_schema_prompt_contains_tables_columns_and_samples(schema):
    prompt = schema.to_prompt()
    assert "TABLE employees (60 rows)" in prompt
    assert "TABLE departments (5 rows)" in prompt
    assert "department" in prompt
    assert "Engineering" in prompt
    assert "salary" in prompt


def test_schema_prompt_notes_column_truncation(schema):
    prompt = schema.to_prompt(max_columns=2)
    assert "more columns" in prompt
