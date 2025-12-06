import os
import json
import sqlite3
from fastmcp import FastMCP

# === Configuration ===
IS_CLOUD = os.environ.get("FASTMCP_ENV", "").lower() == "cloud"
DEFAULT_DB_FILENAME = "expenses.db"
PROJECT_DIR = os.path.dirname(__file__) or "."
DB_FILE_PATH = os.path.join(PROJECT_DIR, DEFAULT_DB_FILENAME)
DB_PATH = ":memory:" if IS_CLOUD else DB_FILE_PATH

CATEGORIES_PATH = os.path.join(PROJECT_DIR, "categories.json")

# MCP server
mcp = FastMCP("ExpenseTracker")

# === Global DB connection ===
_db_conn: sqlite3.Connection | None = None

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS expenses(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT DEFAULT '',
    note TEXT DEFAULT ''
);
"""

def get_db() -> sqlite3.Connection:
    """Return a single persistent SQLite connection."""
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_conn.execute("PRAGMA foreign_keys = ON;")
        if not IS_CLOUD:
            try:
                _db_conn.execute("PRAGMA journal_mode=WAL;")
            except Exception:
                pass
        _db_conn.execute(CREATE_TABLE_SQL)
        _db_conn.commit()
    return _db_conn

# === Tools ===

@mcp.tool()
def add_expense(date: str, amount: float, category: str, subcategory: str = "", note: str = ""):
    """Add a new expense entry."""
    try:
        db = get_db()
        cur = db.execute(
            "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?)",
            (date, amount, category, subcategory, note),
        )
        db.commit()
        return {"status": "success", "id": cur.lastrowid}
    except Exception as e:
        msg = str(e)
        if "readonly" in msg.lower():
            return {"status": "error", "message": "Database is read-only in this environment."}
        return {"status": "error", "message": msg}

@mcp.tool()
def list_expenses(start_date: str, end_date: str, limit: int = 100):
    """List expenses between two dates."""
    try:
        db = get_db()
        cur = db.execute(
            """
            SELECT id, date, amount, category, subcategory, note
            FROM expenses
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC, id DESC
            LIMIT ?
            """,
            (start_date, end_date, limit),
        )
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def summarize(start_date: str, end_date: str, category: str | None = None):
    """Summarize expenses by category."""
    try:
        db = get_db()
        query = """
            SELECT category, SUM(amount) AS total_amount, COUNT(*) AS count
            FROM expenses
            WHERE date BETWEEN ? AND ?
        """
        params = [start_date, end_date]

        if category:
            query += " AND category = ?"
            params.append(category)

        query += " GROUP BY category ORDER BY total_amount DESC"

        cur = db.execute(query, params)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def removal(field: str, value: str):
    """Remove expenses by field."""
    allowed = {"id", "category", "date", "amount"}
    if field not in allowed:
        return {"status": "error", "message": f"Invalid field. Allowed: {sorted(allowed)}"}

    try:
        db = get_db()
        cur = db.execute(f"DELETE FROM expenses WHERE {field} = ?", (value,))
        db.commit()
        return {"status": "success", "deleted": cur.rowcount}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def update_expense(id: int, field: str, new_value: str):
    """Update a field for a given expense."""
    allowed = {"date", "category", "amount", "subcategory", "note"}
    if field not in allowed:
        return {"status": "error", "message": f"Invalid field. Allowed: {sorted(allowed)}"}

    try:
        db = get_db()
        cur = db.execute(f"UPDATE expenses SET {field} = ? WHERE id = ?", (new_value, id))
        db.commit()
        return {"status": "success", "updated": cur.rowcount}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# === Resource: categories ===
@mcp.resource("expense://categories", mime_type="application/json")
def categories():
    default = {
        "categories": [
            "Food & Dining",
            "Transportation",
            "Shopping",
            "Entertainment",
            "Bills & Utilities",
            "Healthcare",
            "Travel",
            "Education",
            "Business",
            "Other"
        ]
    }
    try:
        if os.path.exists(CATEGORIES_PATH):
            with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
                return f.read()
        return json.dumps(default, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Could not load categories: {str(e)}"})

# === Entrypoint ===
if __name__ == "__main__":
    mcp.run()
