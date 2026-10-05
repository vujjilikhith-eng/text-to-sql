import os
import sqlite3
import tempfile

import pandas as pd
import streamlit as st

# Key and optional question limit: from Streamlit Secrets when deployed, otherwise from the environment.
try:
    for name in ("GEMINI_API_KEY", "MAX_QUESTIONS"):
        if name in st.secrets:
            os.environ[name] = str(st.secrets[name])
except Exception:
    pass

import sql_engine as eng  # noqa: E402

MAX_QUESTIONS = int(os.environ.get("MAX_QUESTIONS", "0"))  # 0 = unlimited

st.set_page_config(page_title="Ask your database", page_icon="💬", layout="wide")

READONLY_HELP = {
    "mysql": """CREATE USER 'ai_reader'@'%' IDENTIFIED BY 'choose-a-strong-password';
GRANT SELECT ON your_database.* TO 'ai_reader'@'%';""",
    "postgresql": """CREATE USER ai_reader WITH PASSWORD 'choose-a-strong-password';
GRANT CONNECT ON DATABASE your_database TO ai_reader;
GRANT USAGE ON SCHEMA public TO ai_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ai_reader;""",
}


def finish_connect(engine, kind, label, notes=""):
    """Test the connection, read the structure, and open the chat screen."""
    try:
        tables = eng.test_connection(engine)
        schema = eng.get_schema(engine, kind)
    except Exception as e:
        st.error(f"Could not connect: {e}")
        return
    st.session_state.conn = {
        "engine": engine, "kind": kind, "label": label, "tables": tables, "schema": schema,
        "prompt": eng.build_prompt(schema, kind, notes),
    }
    st.session_state.history = []
    st.rerun()


def connection_screen():
    st.title("🔌 Connect your database")
    st.write("Pick where your data lives. We only read it: nothing can be changed or deleted.")
    st.info("Privacy: the AI receives your table and column names and your questions. "
            "The rows of your data are never sent to it.")

    choice = st.radio("Database type", ["Demo database", "SQLite file", "MySQL", "PostgreSQL"], horizontal=True)

    if choice == "Demo database":
        st.write("A made-up car rental company, so you can try the app right away.")
        if st.button("Connect to demo", type="primary"):
            engine = eng.make_engine("sqlite", path=eng.DEMO_DB_PATH)
            finish_connect(engine, "sqlite", "Demo: car rental", eng.DEMO_NOTES)

    elif choice == "SQLite file":
        with st.form("sqlite_form"):
            up = st.file_uploader("Upload your SQLite file (.db, .sqlite, .sqlite3)", type=["db", "sqlite", "sqlite3"])
            notes = st.text_area("Optional: tell the AI about your data", placeholder="e.g. Revenue means the sum of orders.total. Dates are YYYY-MM-DD.")
            go = st.form_submit_button("Connect", type="primary")
        if go:
            if not up:
                st.warning("Please choose a file first.")
            elif not up.getvalue().startswith(b"SQLite format 3\x00"):
                st.error("That file is not a SQLite database.")
            else:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
                tmp.write(up.getvalue())
                tmp.close()
                engine = eng.make_engine("sqlite", path=tmp.name)
                finish_connect(engine, "sqlite", up.name, notes)

    else:
        kind = "mysql" if choice == "MySQL" else "postgresql"
        default_port = 3306 if kind == "mysql" else 5432
        with st.form(f"{kind}_form"):
            c1, c2 = st.columns([3, 1])
            host = c1.text_input("Host", value="localhost", help="The server address. 'localhost' means the same computer this app runs on.")
            port = c2.number_input("Port", value=default_port, step=1)
            database = st.text_input("Database name")
            user = st.text_input("Username")
            password = st.text_input("Password", type="password")
            notes = st.text_area("Optional: tell the AI about your data", placeholder="e.g. Revenue means the sum of orders.total.")
            go = st.form_submit_button("Connect", type="primary")
        with st.expander("Recommended: create a read-only user for this app"):
            st.write("Give the app an account that can only read, so your data stays safe even if something goes wrong.")
            st.code(READONLY_HELP[kind], language="sql")
        if go:
            if not (host and database and user):
                st.warning("Please fill in host, database name and username.")
            else:
                engine = eng.make_engine(kind, host=host, port=port, database=database, user=user, password=password)
                finish_connect(engine, kind, f"{database} ({choice})", notes)


def chat_screen():
    conn = st.session_state.conn
    with st.sidebar:
        st.success(f"Connected: {conn['label']}")
        with st.expander(f"Tables ({len(conn['tables'])})"):
            for t in conn["tables"]:
                st.write(f"• {t}")
        if st.button("Disconnect"):
            try:
                conn["engine"].dispose()
            except Exception:
                pass
            del st.session_state.conn
            st.session_state.history = []
            st.rerun()

    st.title("💬 Ask your database")
    st.caption("Ask a question in plain English and press Enter.")

    for item in st.session_state.history:
        with st.chat_message("user"):
            st.write(item["question"])
        with st.chat_message("assistant"):
            r = item["result"]
            if r["error"]:
                st.error(r["error"])
            elif not r["rows"]:
                st.info("No matching records found.")
            else:
                df = pd.DataFrame(r["rows"], columns=r["columns"])
                st.dataframe(df, width="stretch")
                st.download_button("Download as spreadsheet (CSV)", df.to_csv(index=False), "result.csv", key=f"dl{item['id']}")
            if r["sql"]:
                with st.expander("Show SQL query", expanded=False):
                    st.code(r["sql"], language="sql")

    question = st.chat_input("Ask a question about your data...")
    if question and question.strip():
        if MAX_QUESTIONS and len(st.session_state.history) >= MAX_QUESTIONS:
            st.warning("Question limit reached for this session. Refresh the page to start again.")
        else:
            with st.spinner("Thinking..."):
                result = eng.answer(question.strip(), conn["engine"], conn["prompt"])
            st.session_state.history.append({"id": len(st.session_state.history), "question": question.strip(), "result": result})
            st.rerun()


if "history" not in st.session_state:
    st.session_state.history = []
if "conn" in st.session_state:
    chat_screen()
else:
    connection_screen()