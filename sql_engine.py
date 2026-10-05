"""Core logic: question -> SQL (Gemini) -> safety checks -> run on the user's own database.
Works with SQLite, MySQL and PostgreSQL. Only the table structure and the question go to the AI,
never the rows of data."""
import os
import re
import sqlite3
import time

from google import genai
from google.genai import types
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

DEMO_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rentdrive_sample.db")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_ROWS = 500
TIMEOUT_SECONDS = 5
DIALECT_NAMES = {"sqlite": "SQLite", "mysql": "MySQL", "postgresql": "PostgreSQL"}

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|grant|revoke|"
    r"pragma|vacuum|truncate|reindex|call|exec|execute|merge)\b",
    re.IGNORECASE,
)

DEMO_NOTES = """- Today's date is 2026-10-05. Money amounts are in Indian rupees.
- Revenue / earnings = SUM(invoice.total_amount). An invoice is unpaid if it has no payment rows;
  paid amount = SUM(payment.amount_paid) per invoice.
- A rental is still open (car not returned yet) when rental.return_date IS NULL.
- An overdue return = open rental whose reservation.end_date is before today's date.
- reservation.status is BOOKED, CANCELLED or COMPLETED. vehicle.status is AVAILABLE, RENTED or MAINTENANCE.
- Join path: customer -> reservation -> rental -> invoice -> payment; reservation -> vehicle -> vehiclecategory.
- Dates are stored as text (YYYY-MM-DD). Use SQLite functions (date(), strftime(), julianday()).
- A "booking" means a row in reservation; unless the question says otherwise, exclude CANCELLED ones and use start_date as the booking date.
- Weekend = Saturday or Sunday: strftime('%w', date) IN ('0','6').
- "Average per weekend / per week / per month" = count the rows in each period first (a subquery grouped by
  strftime('%Y-%W', date) or strftime('%Y-%m', date)), then take AVG of those counts."""


# ---------------------------------------------------------------- connecting
_DEADLINES = {}  # sqlite connection id -> time when the running query must stop


def _sqlite_guard(dbapi_conn, _record):
    """Allow only reading, and stop slow queries."""
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    dbapi_conn.set_authorizer(lambda action, a, b, db, trig: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    key = id(dbapi_conn)

    def progress():
        deadline = _DEADLINES.get(key)
        return 1 if deadline and time.time() > deadline else 0

    dbapi_conn.set_progress_handler(progress, 100000)


def make_engine(kind, path=None, host=None, port=None, database=None, user=None, password=None):
    """Create a read-only engine. kind: sqlite | mysql | postgresql."""
    if kind == "sqlite":
        def creator():
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        engine = create_engine("sqlite://", creator=creator, poolclass=NullPool)
        event.listen(engine, "connect", _sqlite_guard)
        return engine

    if kind == "mysql":
        url = URL.create("mysql+pymysql", username=user, password=password, host=host,
                         port=int(port or 3306), database=database)
        engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})

        @event.listens_for(engine, "connect")
        def _mysql_readonly(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            for stmt in ("SET SESSION TRANSACTION READ ONLY", "SET SESSION MAX_EXECUTION_TIME=5000"):
                try:
                    cur.execute(stmt)
                except Exception:  # e.g. MariaDB does not know MAX_EXECUTION_TIME
                    pass
            cur.close()
            dbapi_conn.commit()
        return engine

    if kind == "postgresql":
        url = URL.create("postgresql+psycopg", username=user, password=password, host=host,
                         port=int(port or 5432), database=database)
        return create_engine(url, pool_pre_ping=True, connect_args={
            "connect_timeout": 10,
            "options": "-c default_transaction_read_only=on -c statement_timeout=5000"})

    raise ValueError("Unknown database type.")


def test_connection(engine):
    """Connect and return the list of table names. Raises a readable error on failure."""
    try:
        names = inspect(engine).get_table_names() + inspect(engine).get_view_names()
    except ModuleNotFoundError as e:
        raise RuntimeError(f"A database driver is missing: {e.name}. Install it with pip and try again.")
    except SQLAlchemyError as e:
        raise RuntimeError(str(getattr(e, "orig", e)))
    if not names:
        raise RuntimeError("Connected, but no tables were found in this database.")
    return sorted(names)


# ---------------------------------------------------------------- schema
def _introspect_schema(engine) -> str:
    insp = inspect(engine)
    blocks = []
    for name in insp.get_table_names() + insp.get_view_names():
        fks = {}
        try:
            for fk in insp.get_foreign_keys(name):
                for c, rc in zip(fk["constrained_columns"], fk["referred_columns"]):
                    fks[c] = f"{fk['referred_table']}.{rc}"
        except Exception:
            pass
        try:
            pks = set(insp.get_pk_constraint(name).get("constrained_columns", []))
        except Exception:
            pks = set()
        lines = []
        for col in insp.get_columns(name):
            try:
                ctype = str(col["type"])
            except Exception:
                ctype = "UNKNOWN"
            extra = (" PRIMARY KEY" if col["name"] in pks else "") + (f" -> {fks[col['name']]}" if col["name"] in fks else "")
            lines.append(f"  {col['name']} {ctype}{extra}")
        blocks.append(f"TABLE {name} (\n" + ",\n".join(lines) + "\n)")
    return "\n\n".join(blocks)


def get_schema(engine, dialect: str) -> str:
    if dialect == "sqlite":  # the original CREATE statements keep CHECK rules such as allowed status values
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                                     "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name")).fetchall()
        return "\n\n".join(r[0] for r in rows)
    return _introspect_schema(engine)


