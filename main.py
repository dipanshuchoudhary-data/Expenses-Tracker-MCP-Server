import os
import json
import asyncio
import aiosqlite
from fastmcp import FastMCP

# === Configuration ===
IS_CLOUD = os.environ.get("FASTMCP_ENV", "").lower() == "cloud"
# Use in-memory DB on cloud (persistent connection), otherwise a file in repo dir
DEFAULT_DB_FILENAME = "expenses.db"
PROJECT_DIR = os.path.dirname(__file__) or "."
DB_FILE_PATH = os.path.join(PROJECT_DIR, DEFAULT_DB_FILENAME)
DB_PATH = ":memory:" if IS_CLOUD else DB_FILE_PATH

# Categories file (optional). If missing, fallback to embedded defaults.
CATEGORIES_PATH = os.path.join(PROJECT_DIR, "categories.json")

# MCP server
mcp = FastMCP("ExpenseTracker")

# === Global async DB connection and sync primitives ===
_db_conn: aiosqlite.Connection | None = None
_db_lock = asyncio.Lock()  # protects initialization so it's run only once

# SQL for table creation
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

async def get_db() -> aiosqlite.Connection:
    """
    Return a single persistent aiosqlite connection for the lifetime of the process.
    For in-memory DB (':memory:'), using one persistent connection is required
    to keep data between calls.
    """
    global _db_conn
    if _db_conn is not None:
        return _db_conn

    async with _db_lock:
        if _db_conn is not None:
            return _db_conn

        # instantiate connection
        # enable WAL for file DB where possible (improves concurrency)
        _db_conn = await aiosqlite.connect(DB_PATH)
        # recommended pragmas
        await _db_conn.execute("PRAGMA foreign_keys = ON;")
        if not IS_CLOUD:  # WAL on file-based DB only (cloud is read-only anyway)
            try:
                await _db_conn.execute("PRAGMA journal_mode=WAL;")
            except Exception:
                # Not fatal if WAL unsupported
                pass

        # Ensure schema exists (idempotent, no test writes)
        await _db_conn.execute(CREATE_TABLE_SQL)
        await _db_conn.commit()

        return _db_conn

# === Tools ===

@mcp.tool()
async def add_expense(date: str, amount: float, category: str, subcategory: str = "", note: str = ""):
    """
    Add a new expense entry.
    Returns dict with status and inserted id or error message.
    """
    try:
        db = await get_db()
        cur = await db.execute(
            "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?);",
            (date, amount, category, subcategory, note),
        )
        await db.commit()
        inserted_id = cur.lastrowid
        return {"status": "success", "id": inserted_id}
    except Exception as e:
        msg = str(e)
        if "readonly" in msg.lower() or "attempt to write a readonly database" in msg.lower():
            return {"status": "error", "message": "Database is read-only in this environment."}
        return {"status": "error", "message": f"Database error: {msg}"}


@mcp.tool()
async def list_expenses(start_date: str, end_date: str, limit: int = 100):
    """
    List expenses between start_date and end_date (inclusive).
    Returns a list of rows (dicts).
    """
    try:
        db = await get_db()
        cur = await db.execute(
            """
            SELECT id, date, amount, category, subcategory, note
            FROM expenses
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC, id DESC
            LIMIT ?
            """,
            (start_date, end_date, limit),
        )
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": f"Error listing expenses: {str(e)}"}


@mcp.tool()
async def summarize(start_date: str, end_date: str, category: str | None = None):
    """
    Summarize expenses by category. Returns list of {category, total_amount, count}.
    """
    try:
        db = await get_db()
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

        cur = await db.execute(query, params)
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": f"Error summarizing expenses: {str(e)}"}


@mcp.tool()
async def removal(field: str, value: str):
    """
    Remove expenses by a validated field. Valid fields: id, category, date, amount.
    Returns count removed or error.
    """
    allowed = {"id", "category", "date", "amount"}
    if field not in allowed:
        return {"status": "error", "message": f"Invalid field. Allowed: {sorted(allowed)}"}

    # Ensure id/amount use numeric types where appropriate
    try:
        db = await get_db()
        cur = await db.execute(f"DELETE FROM expenses WHERE {field} = ?;", (value,))
        await db.commit()
        return {"status": "success", "deleted": cur.rowcount}
    except Exception as e:
        return {"status": "error", "message": f"Error deleting expenses: {str(e)}"}


@mcp.tool()
async def update_expense(id: int, field: str, new_value: str):
    """
    Update a field for a given expense id. Allowed fields: date, category, amount, subcategory, note.
    """
    allowed = {"date", "category", "amount", "subcategory", "note"}
    if field not in allowed:
        return {"status": "error", "message": f"Invalid field. Allowed: {sorted(allowed)}"}

    try:
        db = await get_db()
        cur = await db.execute(f"UPDATE expenses SET {field} = ? WHERE id = ?;", (new_value, id))
        await db.commit()
        return {"status": "success", "updated": cur.rowcount}
    except Exception as e:
        return {"status": "error", "message": f"Error updating expense: {str(e)}"}


# === Resource: categories ===
# Use a valid resource URI and return JSON text. No filesystem writes attempted.
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
                # Return raw JSON string — resource expects mime_type JSON
                return f.read()
        # fallback to embedded defaults
        return json.dumps(default, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Could not load categories: {str(e)}"})

# === Entrypoint behavior ===
# Keep __main__ block for local debugging; FastMCP Cloud ignores it and uses entrypoint you configure.
if __name__ == "__main__":
    # Use port 8000 locally by default
    mcp.run(transport="http", host="0.0.0.0", port=8000)
