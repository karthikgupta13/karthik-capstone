"""
main.py
-------
The web app. Run it with:   python main.py
Then open:                  http://127.0.0.1:5000

Flask connects each URL to a Python function (a "route"). Each route asks
database.py for data and hands it to an HTML page in the templates/ folder.
"""

import csv
import io
from datetime import datetime, timedelta

from flask import Flask, Response, abort, flash, redirect, render_template, request, url_for

import database as db

app = Flask(__name__)
app.secret_key = "support-desk-demo"  # shortcut: hard-coded, only signs flash messages; load from an env variable if this ever goes online

# How many hours each priority gets before the ticket breaches its SLA.
SLA_HOURS = {"High": 4, "Medium": 24, "Low": 72}

# Make sure the table and the sample data exist before the first request.
db.create_table()
db.seed_sample_tickets()


# ---------------------------------------------------------------- helpers

def describe_gap(delta):
    """Turn a time gap into short text such as '45m', '5h' or '2d 3h'."""
    minutes = int(abs(delta.total_seconds()) // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h" if hours < 24 else f"{hours // 24}d {hours % 24}h"


def with_sla(row):
    """Copy a database row into a dict and add its SLA due time, breach flag and label."""
    ticket  = dict(row)
    created = datetime.strptime(ticket["created_date"], "%Y-%m-%d %H:%M:%S")
    due     = created + timedelta(hours=SLA_HOURS[ticket["priority"]])
    gap     = due - datetime.now()

    ticket["sla_due"]  = due
    ticket["breached"] = ticket["status"] != "Resolved" and gap.total_seconds() < 0

    if ticket["status"] == "Resolved":
        ticket["sla_text"] = "Closed"
    elif ticket["breached"]:
        ticket["sla_text"] = f"Overdue by {describe_gap(gap)}"
    else:
        ticket["sla_text"] = f"Due in {describe_gap(gap)}"
    return ticket


def breached_tickets():
    """Return the unresolved tickets that are past their SLA. These need escalation."""
    return [t for t in map(with_sla, db.get_unresolved_tickets()) if t["breached"]]


def validate_ticket_fields(form, errors):
    """Check the priority and team fields shared by the create and update forms."""
    if form.get("priority") not in db.PRIORITIES:
        errors.append("Choose a priority: Low, Medium or High.")
    if form.get("assigned_team") not in db.TEAMS:
        errors.append("Choose a support team from the list.")


@app.template_filter("nice_date")
def nice_date(value):
    """Template filter: show '2026-11-16 21:03:11' as '16 Nov 2026, 21:03'."""
    if isinstance(value, str):
        value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return value.strftime("%d %b %Y, %H:%M")


@app.context_processor
def shared_values():
    """Make the allowed values available in every template, for dropdowns and labels."""
    return {
        "PRIORITIES":      db.PRIORITIES,
        "STATUSES":        db.STATUSES,
        "TEAMS":           db.TEAMS,
        "EXCEPTION_TYPES": db.EXCEPTION_TYPES,
        "SLA_HOURS":       SLA_HOURS,
    }


# ---------------------------------------------------------------- pages

@app.route("/")
def tickets():
    """Tickets page: status counts, a status filter and the full ticket list."""
    status = request.args.get("status")
    if status not in db.STATUSES:
        status = None  # unknown or missing filter: show everything

    counts = db.count_by("status")
    return render_template(
        "index.html",
        tickets  = [with_sla(row) for row in db.get_tickets(status)],
        counts   = counts,
        total    = sum(counts.values()),
        breached = len(breached_tickets()),
        active   = status,
    )


@app.route("/search")
def search():
    """Find a ticket by ID. Accepts '7' or '#7'. Bad input shows a message instead of crashing."""
    raw = request.args.get("ticket_id", "").strip().lstrip("#")
    if not raw:
        flash("Enter a ticket ID to search, for example 7.", "error")
        return redirect(url_for("tickets"))

    try:
        ticket_id = int(raw)
    except ValueError:
        flash(f"'{raw}' is not a ticket ID. Ticket IDs are numbers, like 7.", "error")
        return redirect(url_for("tickets"))

    if db.get_ticket(ticket_id) is None:
        flash(f"No ticket found with ID #{ticket_id}.", "error")
        return redirect(url_for("tickets"))

    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/tickets/new", methods=["GET", "POST"])
def new_ticket():
    """Show the new-ticket form (GET), or validate it and save the ticket (POST)."""
    form   = request.form
    errors = []

    if request.method == "POST":
        employee_name = form.get("employee_name", "").strip()
        issue         = form.get("issue", "").strip()
        process_name  = form.get("process_name", "").strip()
        exception     = form.get("exception_type", "")

        if not employee_name:
            errors.append("Employee name is required.")
        if len(issue) < 5:
            errors.append("Describe the issue in at least 5 characters.")
        if exception and exception not in db.EXCEPTION_TYPES:
            errors.append("Choose a valid exception type.")
        validate_ticket_fields(form, errors)

        if not errors:
            ticket_id = db.create_ticket(
                employee_name,
                issue,
                form["priority"],
                form["assigned_team"],
                process_name or None,
                exception or None,
            )
            flash(f"Ticket #{ticket_id} created and assigned to {form['assigned_team']}.", "success")
            return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    return render_template("new.html", form=form, errors=errors)


@app.route("/tickets/<int:ticket_id>", methods=["GET", "POST"])
def ticket_detail(ticket_id):
    """Show one ticket (GET), or save a new status, priority, team and notes (POST)."""
    row = db.get_ticket(ticket_id)
    if row is None:
        abort(404)

    form   = request.form
    errors = []

    if request.method == "POST":
        status = form.get("status")
        notes  = form.get("resolution_notes", "").strip()

        if status not in db.STATUSES:
            errors.append("Choose a status: Open, In Progress or Resolved.")
        if status == "Resolved" and not notes:
            errors.append("Add resolution notes before resolving. What was the fix?")
        validate_ticket_fields(form, errors)

        if not errors:
            db.update_ticket(ticket_id, status, form["priority"], form["assigned_team"], notes or None)
            flash(f"Ticket #{ticket_id} saved. Status is now {status}.", "success")
            return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    return render_template("ticket.html", ticket=with_sla(row), form=form, errors=errors)


@app.route("/report")
def report():
    """Report page: GROUP BY counts for status, priority and team, plus SLA breaches."""
    by_status   = db.count_by("status")
    by_priority = db.count_by("priority")
    by_team     = db.count_by("assigned_team")

    # Every team appears, even with 0 tickets, busiest team first.
    teams = sorted(((team, by_team.get(team, 0)) for team in db.TEAMS), key=lambda pair: -pair[1])

    return render_template(
        "report.html",
        by_status   = by_status,
        by_priority = by_priority,
        teams       = teams,
        total       = sum(by_status.values()),
        breached    = breached_tickets(),
        generated   = datetime.now(),
    )


@app.route("/report.csv")
def report_csv():
    """Download every ticket as a CSV file that opens straight in Excel."""
    rows   = db.get_tickets()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if rows:
        writer.writerow(rows[0].keys())
        writer.writerows(tuple(row) for row in rows)

    filename = f"ticket_report_{datetime.now():%Y-%m-%d}.csv"
    return Response(
        "﻿" + buffer.getvalue(),  # the BOM tells Excel the file is UTF-8
        mimetype = "text/csv",
        headers  = {"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    app.run(debug=True)
