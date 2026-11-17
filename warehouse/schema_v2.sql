PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS v2_programs(program_id TEXT PRIMARY KEY, document_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS v2_revisions(
 revision INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
 validator_program TEXT NOT NULL REFERENCES v2_programs(program_id));
CREATE TABLE IF NOT EXISTS v2_heads(
 name TEXT PRIMARY KEY, revision INTEGER NOT NULL REFERENCES v2_revisions(revision));
CREATE TABLE IF NOT EXISTS v2_bindings(
 writer_revision INTEGER NOT NULL REFERENCES v2_revisions(revision),
 reader_revision INTEGER NOT NULL REFERENCES v2_revisions(revision),
 program_id TEXT NOT NULL REFERENCES v2_programs(program_id),
 PRIMARY KEY(writer_revision,reader_revision));
CREATE TABLE IF NOT EXISTS v2_batches(
 batch_id TEXT PRIMARY KEY, writer_revision INTEGER NOT NULL REFERENCES v2_revisions(revision),
 rows_json TEXT NOT NULL, receipt TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS v2_tasks(
 task_id INTEGER PRIMARY KEY AUTOINCREMENT,
 reader_revision INTEGER NOT NULL REFERENCES v2_revisions(revision),
 batches_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','success','failed')));
CREATE TABLE IF NOT EXISTS v2_results(
 task_id INTEGER PRIMARY KEY REFERENCES v2_tasks(task_id), rows_json TEXT, error_json TEXT,
 CHECK((rows_json IS NULL) != (error_json IS NULL)));
CREATE TABLE IF NOT EXISTS v2_receipts(
 task_id INTEGER PRIMARY KEY REFERENCES v2_tasks(task_id), receipt TEXT NOT NULL UNIQUE);
