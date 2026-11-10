"""Faithful older schemas for migration tests built from a current synthetic library.

Relabelling a current database as an older version is not enough (B365): the schema-17
language columns and the schema-18 Working authority table are created unconditionally by
their migrations, and schema-14 Working role rows can reference result versions that an
older fixture drops. A genuine older library has none of them.
"""

LANGUAGE_COLUMNS = {
    "projects": ("default_language", "language_revision"),
    "recordings": ("language_override", "language_revision"),
}
ROLE_TABLES = ("result_role_events", "recording_result_roles")


def remove_later_additions(db, version):
    """Drop schema additions after `version` that an older library cannot contain."""
    if version < 19:
        db.execute("DROP TABLE IF EXISTS passage_reviews")
    if version < 18:
        db.execute("DROP TABLE IF EXISTS working_job_authorities")
    if version < 17:
        for table, columns in LANGUAGE_COLUMNS.items():
            for column in columns:
                db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    if version < 14:
        for table in ROLE_TABLES:
            db.execute(f"DROP TABLE IF EXISTS {table}")


def without_added_languages(table, rows):
    """Migrated rows minus schema-17 columns, which must hold their additive defaults."""
    columns = LANGUAGE_COLUMNS.get(table, ())
    stripped = []
    for row in rows:
        row = dict(row)
        for column in columns:
            assert row.pop(column) == (0 if column == "language_revision" else None)
        stripped.append(row)
    return stripped
