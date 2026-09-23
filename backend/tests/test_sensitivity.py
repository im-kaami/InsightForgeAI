from insightforge.core.sensitivity import classify_column


def test_classify_sensitive_columns():
    assert classify_column("email") == "pii"
    assert classify_column("customerEmail") == "pii"
    assert classify_column("Salary") == "financial"
    assert classify_column("birth_date") == "pii"
    assert classify_column("password_hash") == "credential"
    assert classify_column("access_token") == "credential"
    assert classify_column("department") is None
    assert classify_column("lesson") is None
    assert classify_column("adobe_id") is None
    assert classify_column("token_usage") is None


def test_introspection_redacts_sensitive_samples(catalog):
    schema = catalog.introspect()
    employees = schema.table("employees")
    salary = next(column for column in employees.columns if column.name == "salary")
    department = next(column for column in employees.columns if column.name == "department")
    assert salary.sensitivity == "financial"
    assert salary.sample_values == ["<redacted>"]
    assert department.sensitivity is None
    assert "Engineering" in department.sample_values
    prompt = schema.to_prompt()
    assert "[sensitive: financial]" in prompt
    assert "98000" not in prompt
