"""
database.py
-----------
Everything that talks to SQLite lives in this file.

main.py never writes SQL itself. It calls the functions below, so every query
(CREATE, INSERT, SELECT, UPDATE, GROUP BY) sits in one place and is easy to find.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

# The database file is created next to this script, wherever you run it from.
DB_PATH = Path(__file__).with_name("support_tickets.db")

# Allowed values. The CHECK constraints in the table enforce the same lists,
# and main.py uses them to fill the dropdowns and validate the forms.
PRIORITIES      = ("Low", "Medium", "High")
STATUSES        = ("Open", "In Progress", "Resolved")
EXCEPTION_TYPES = ("System Exception", "Business Exception")
TEAMS           = (
    "Automation Support",
    "RPA Development",
    "Application Support",
    "Infrastructure",
    "Database Team",
)

# Ten realistic tickets so the app has data to show on first run.
# Columns: employee, process, exception type, priority, status, team, hours ago, issue, resolution notes
SAMPLE_TICKETS = [
    ("Karthik",        "Invoice_Processing",     "System Exception",   "High",   "Open",        "Automation Support",   6,
     "RPA bot login failure on virtual machine VM-02", None),
    ("Priya Sharma",   "Vendor_Onboarding",      "Business Exception", "Medium", "In Progress", "Automation Support",  10,
     "Bot stopped: vendor GST number missing in input file", None),
    ("Rahul Verma",    "Payroll_Reconciliation", "System Exception",   "High",   "Resolved",    "RPA Development",     30,
     "Excel automation timed out while opening the payroll workbook",
     "Raised wait stage timeout to 60 seconds and re-ran the batch successfully."),
    ("Ananya Iyer",    "Email_Triage",           "System Exception",   "Medium", "Open",        "Infrastructure",      30,
     "Outlook mailbox not reachable from virtual machine VM-05", None),
    ("Mohammed Arif",  "Claims_Intake",          "Business Exception", "Low",    "Resolved",    "Automation Support",  50,
     "Work queue rejected a duplicate claim ID",
     "Confirmed duplicate with the business team and marked the item complete."),
    ("Sneha Reddy",    "SAP_PO_Creation",        "System Exception",   "High",   "In Progress", "Application Support",  2,
     "SAP GUI session crashed during purchase order creation", None),
    ("Vikram Singh",   "Report_Distribution",    "System Exception",   "Medium", "Resolved",    "Automation Support",  20,
     "Scheduler did not trigger the 6 AM report process",
     "Schedule had been retired by mistake. Re-enabled it in the scheduler."),
    ("Divya Nair",     "Customer_KYC",           "Business Exception", "Low",    "Open",        "Automation Support",   5,
     "Scanned PAN card image is unreadable", None),
    ("Arjun Mehta",    "Database_Sync",          "System Exception",   "High",   "Resolved",    "Database Team",        8,
     "SQL Server refused the connection from the DB_Sync process",
     "Database service restarted after patching. Connection restored."),
    ("Kavya Rao",      "HR_Leave_Update",        "System Exception",   "Medium", "Open",        "Automation Support",   1,
     "Stored bot password expired for the HR portal", None),
]


@contextmanager
def connect():
    """Open the database, commit if everything worked, roll back if anything failed, always close."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us read a row like a dict: row["status"]
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_table():
    """Create the tickets table the first time the app runs. Does nothing if it already exists."""
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id        INTEGER PRIMARY KEY,
                employee_name    TEXT NOT NULL,
                issue            TEXT NOT NULL,
                priority         TEXT NOT NULL CHECK (priority IN ('Low', 'Medium', 'High')),
                status           TEXT NOT NULL DEFAULT 'Open'
                                      CHECK (status IN ('Open', 'In Progress', 'Resolved')),
                assigned_team    TEXT NOT NULL,
                created_date     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                resolution_notes TEXT,
                process_name     TEXT,
                exception_type   TEXT
            )
        """)


def seed_sample_tickets():
    """Insert the 10 sample tickets, but only when the table is empty."""
    with connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] > 0:
            return

        now = datetime.now()
        for name, process, exc_type, priority, status, team, hours_ago, issue, notes in SAMPLE_TICKETS:
            created = (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO tickets (employee_name, issue, priority, status, assigned_team,
                                     created_date, resolution_notes, process_name, exception_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, issue, priority, status, team, created, notes, process, exc_type),
            )


def create_ticket(employee_name, issue, priority, assigned_team, process_name, exception_type):
    """INSERT a new ticket. Every new ticket starts as 'Open'. Returns the new ticket_id."""
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tickets (employee_name, issue, priority, status, assigned_team,
                                 process_name, exception_type)
            VALUES (?, ?, ?, 'Open', ?, ?, ?)
            """,
            (employee_name, issue, priority, assigned_team, process_name, exception_type),
        )
        return cursor.lastrowid


def get_tickets(status=None):
    """SELECT every ticket, newest first. Pass a status to get only that status."""
    with connect() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM tickets WHERE status = ? ORDER BY ticket_id DESC", (status,)
            ).fetchall()
        return conn.execute("SELECT * FROM tickets ORDER BY ticket_id DESC").fetchall()


def get_ticket(ticket_id):
    """SELECT one ticket by its ID. Returns None when no ticket has that ID."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()


def get_unresolved_tickets():
    """SELECT every ticket that is not Resolved yet (Open or In Progress)."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM tickets WHERE status != 'Resolved' ORDER BY ticket_id"
        ).fetchall()


def update_ticket(ticket_id, status, priority, assigned_team, resolution_notes):
    """UPDATE a ticket's status, priority, team and resolution notes."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE tickets
            SET    status           = ?,
                   priority         = ?,
                   assigned_team    = ?,
                   resolution_notes = ?
            WHERE  ticket_id        = ?
            """,
            (status, priority, assigned_team, resolution_notes, ticket_id),
        )


def count_by(column):
    """GROUP BY report: how many tickets per status, priority or team, e.g. {'Open': 4, ...}."""
    # A column name can't be a ? placeholder, so only these known names are allowed into the SQL.
    if column not in ("status", "priority", "assigned_team"):
        raise ValueError(f"Cannot group by {column!r}")

    with connect() as conn:
        rows = conn.execute(
            f"SELECT {column}, COUNT(*) AS total FROM tickets GROUP BY {column}"
        ).fetchall()
    return {row[column]: row["total"] for row in rows}
