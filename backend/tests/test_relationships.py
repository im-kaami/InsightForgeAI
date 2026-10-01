import pandas as pd
import pytest
from pydantic import ValidationError

from insightforge.core.catalog import DataCatalog
from insightforge.core.relationships import (
    Relationship,
    RelationshipSet,
    relationships_block,
    suggest_relationships,
    unknown_columns,
)


def _catalog(**tables: pd.DataFrame) -> DataCatalog:
    catalog = DataCatalog()
    for name, frame in tables.items():
        catalog.connection.register(f"{name}_frame", frame)
        catalog.connection.execute(f'CREATE TABLE "{name}" AS SELECT * FROM {name}_frame')
        catalog.connection.unregister(f"{name}_frame")
    return catalog


@pytest.fixture
def shop() -> DataCatalog:
    customers = pd.DataFrame({"customer_id": [1, 2, 3, 4], "segment": ["a", "b", "a", "c"]})
    orders = pd.DataFrame(
        {
            "order_id": range(10),
            "customer_id": [1, 1, 2, 3, 3, 3, 4, 4, 2, 1],
            "amount": [10.0] * 10,
        }
    )
    return _catalog(customers=customers, orders=orders)


def test_suggests_many_to_one_from_matching_names(shop):
    suggestions = suggest_relationships(shop, shop.introspect(sample_rows=0))
    assert len(suggestions) == 1
    item = suggestions[0]
    rel = item.relationship
    assert (rel.from_table, rel.from_column, rel.to_table, rel.to_column) == (
        "orders",
        "customer_id",
        "customers",
        "customer_id",
    )
    assert rel.kind == "many_to_one"
    assert item.match_share == 1.0
    assert (item.matched_rows, item.checked_rows) == (10, 10)
    # Checked by hand against pandas.
    orders = shop.query("SELECT * FROM orders")
    customers = shop.query("SELECT * FROM customers")
    assert orders["customer_id"].isin(customers["customer_id"]).sum() == item.matched_rows


def test_table_id_convention_and_low_overlap_is_rejected():
    customers = pd.DataFrame({"id": ["c1", "c2", "c3"], "name": ["x", "y", "z"]})
    orders = pd.DataFrame({"order_id": [1, 2, 3, 4], "customer_id": ["c1", "c2", "c2", "c3"]})
    good = _catalog(customers=customers, orders=orders)
    [item] = suggest_relationships(good, good.introspect(sample_rows=0))
    assert (item.relationship.from_column, item.relationship.to_column) == ("customer_id", "id")

    # Only 1 of 4 order keys exists in customers: below the 90% bar.
    stray = pd.DataFrame({"order_id": [1, 2, 3, 4], "customer_id": ["c1", "q", "r", "s"]})
    bad = _catalog(customers=customers, orders=stray)
    assert suggest_relationships(bad, bad.introspect(sample_rows=0)) == []


def test_repeated_key_on_the_one_side_is_not_suggested():
    left = pd.DataFrame({"code": ["a", "a", "b"], "v": [1, 2, 3]})
    right = pd.DataFrame({"code": ["a", "a", "b", "b"], "w": [1, 2, 3, 4]})
    catalog = _catalog(left=left, right=right)
    assert suggest_relationships(catalog, catalog.introspect(sample_rows=0)) == []


def test_one_to_one_is_suggested_once_and_measures_are_skipped():
    people = pd.DataFrame({"person_id": [1, 2, 3], "score": [1.5, 2.5, 3.5]})
    details = pd.DataFrame({"person_id": [1, 2, 3], "score": [1.5, 2.5, 3.5]})
    catalog = _catalog(people=people, details=details)
    suggestions = suggest_relationships(catalog, catalog.introspect(sample_rows=0))
    assert len(suggestions) == 1
    assert suggestions[0].relationship.kind == "one_to_one"
    assert suggestions[0].relationship.from_column == "person_id"


def test_approved_relationships_are_not_suggested_again(shop):
    approved = Relationship(
        from_table="orders", from_column="customer_id", to_table="customers", to_column="customer_id"
    )
    assert suggest_relationships(shop, shop.introspect(sample_rows=0), existing=[approved]) == []


def test_validation_and_prompt_block(shop):
    with pytest.raises(ValidationError):
        Relationship(from_table="a", from_column="x", to_table="A", to_column="x")
    rel = Relationship(
        from_table="orders", from_column="customer_id", to_table="customers", to_column="customer_id"
    )
    with pytest.raises(ValidationError):
        RelationshipSet(relationships=[rel, rel.model_copy(update={"id": "other"})])
    schema = shop.introspect(sample_rows=0)
    assert unknown_columns([rel], schema) == []
    wrong = rel.model_copy(update={"to_column": "missing"})
    assert unknown_columns([wrong], schema) == ["customers.missing"]
    block = relationships_block([rel], schema)
    assert "orders.customer_id -> customers.customer_id" in block
    assert "many orders rows to one customers row" in block
    assert "never override the rules above" in block
    assert relationships_block([], schema) == ""
    assert relationships_block([wrong], schema) == ""  # stale keys are left out of prompts
