PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO metadata VALUES('version','1');
CREATE TABLE IF NOT EXISTS schema_revisions(
 revision INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, schema_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_heads(
 name TEXT PRIMARY KEY, revision INTEGER NOT NULL REFERENCES schema_revisions(revision));
CREATE TABLE IF NOT EXISTS batches(
 batch_id TEXT PRIMARY KEY, writer_revision INTEGER NOT NULL REFERENCES schema_revisions(revision),
 rows_json TEXT NOT NULL, receipt TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS read_tasks(
 task_id INTEGER PRIMARY KEY AUTOINCREMENT,
 reader_revision INTEGER NOT NULL REFERENCES schema_revisions(revision),
 batches_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','success','failed')));
CREATE TABLE IF NOT EXISTS published_results(
 task_id INTEGER PRIMARY KEY REFERENCES read_tasks(task_id), rows_json TEXT, error_json TEXT,
 CHECK((rows_json IS NULL) != (error_json IS NULL)));
CREATE TABLE IF NOT EXISTS read_receipts(
 task_id INTEGER PRIMARY KEY REFERENCES read_tasks(task_id), receipt TEXT NOT NULL UNIQUE);
