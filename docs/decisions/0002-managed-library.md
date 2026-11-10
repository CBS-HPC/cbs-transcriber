# Managed library with SQLite metadata

Accepted, 2026-10-20. Copy imports into platform-local application data outside Git and
OneDrive, storing metadata and editable transcript JSON in SQLite. Managed copies cost
disk space but avoid broken references when the original source folder moves; preserving
originals makes correction/export reversible. A library backup includes the database,
originals, source transcripts, and derivatives and is taken with the server stopped.
