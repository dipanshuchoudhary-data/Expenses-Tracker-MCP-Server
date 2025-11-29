from fastmcp import FastMCP
import os
import json
import aiosqlite

DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

mcp = FastMCP("ExpenseTracker")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as c:
        await c.execute("""
            CREATE TABLE IF NOT EXISTS expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                note TEXT DEFAULT ''
            )
        """)
        await c.commit()


# Initialize database on startup
import asyncio
asyncio.run(init_db())


# ---------------------------- ADD EXPENSE ----------------------------
@mcp.tool()
async def add_expense(date, amount, category, subcategory="", note=""):
    """Add a new expense entry to the database."""
    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(
            "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?)",
            (date, amount, category, subcategory, note)
        )
        await c.commit()
        return {"status": "ok", "id": cur.lastrowid}


# ---------------------------- LIST EXPENSES ----------------------------
@mcp.tool()
async def list_expenses(start_date, end_date):
    """List expenses in a date range."""
    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(
            """
            SELECT id, date, amount, category, subcategory, note
            FROM expenses
            WHERE date BETWEEN ? AND ?
            ORDER BY id ASC
            """,
            (start_date, end_date)
        )
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]


# ---------------------------- SUMMARIZE ----------------------------
@mcp.tool()
async def summarize(start_date, end_date, category=None):
    """Summarize expenses by category."""
    query = """
        SELECT category, SUM(amount) AS total_amount
        FROM expenses
        WHERE date BETWEEN ? AND ?
    """

    params = [start_date, end_date]

    if category:
        query += " AND category = ?"
        params.append(category)

    query += " GROUP BY category ORDER BY category ASC"

    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(query, params)
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]


# ---------------------------- CATEGORIES RESOURCE ----------------------------
@mcp.resource("expense://categories", mime_type="application/json")
async def categories():
    async with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------- REMOVE EXPENSE ----------------------------
@mcp.tool()
async def removal(field: str, value: str):
    """Remove expense by field (id, category, date, amount)."""

    allowed = ["id", "category", "date", "amount"]
    if field not in allowed:
        return f"Invalid field. Use one of: {allowed}"

    query = f"DELETE FROM expenses WHERE {field} = ?;"

    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(query, (value,))
        await c.commit()
        count = cur.rowcount

    return f"{count} expense(s) removed."


# ---------------------------- UPDATE EXPENSE ----------------------------
@mcp.tool()
async def update_expense(id: int, field: str, new_value: str):
    """Update any expense field using ID."""

    allowed = ['date', 'category', 'amount']
    if field not in allowed:
        return f"Invalid field. You can only update: {allowed}"

    query = f"UPDATE expenses SET {field} = ? WHERE id = ?;"

    async with aiosqlite.connect(DB_PATH) as c:
        cur = await c.execute(query, (new_value, id))
        await c.commit()
        count = cur.rowcount

    return f"{count} expense(s) updated."


# ---------------------------- RUN SERVER ----------------------------
if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port="8000")