def build_prompt(schema: str, dialect: str, notes: str = "") -> str:
    name = DIALECT_NAMES[dialect]
    notes_block = f"\nNotes about the data from the database owner:\n{notes.strip()}\n" if notes and notes.strip() else ""
    return f"""You are an expert {name} data analyst.
Convert the user's question into ONE {name} SELECT query.

Rules:
- Output ONLY the SQL query. No explanation, no markdown, no comments.
- Use only the tables and columns in the schema below.
- Only SELECT (or WITH ... SELECT). Never modify data.
- Give columns readable names with AS. Add a row limit of 100 unless the question asks for all rows or a single value.
- If a question is ambiguous, choose the most reasonable interpretation and answer it.
- Only if the database truly has no data for the question, output exactly: SELECT 'I cannot answer that from this database' AS message

Schema:
{schema}
{notes_block}"""


# ---------------------------------------------------------------- safety + running
def clean_sql(text_: str) -> str:
    text_ = text_.strip()
    text_ = re.sub(r"^```(?:sql)?", "", text_, flags=re.IGNORECASE).strip()
    text_ = re.sub(r"```$", "", text_).strip()
    return text_.rstrip(";").strip()


def validate_sql(sql: str) -> str:
    """Return the cleaned query or raise ValueError if it is not a safe read-only SELECT."""
    sql = clean_sql(sql)
    if not sql:
        raise ValueError("The AI returned an empty query.")
    if ";" in sql:
        raise ValueError("Only one SQL statement is allowed.")
    if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        raise ValueError("Only SELECT queries are allowed.")
    bad = FORBIDDEN.search(sql)
    if bad:
        raise ValueError(f"Blocked keyword in query: {bad.group(0).upper()}")
    return sql


def run_sql(engine, sql: str):
    """Run a validated query. Returns (columns, rows)."""
    with engine.connect() as conn:
        key = None
        if engine.dialect.name == "sqlite":
            key = id(conn.connection.dbapi_connection)
            _DEADLINES[key] = time.time() + TIMEOUT_SECONDS
        try:
            # escape ":" so SQLAlchemy never mistakes text such as '10:30' for a parameter
            result = conn.execute(text(sql.replace(":", "\\:")))
            columns = list(result.keys())
            rows = [tuple(r) for r in result.fetchmany(MAX_ROWS)]
            return columns, rows
        finally:
            if key is not None:
                _DEADLINES.pop(key, None)


def generate_sql(system_prompt: str, question: str, previous_sql: str = None, error: str = None) -> str:
    client = genai.Client()  # reads GEMINI_API_KEY from the environment
    prompt = f"Question: {question}"
    if previous_sql and error:
        prompt += f"\n\nYour previous query failed.\nQuery: {previous_sql}\nError: {error}\nWrite a corrected query."
    response = client.models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system_prompt, temperature=0),
    )
    return response.text


def answer(question: str, engine, system_prompt: str) -> dict:
    """Full pipeline. Returns dict with sql, columns, rows, error."""
    sql, last_error = None, None
    for attempt in range(2):  # one automatic retry if the query fails
        try:
            raw = generate_sql(system_prompt, question, sql, last_error) if attempt else generate_sql(system_prompt, question)
            sql = validate_sql(raw)
            columns, rows = run_sql(engine, sql)
            return {"sql": sql, "columns": columns, "rows": rows, "error": None}
        except ValueError as e:          # unsafe query: do not retry
            return {"sql": sql, "columns": [], "rows": [], "error": str(e)}
        except SQLAlchemyError as e:     # bad SQL: let the AI fix it once
            last_error = str(getattr(e, "orig", e))
        except Exception as e:           # API problems (quota, network, key)
            return {"sql": sql, "columns": [], "rows": [], "error": f"AI service error: {e}"}
    return {"sql": sql, "columns": [], "rows": [], "error": f"Query failed: {last_error}"}