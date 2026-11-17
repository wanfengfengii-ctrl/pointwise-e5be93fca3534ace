from .jsonutil import dumps, loads


class ResultPublisher:
    def __init__(self, db, tasks):
        self.db, self.tasks = db, tasks

    def result(self, task_id):
        task = self.tasks.get(task_id)
        if task["state"] == "pending":
            return {"task_id": task_id, "state": "pending", "rows": None,
                    "error": None, "receipt": None}
        row = self.db.connection.execute(
            "SELECT p.rows_json,p.error_json,r.receipt FROM published_results p "
            "JOIN read_receipts r USING(task_id) WHERE task_id=?", (task_id,)).fetchone()
        return {"task_id": task_id, "state": task["state"],
                "rows": loads(row[0]) if row[0] is not None else None,
                "error": loads(row[1]) if row[1] is not None else None, "receipt": row[2]}

    def publish(self, task_id, rows, error, before_commit=None):
        if (rows is None) == (error is None):
            raise ValueError("publish exactly one outcome")
        with self.db.transaction() as conn:
            task = self.tasks.get(task_id)
            if task["state"] != "pending":
                return self.result(task_id)
            state = "failed" if error is not None else "success"
            conn.execute("INSERT INTO published_results VALUES(?,?,?)",
                         (task_id, dumps(rows) if rows is not None else None,
                          dumps(error) if error is not None else None))
            conn.execute("INSERT INTO read_receipts VALUES(?,?)",
                         (task_id, f"read:{task_id}:{state}"))
            conn.execute("UPDATE read_tasks SET state=? WHERE task_id=?", (state, task_id))
            if before_commit is not None:
                before_commit(conn)
        return self.result(task_id)
