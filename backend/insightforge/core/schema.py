from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str
    dtype: str
    sample_values: list[str] = Field(default_factory=list)
    null_fraction: float | None = None
    sensitivity: str | None = None


class TableInfo(BaseModel):
    name: str
    row_count: int
    columns: list[ColumnInfo]


class SchemaInfo(BaseModel):
    tables: list[TableInfo]

    def table(self, name: str) -> TableInfo | None:
        target = name.casefold()
        return next((table for table in self.tables if table.name.casefold() == target), None)

    def has_column(self, table: str, column: str) -> bool:
        table_info = self.table(table)
        target = column.casefold()
        return bool(table_info and any(item.name.casefold() == target for item in table_info.columns))

    def to_prompt(self, max_tables: int = 20, max_columns: int = 60) -> str:
        blocks: list[str] = []
        columns_left = max_columns
        shown_tables = self.tables[:max_tables]
        omitted_columns = sum(len(table.columns) for table in self.tables[max_tables:])
        for table in shown_tables:
            lines = [f"TABLE {table.name} ({table.row_count} rows)"]
            shown = table.columns[: max(columns_left, 0)]
            for column in shown:
                sensitive = f" [sensitive: {column.sensitivity}]" if column.sensitivity else ""
                samples = f"  e.g. {', '.join(column.sample_values)}" if column.sample_values else ""
                lines.append(f"- {column.name}: {column.dtype}{sensitive}{samples}")
            hidden = len(table.columns) - len(shown)
            omitted_columns += hidden
            columns_left -= len(shown)
            blocks.append("\n".join(lines))
        if omitted_columns:
            blocks.append(f"... (+{omitted_columns} more columns)")
        omitted_tables = len(self.tables) - len(shown_tables)
        if omitted_tables:
            blocks.append(f"... (+{omitted_tables} more tables)")
        return "\n\n".join(blocks)
