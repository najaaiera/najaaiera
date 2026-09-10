"""Receptionist Dashboard - standalone Flask app.

Separate from python_live_app.py / python_db_helper.py on purpose (per Thena's
instruction) - this project does not import or touch either of those files.
Lets receptionists look up a customer and see who owns it: Partner In Charge,
Portfolio Holder, and (most recent task's) Staff In Charge.

New accounts need approval before they can log in — see APPROVER_EMAILS and
the /pending-approvals route. On registration, an email goes out to every
approver with one-click Approve/Decline links (see send_approval_request_email
and the /approve-via-email, /decline-via-email routes) so they don't have to
log into the dashboard at all to act on it.
"""

import re as _re
import secrets
import smtplib
import threading
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pyodbc
from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for
from markupsafe import escape
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = "c30337c3c7bc1cc54871ee693629d0815753a740bdf40d842c9f1e9705b19b47"
# session.permanent = True (set on login) makes the cookie carry this expiry
# instead of dying when the browser closes, so users stay logged in across
# browser restarts until 30 days of inactivity.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
# SameSite=Lax blocks the cookie from being sent on cross-site POSTs (extra
# defense-in-depth alongside the CSRF tokens on state-changing forms).
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True


@app.after_request
def _no_cache_after_logout(response):
    # Stops the browser's back/forward cache from replaying an authenticated
    # page after logout — without this, clicking Back after logging out could
    # show the last-rendered dashboard straight from cache, no server request
    # (and no login check) involved at all.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    # Clickjacking / MIME-sniffing / referrer-leak hardening.
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response

# DATABASE CONNECTION (read-only lookups against the same Inistate data the
# main dashboard uses, plus this project's own accounts table)
SERVER = r"VM2025SQL01\SQLEXPRESS"
DATABASE = "Inistate"
USERNAME = "sa2"
PASSWORD = "sa2"

# Accounts table lives in the same separate "Dashboard Inistate" database the
# main dashboard's DashboardUsers table uses, referenced with a 3-part name
# so this app's one connection (pointed at Inistate) can still reach it.
_ACCOUNTS_TABLE = "[Dashboard Inistate].dbo.ReceptionistUsers"

# Only these emails can see /pending-approvals and approve new accounts.
# Also always counted as approvers regardless of the DB IsApprover flag below
# — a bootstrap set that can't accidentally be locked out of the dashboard.
APPROVER_EMAILS = {"fung.quek@daxin.com.my", "thenamuthean@asq.com.my"}

# 2026-08-11: Quek Fung asked for an admin view that can see every member,
# grant/revoke their approver access, or delete their account. Deliberately a
# SEPARATE, smaller set from APPROVER_EMAILS — deleting someone's account is a
# lot more dangerous than approving a registration, so this stays limited to
# exactly the two people he named, regardless of who else is an approver.
SUPER_ADMIN_EMAILS = {"fung.quek@daxin.com.my", "remmy@asq.com.my"}

# Fixed, hardcoded — do NOT switch this back to request.host_url. That reflects
# whatever address the REGISTRANT's own browser happened to be using (e.g.
# "localhost" if they registered from a browser open directly on the server),
# which breaks the approve/decline links for anyone opening the email from a
# different machine. This must always be the one real address every approver
# can reach the dashboard from.
PUBLIC_BASE_URL = "http://vm2025sql01:5090/"

# Same helpdesk mailbox/app password as the main dashboard's verification
# email — this file stays a separate SMTP setup on purpose (no imports from
# python_live_app.py), even though it happens to send from the same address.
SMTP_HOST = "smtp.office365.com"
SMTP_PORT = 587
SMTP_EMAIL = "helpdesk@asq.com.my"
SMTP_APP_PASSWORD = "hfnfwcwgnhzvhftz"


def _sanitize_header_value(value):
    # Registration name comes from the public form with no character
    # restriction — strip CR/LF before it reaches an email header so a name
    # like "Foo\r\nBcc: attacker@x.com" can't inject extra headers/recipients.
    return (value or "").replace("\r", " ").replace("\n", " ")


def send_approval_request_email(name, email, token, base_url):
    name = _sanitize_header_value(name)
    approve_url = f"{base_url}approve-via-email?token={token}"
    decline_url = f"{base_url}decline-via-email?token={token}"

    text_body = (
        f"A new Receptionist Dashboard account is waiting for approval:\n\n"
        f"    Name:  {name}\n"
        f"    Email: {email}\n\n"
        f"Approve:  {approve_url}\n"
        f"Decline:  {decline_url}\n\n"
        f"You can also review it inside the dashboard under Pending approvals.\n\n"
        f"Receptionist Dashboard - ASQ Consulting Group\n"
        f"This is an automated message - please don't reply."
    )

    # Email clients don't render <button>, so these are <a> tags styled with
    # inline CSS to look like buttons — the only reliable way to get a
    # button-like control in HTML email. Both links land on a confirmation
    # page, not the action itself (see the routes' own comment) — necessary
    # because corporate email security auto-visits every link in an email to
    # scan it, which would otherwise silently approve/decline on its own.
    button_style = (
        "display:inline-block;padding:12px 28px;margin:6px 8px;border-radius:8px;"
        "font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:15px;font-weight:600;"
        "text-decoration:none;"
    )
    approve_style = button_style + "background-color:#2997ff;color:#ffffff;"
    decline_style = button_style + "background-color:#ff453a;color:#ffffff;"
    html_body = f"""\
<html>
  <body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1c1d1f;">
    <p>A new Receptionist Dashboard account is waiting for approval:</p>
    <p>
      <b>Name:</b> {name}<br>
      <b>Email:</b> {email}
    </p>
    <p>
      <a href="{approve_url}" style="{approve_style}">Approve</a>
      <a href="{decline_url}" style="{decline_style}">Decline</a>
    </p>
    <p style="color:#6b6d70;font-size:13px;">
      You can also review it inside the dashboard under Pending approvals.
    </p>
    <p style="color:#6b6d70;font-size:12px;">
      Receptionist Dashboard &middot; ASQ Consulting Group<br>
      This is an automated message — please don't reply.
    </p>
  </body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Receptionist Dashboard: approve {name}?"
    msg["From"] = SMTP_EMAIL
    msg["To"] = ", ".join(sorted(APPROVER_EMAILS))
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_EMAIL, list(APPROVER_EMAILS), msg.as_string())


def send_decision_email(name, email, approved):
    if approved:
        subject = "Your Receptionist Dashboard account has been approved"
        heading = "You're approved"
        body_line = "You can now log in to the Receptionist Dashboard with the email and password you registered."
    else:
        subject = "Your Receptionist Dashboard registration was declined"
        heading = "Registration declined"
        body_line = "Your registration wasn't approved. If you think this is a mistake, please reach out to Quek Fung or Thena directly."

    text_body = f"Hi {name},\n\n{body_line}\n\nReceptionist Dashboard - ASQ Consulting Group\nThis is an automated message - please don't reply."
    html_body = f"""\
<html>
  <body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1c1d1f;">
    <p>Hi {name},</p>
    <p><b>{heading}.</b><br>{body_line}</p>
    <p style="color:#6b6d70;font-size:12px;">
      Receptionist Dashboard &middot; ASQ Consulting Group<br>
      This is an automated message — please don't reply.
    </p>
  </body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_EMAIL
    msg["To"] = email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_EMAIL, [email], msg.as_string())


def connect():
    return pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        f"UID={USERNAME};"
        f"PWD={PASSWORD};"
        "TrustServerCertificate=yes;"
        "Encrypt=no;",
        timeout=10,
        autocommit=True,
    )


_local = threading.local()


def fetch_rows(sql, params=None):
    conn = connect()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or [])
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()


def execute_write(sql, params=None):
    conn = connect()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or [])
    finally:
        conn.close()


def ensure_accounts_table():
    execute_write(
        "IF NOT EXISTS (SELECT 1 FROM [Dashboard Inistate].INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'ReceptionistUsers') "
        "CREATE TABLE [Dashboard Inistate].dbo.ReceptionistUsers ("
        "[Name] nvarchar(255), [Email] nvarchar(255), [PasswordHash] nvarchar(255), "
        "[CreatedAt] nvarchar(50), [PasswordUpdatedAt] nvarchar(50), "
        "[Approved] bit NOT NULL DEFAULT 0, [ApprovedAt] nvarchar(50), [ApprovedBy] nvarchar(255), "
        "[ApprovalToken] nvarchar(64) NULL)"
    )
    # Table may already exist from before this column was added — extend it
    # rather than assuming a fresh CREATE TABLE always ran.
    execute_write(
        "IF NOT EXISTS (SELECT 1 FROM [Dashboard Inistate].INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'ReceptionistUsers' AND COLUMN_NAME = 'ApprovalToken') "
        "ALTER TABLE [Dashboard Inistate].dbo.ReceptionistUsers ADD [ApprovalToken] nvarchar(64) NULL"
    )
    # Lets a super admin grant/revoke approver access to a regular member
    # through the Members page, instead of only the hardcoded APPROVER_EMAILS.
    execute_write(
        "IF NOT EXISTS (SELECT 1 FROM [Dashboard Inistate].INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'ReceptionistUsers' AND COLUMN_NAME = 'IsApprover') "
        "ALTER TABLE [Dashboard Inistate].dbo.ReceptionistUsers ADD [IsApprover] bit NOT NULL DEFAULT 0"
    )


ensure_accounts_table()

_ACCOUNT_SELECT = (
    "SELECT [Name] AS name, [Email] AS email, [PasswordHash] AS password_hash, "
    "[CreatedAt] AS created_at, [PasswordUpdatedAt] AS password_updated_at, "
    "[Approved] AS approved, [ApprovedAt] AS approved_at, [ApprovalToken] AS approval_token, "
    "[IsApprover] AS is_approver "
    f"FROM {_ACCOUNTS_TABLE}"
)


def find_account(email):
    rows = fetch_rows(
        _ACCOUNT_SELECT.replace("SELECT", "SELECT TOP (1)", 1) + " WHERE LOWER([Email]) = LOWER(?)",
        [email],
    )
    return rows[0] if rows else None


def find_account_by_token(token):
    rows = fetch_rows(
        _ACCOUNT_SELECT.replace("SELECT", "SELECT TOP (1)", 1) + " WHERE [ApprovalToken] = ? AND [Approved] = 0",
        [token],
    )
    return rows[0] if rows else None


def list_pending_accounts():
    return fetch_rows(
        _ACCOUNT_SELECT.replace("SELECT", "SELECT", 1)
        + " WHERE [Approved] = 0 ORDER BY [CreatedAt] DESC"
    )


def insert_account(name, email, password_hash, created_at, approval_token):
    execute_write(
        f"INSERT INTO {_ACCOUNTS_TABLE} (Name, Email, PasswordHash, CreatedAt, Approved, ApprovalToken) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        [name, email, password_hash, created_at, approval_token],
    )


def update_account_password(email, password_hash, updated_at):
    execute_write(
        f"UPDATE {_ACCOUNTS_TABLE} SET PasswordHash = ?, PasswordUpdatedAt = ? WHERE LOWER([Email]) = LOWER(?)",
        [password_hash, updated_at, email],
    )


def approve_account(email, approved_by):
    execute_write(
        f"UPDATE {_ACCOUNTS_TABLE} SET Approved = 1, ApprovedAt = ?, ApprovedBy = ?, ApprovalToken = NULL "
        "WHERE LOWER([Email]) = LOWER(?)",
        [datetime.now(timezone.utc).isoformat(), approved_by, email],
    )


def decline_account(email):
    """A decline just removes the pending registration — they can register
    again if it was a mistake."""
    execute_write(f"DELETE FROM {_ACCOUNTS_TABLE} WHERE LOWER([Email]) = LOWER(?)", [email])


def list_all_accounts():
    return fetch_rows(_ACCOUNT_SELECT + " ORDER BY [Name]")


def set_account_approver(email, is_approver):
    execute_write(
        f"UPDATE {_ACCOUNTS_TABLE} SET IsApprover = ? WHERE LOWER([Email]) = LOWER(?)",
        [1 if is_approver else 0, email],
    )


def delete_account(email):
    execute_write(f"DELETE FROM {_ACCOUNTS_TABLE} WHERE LOWER([Email]) = LOWER(?)", [email])


# 2026-08 item 6: per-user search history — each receptionist sees only their
# OWN past lookups (not everyone's), most-recently-viewed company first, no
# repeats. Logged every time a profile is actually opened (see
# customer_profile_api), not on every keystroke of the live search.
_SEARCH_HISTORY_TABLE = "[Dashboard Inistate].dbo.ReceptionistSearchHistory"


def ensure_search_history_table():
    execute_write(
        "IF NOT EXISTS (SELECT 1 FROM [Dashboard Inistate].INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'ReceptionistSearchHistory') "
        "CREATE TABLE [Dashboard Inistate].dbo.ReceptionistSearchHistory ("
        "[ID] int IDENTITY(1,1) PRIMARY KEY, [UserEmail] nvarchar(255), "
        "[CompanyName] nvarchar(500), [ViewedAt] nvarchar(50))"
    )


ensure_search_history_table()


def record_search_history(user_email, company_name):
    execute_write(
        f"INSERT INTO {_SEARCH_HISTORY_TABLE} (UserEmail, CompanyName, ViewedAt) VALUES (?, ?, ?)",
        [user_email, company_name, datetime.now(timezone.utc).isoformat()],
    )


def get_search_history(user_email, limit=15):
    """Sorted newest-viewed-first (ViewedAt DESC — item 12's 'Z-A' sort),
    each entry carrying its own viewed_at timestamp so the UI can show
    day-of-week, date, and time next to the company name."""
    rows = fetch_rows(
        f"""
        SELECT TOP (?) CompanyName, MAX(ViewedAt) AS LastViewedAt
        FROM {_SEARCH_HISTORY_TABLE}
        WHERE LOWER(UserEmail) = LOWER(?)
        GROUP BY CompanyName
        ORDER BY MAX(ViewedAt) DESC
        """,
        [limit, user_email],
    )
    return [{"company": row["CompanyName"], "viewed_at": row["LastViewedAt"]} for row in rows]


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def csrf_valid(submitted_token):
    real_token = session.get("csrf_token")
    return bool(real_token) and secrets.compare_digest(submitted_token or "", real_token)


# In-memory brute-force throttle, keyed by (attempted email, caller IP) so
# one bad actor can't lock out a real user's account by spamming their email.
# Fine for a single-process Flask app (this dashboard's actual deployment);
# would need a shared store (Redis etc.) if this ever ran multi-process.
_LOGIN_ATTEMPT_LIMIT = 6
_LOGIN_ATTEMPT_WINDOW = timedelta(minutes=15)
_login_attempts = {}
_login_attempts_lock = threading.Lock()


def _login_throttle_key():
    identifier = (request.form.get("email") or "").strip().lower()
    return (identifier, request.remote_addr or "")


def is_login_throttled():
    key = _login_throttle_key()
    now = datetime.now(timezone.utc)
    with _login_attempts_lock:
        attempts = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_ATTEMPT_WINDOW]
        _login_attempts[key] = attempts
        return len(attempts) >= _LOGIN_ATTEMPT_LIMIT


def record_failed_login():
    key = _login_throttle_key()
    with _login_attempts_lock:
        _login_attempts.setdefault(key, []).append(datetime.now(timezone.utc))


def clear_login_attempts():
    key = _login_throttle_key()
    with _login_attempts_lock:
        _login_attempts.pop(key, None)


def validate_email_domain(email):
    domain = (email.split("@")[-1] if "@" in email else "").lower()
    return bool(_re.search(r"daxin|asq|sqpartners", domain))


def validate_password_strength(password):
    if len(password) < 8:
        return False
    return bool(
        _re.search(r"[a-z]", password)
        and _re.search(r"[A-Z]", password)
        and _re.search(r"[0-9]", password)
        and _re.search(r"[^A-Za-z0-9]", password)
    )


def current_user():
    email = session.get("user_id")
    if not email:
        return None
    return {"email": email, "name": session.get("name", email)}


def require_login():
    if not current_user():
        return redirect(url_for("login"))
    return None


def is_approver_email(email):
    email = (email or "").lower()
    if email in APPROVER_EMAILS:
        return True
    account = find_account(email)
    return bool(account and account.get("is_approver"))


def require_approver():
    user = current_user()
    if not user or not is_approver_email(user["email"]):
        return redirect(url_for("index"))
    return None


def require_super_admin():
    user = current_user()
    if not user or user["email"].lower() not in SUPER_ADMIN_EMAILS:
        return redirect(url_for("index"))
    return None




LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Receptionist Dashboard - Log in</title>
  <script>
    try {
      var pdSavedTheme = localStorage.getItem("pd_theme");
      if (pdSavedTheme === "dark" || pdSavedTheme === "light") {
        document.documentElement.setAttribute("data-theme", pdSavedTheme);
      }
    } catch (e) {}
  </script>
  <style>
    :root, body{
      color-scheme: dark;
      --void:#000000; --charcoal:#1D1D1F; --charcoal-2:#161617;
      --glass: rgba(29,29,31,.9); --glass-strong: rgba(29,29,31,.97);
      --surface-rgb: 255,255,255;
      --hairline: rgba(var(--surface-rgb),.12); --hairline-strong: rgba(var(--surface-rgb),.2);
      --ink:#F5F5F7; --ink-soft:#86868B; --muted:#6E6E73;
      --brand-blue:#2997FF; --brand-blue-soft: rgba(41,151,255,.14);
      --acc-teal:#30D158; --acc-rose:#FF453A;
      --ease: cubic-bezier(.16,1,.3,1);
      --font: -apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Segoe UI",Helvetica,Arial,sans-serif;
      --font-mono: ui-monospace,"SF Mono","Cascadia Mono",Consolas,"Liberation Mono",monospace;
    }
    @media (prefers-color-scheme: light){
      :root, body{
        color-scheme: light;
        --void:#FBFBFD; --charcoal:#FFFFFF; --charcoal-2:#F5F5F7;
        --glass: rgba(255,255,255,.92); --glass-strong: rgba(255,255,255,.98);
        --surface-rgb: 10,10,20;
        --hairline: rgba(var(--surface-rgb),.08); --hairline-strong: rgba(var(--surface-rgb),.15);
        --ink:#1D1D1F; --ink-soft:#6E6E73; --muted:#86868A;
        --brand-blue:#0071E3; --brand-blue-soft: rgba(0,113,227,.08);
        --acc-teal:#00875A; --acc-rose:#D70015;
      }
    }
    :root[data-theme="dark"], :root[data-theme="dark"] body{
      color-scheme: dark;
      --void:#000000; --charcoal:#1D1D1F; --charcoal-2:#161617;
      --glass: rgba(29,29,31,.9); --glass-strong: rgba(29,29,31,.97);
      --surface-rgb: 255,255,255;
      --hairline: rgba(var(--surface-rgb),.12); --hairline-strong: rgba(var(--surface-rgb),.2);
      --ink:#F5F5F7; --ink-soft:#86868B; --muted:#6E6E73;
      --brand-blue:#2997FF; --brand-blue-soft: rgba(41,151,255,.14);
      --acc-teal:#30D158; --acc-rose:#FF453A;
    }
    :root[data-theme="light"], :root[data-theme="light"] body{
      color-scheme: light;
      --void:#FBFBFD; --charcoal:#FFFFFF; --charcoal-2:#F5F5F7;
      --glass: rgba(255,255,255,.92); --glass-strong: rgba(255,255,255,.98);
      --surface-rgb: 10,10,20;
      --hairline: rgba(var(--surface-rgb),.08); --hairline-strong: rgba(var(--surface-rgb),.15);
      --ink:#1D1D1F; --ink-soft:#6E6E73; --muted:#86868A;
      --brand-blue:#0071E3; --brand-blue-soft: rgba(0,113,227,.08);
      --acc-teal:#00875A; --acc-rose:#D70015;
    }
    *{ box-sizing:border-box; }
    html,body{ margin:0; padding:0; }
    body{
      min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px;
      background:
        radial-gradient(900px 700px at 12% 0%, var(--brand-blue-soft), transparent 55%),
        radial-gradient(800px 650px at 90% 100%, color-mix(in srgb, var(--acc-teal) 10%, transparent), transparent 55%),
        var(--void);
      background-attachment:fixed;
      color:var(--ink); font-family:var(--font); -webkit-font-smoothing:antialiased;
      transition:background-color .3s var(--ease), color .3s var(--ease);
    }
    a{ color:var(--brand-blue); }
    .auth-card{
      position:relative; width:100%; max-width:420px; background:var(--glass-strong);
      border:1px solid var(--hairline); border-radius:20px;
      box-shadow:0 1px 2px rgba(0,0,0,.06), 0 30px 70px rgba(0,0,0,.25);
      padding:36px 34px 30px;
    }
    .brand-logos{ display:flex; align-items:center; justify-content:center; gap:14px; margin-bottom:26px; }
    .brand-logos img{ display:block; height:28px; width:auto; }
    .brand-divider{ width:1px; height:26px; background:var(--hairline-strong); }
    .brand-logos .brand-logo-light{ display:none; }
    :root[data-theme="light"] .brand-logo-dark{ display:none; }
    :root[data-theme="light"] .brand-logo-light{ display:block; }
    @media (prefers-color-scheme: light){
      .brand-logos .brand-logo-dark{ display:none; }
      .brand-logos .brand-logo-light{ display:block; }
    }
    :root[data-theme="dark"] .brand-logo-dark{ display:block; }
    :root[data-theme="dark"] .brand-logo-light{ display:none; }
    .theme-toggle{
      position:absolute; top:18px; right:18px;
      display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px;
      background:none; border:1px solid var(--hairline-strong); color:var(--ink-soft); border-radius:50%;
      cursor:pointer; transition:all .2s var(--ease); padding:0;
    }
    .theme-toggle:hover{ border-color:var(--brand-blue); color:var(--brand-blue); }
    .theme-toggle svg{ width:16px; height:16px; display:block; }
    .theme-toggle .icon-moon{ display:none; }
    :root[data-theme="light"] .theme-toggle .icon-sun{ display:none; }
    :root[data-theme="light"] .theme-toggle .icon-moon{ display:block; }
    @media (prefers-color-scheme: light){
      .theme-toggle .icon-sun{ display:none; }
      .theme-toggle .icon-moon{ display:block; }
    }
    :root[data-theme="dark"] .theme-toggle .icon-sun{ display:block; }
    :root[data-theme="dark"] .theme-toggle .icon-moon{ display:none; }
    .auth-tabs{ display:flex; background:rgba(var(--surface-rgb),.05); border:1px solid var(--hairline); border-radius:12px; padding:4px; margin-bottom:24px; }
    .auth-tab{ flex:1; text-align:center; padding:10px; border:none; background:none; color:var(--ink-soft); font:600 13px var(--font); border-radius:9px; cursor:pointer; }
    .auth-tab.active{ background:var(--brand-blue); color:#fff; }
    .auth-h{ font-size:21px; font-weight:700; letter-spacing:-.02em; margin:0 0 6px; text-align:center; }
    .auth-sub{ font-size:13px; color:var(--ink-soft); text-align:center; margin:0 0 26px; }
    .auth-panel{ display:none; }
    .auth-panel.active{ display:block; }
    .auth-field{ margin-bottom:16px; }
    .auth-field label{ display:block; font:600 11px var(--font); letter-spacing:.04em; text-transform:uppercase; color:var(--muted); margin-bottom:7px; }
    .auth-field input, .auth-field select{
      width:100%; background:rgba(var(--surface-rgb),.045); border:1px solid var(--hairline-strong); border-radius:10px;
      padding:11px 13px; color:var(--ink); font:500 14px var(--font); transition:border-color .2s var(--ease), box-shadow .2s var(--ease);
    }
    .auth-field select{ cursor:pointer; }
    .auth-field select option{ color:#1B1C1E; background:#FFFFFF; }
    .auth-field input:focus, .auth-field select:focus{ outline:none; border-color:var(--brand-blue); box-shadow:0 0 0 4px var(--brand-blue-soft); }
    .field-hint{ font-size:11px; color:var(--muted); margin-top:6px; line-height:1.4; }
    .pw-requirements{ margin-top:8px; display:flex; flex-direction:column; gap:5px; }
    .pw-req{ display:flex; align-items:center; gap:7px; font-size:12px; color:var(--muted); transition:color .15s var(--ease); }
    .pw-req-dot{ width:14px; height:14px; border-radius:50%; border:1.5px solid var(--muted); flex-shrink:0; position:relative; transition:background-color .15s var(--ease), border-color .15s var(--ease); }
    .pw-req-dot::after{ content:""; position:absolute; left:3px; top:2px; width:6px; height:3px; border-left:1.5px solid transparent; border-bottom:1.5px solid transparent; transform:rotate(-45deg); }
    .pw-req.met{ color:var(--acc-teal); }
    .pw-req.met .pw-req-dot{ background:var(--acc-teal); border-color:var(--acc-teal); }
    .pw-req.met .pw-req-dot::after{ border-left-color:#fff; border-bottom-color:#fff; }
    .pw-field{ position:relative; }
    .pw-field input{ padding-right:42px; }
    .pw-field input::-ms-reveal, .pw-field input::-ms-clear { display:none; }
    .pw-toggle{ position:absolute; top:50%; right:6px; transform:translateY(-50%); background:none; border:none; color:var(--muted); cursor:pointer; padding:6px; border-radius:6px; }
    .pw-toggle:hover{ color:var(--ink-soft); background:rgba(var(--surface-rgb),.06); }
    .pw-toggle .eye-closed{ display:none; }
    .auth-error{ display:none; border:1px solid color-mix(in srgb, var(--acc-rose) 40%, transparent); background:color-mix(in srgb, var(--acc-rose) 10%, transparent); color:var(--acc-rose); border-radius:10px; padding:11px 14px; font-size:13px; margin-bottom:16px; }
    .auth-error.show{ display:block; }
    .auth-success{ border:1px solid color-mix(in srgb, var(--acc-teal) 40%, transparent); background:color-mix(in srgb, var(--acc-teal) 10%, transparent); color:var(--acc-teal); border-radius:10px; padding:11px 14px; font-size:13px; margin-bottom:16px; }
    .auth-submit{
      width:100%; border:1px solid var(--brand-blue); border-radius:12px; background:var(--brand-blue); color:#fff;
      padding:13px 18px; font:700 14px var(--font); cursor:pointer; transition:filter .2s var(--ease); margin-top:6px;
    }
    .auth-submit:hover{ filter:brightness(1.08); }
    .auth-switch{ text-align:center; font-size:13px; color:var(--ink-soft); margin:18px 0 0; }
    .auth-forgot{ text-align:center; margin:14px 0 0; }
    .auth-forgot button{ background:none; border:none; color:var(--brand-blue); font-size:13px; font-weight:600; cursor:pointer; padding:0; }
    .auth-switch button{ background:none; border:none; color:var(--brand-blue); font-weight:700; cursor:pointer; padding:0; }
    .auth-note{ text-align:center; font-size:11px; color:var(--muted); margin-top:20px; line-height:1.5; }
  </style>
</head>
<body>
  <div class="auth-card">
    <button type="button" class="theme-toggle" id="themeToggle" aria-label="Toggle dark/light theme" title="Toggle dark/light theme">
      <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path></svg>
      <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
    </button>

    <div class="brand-logos">
      <img class="brand-logo-dark" src="data:image/png;base64,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" alt="ASQ Consulting Group">
      <img class="brand-logo-light" src="data:image/png;base64,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" alt="ASQ Consulting Group">
      <span class="brand-divider"></span>
      <img class="brand-logo-dark" src="data:image/png;base64,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" alt="daxin GLOBAL">
      <img class="brand-logo-light" src="data:image/png;base64,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" alt="daxin GLOBAL">
    </div>

    <div class="auth-tabs">
      <button type="button" class="auth-tab" id="tabLoginBtn">Log in</button>
      <button type="button" class="auth-tab" id="tabRegisterBtn">Create account</button>
    </div>
    <div class="auth-panel" id="loginPanel">
      <h1 class="auth-h">Welcome back</h1>
      <p class="auth-sub">Log in to open the receptionist dashboard.</p>
      <div class="auth-error {{ "show" if error else "" }}">{{ error }}</div>
      {% if registered %}
      <div class="auth-success">Account created — it's pending approval. You can log in once it's approved.</div>
      {% endif %}
      <form method="post" action="/login">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="auth-field">
          <label>Email</label>
          <input name="email" type="text" placeholder="you@daxin.com.my or you@asq.com.my" value="{{ prefill_email }}" autocomplete="username" required>
        </div>
        <div class="auth-field">
          <label>Password</label>
          <div class="pw-field">
            <input type="password" name="password" id="loginPassword" autocomplete="current-password" required>
            <button type="button" class="pw-toggle" data-target="loginPassword" aria-label="Show password">
              <svg class="eye-open" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              <svg class="eye-closed" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.8 21.8 0 0 1 5.06-6.94M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a21.8 21.8 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
            </button>
          </div>
        </div>
        <button type="submit" class="auth-submit">Log in</button>
      </form>
      <p class="auth-forgot"><button type="button" id="goForgot">Forgot password?</button></p>
      <p class="auth-switch">New here? <button type="button" id="goRegister">Create an account</button></p>
    </div>
    <div class="auth-panel" id="registerPanel">
      <h1 class="auth-h">Create your account</h1>
      <p class="auth-sub">Set up access to the receptionist dashboard. New accounts need approval before you can log in.</p>
      <div class="auth-error {{ "show" if register_error else "" }}">{{ register_error }}</div>
      <form method="post" action="/register">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="auth-field">
          <label>Full name</label>
          <input type="text" name="name" placeholder="Jane Tan" value="{{ register_values.name or '' }}" autocomplete="name" required>
        </div>
        <div class="auth-field">
          <label>Email</label>
          <input type="email" name="email" placeholder="you@daxin.com.my or you@asq.com.my" value="{{ register_values.email or '' }}" autocomplete="username" required>
          <div class="field-hint">Must be a Daxin, ASQ, or SQ Partners company email address.</div>
        </div>
        <div class="auth-field">
          <label>Password</label>
          <div class="pw-field">
            <input type="password" name="password" id="registerPassword" placeholder="At least 8 characters" autocomplete="new-password" required>
            <button type="button" class="pw-toggle" data-target="registerPassword" aria-label="Show password">
              <svg class="eye-open" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              <svg class="eye-closed" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.8 21.8 0 0 1 5.06-6.94M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a21.8 21.8 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
            </button>
          </div>
          <div class="pw-requirements" id="registerPasswordReqs">
            <div class="pw-req" data-rule="length"><span class="pw-req-dot"></span>At least 8 characters</div>
            <div class="pw-req" data-rule="upper"><span class="pw-req-dot"></span>One uppercase letter</div>
            <div class="pw-req" data-rule="lower"><span class="pw-req-dot"></span>One lowercase letter</div>
            <div class="pw-req" data-rule="number"><span class="pw-req-dot"></span>One number</div>
            <div class="pw-req" data-rule="symbol"><span class="pw-req-dot"></span>One symbol</div>
          </div>
        </div>
        <div class="auth-field">
          <label>Confirm password</label>
          <div class="pw-field">
            <input type="password" name="confirm" id="registerConfirm" autocomplete="new-password" required>
            <button type="button" class="pw-toggle" data-target="registerConfirm" aria-label="Show password">
              <svg class="eye-open" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              <svg class="eye-closed" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.8 21.8 0 0 1 5.06-6.94M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a21.8 21.8 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
            </button>
          </div>
        </div>
        <button type="submit" class="auth-submit">Create account</button>
      </form>
      <p class="auth-switch">Already have an account? <button type="button" id="goLogin">Log in</button></p>
    </div>
    <div class="auth-panel" id="forgotPanel">
      <h1 class="auth-h">Reset your password</h1>
      <p class="auth-sub">Use your registered company email to set a new password.</p>
      <div class="auth-error {{ "show" if forgot_error else "" }}">{{ forgot_error }}</div>
      {% if forgot_success %}
      <div class="auth-success">Password updated — log in with your new password to continue.</div>
      {% endif %}
      <form method="post" action="/forgot-password">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="auth-field">
          <label>Email</label>
          <input name="email" type="text" placeholder="you@daxin.com.my or you@asq.com.my" value="{{ forgot_email }}" autocomplete="username" required>
          <div class="field-hint">Must match the email on your existing account.</div>
        </div>
        <div class="auth-field">
          <label>New password</label>
          <div class="pw-field">
            <input type="password" name="password" id="forgotPassword" placeholder="At least 8 characters" autocomplete="new-password" required>
            <button type="button" class="pw-toggle" data-target="forgotPassword" aria-label="Show password">
              <svg class="eye-open" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              <svg class="eye-closed" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.8 21.8 0 0 1 5.06-6.94M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a21.8 21.8 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
            </button>
          </div>
          <div class="pw-requirements" id="forgotPasswordReqs">
            <div class="pw-req" data-rule="length"><span class="pw-req-dot"></span>At least 8 characters</div>
            <div class="pw-req" data-rule="upper"><span class="pw-req-dot"></span>One uppercase letter</div>
            <div class="pw-req" data-rule="lower"><span class="pw-req-dot"></span>One lowercase letter</div>
            <div class="pw-req" data-rule="number"><span class="pw-req-dot"></span>One number</div>
            <div class="pw-req" data-rule="symbol"><span class="pw-req-dot"></span>One symbol</div>
          </div>
        </div>
        <div class="auth-field">
          <label>Confirm new password</label>
          <div class="pw-field">
            <input type="password" name="confirm" id="forgotConfirm" autocomplete="new-password" required>
            <button type="button" class="pw-toggle" data-target="forgotConfirm" aria-label="Show password">
              <svg class="eye-open" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              <svg class="eye-closed" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.8 21.8 0 0 1 5.06-6.94M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a21.8 21.8 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
            </button>
          </div>
        </div>
        <button type="submit" class="auth-submit">Reset password</button>
      </form>
      <p class="auth-switch">Remembered it? <button type="button" id="goLoginFromForgot">Log in</button></p>
    </div>
  </div>

  <script>
    (function(){
      function currentTheme(){
        var explicit = document.documentElement.getAttribute("data-theme");
        if (explicit === "dark" || explicit === "light") return explicit;
        return (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
      }
      function setTheme(theme){
        document.documentElement.setAttribute("data-theme", theme);
        try { localStorage.setItem("pd_theme", theme); } catch(e) {}
      }
      document.getElementById("themeToggle").addEventListener("click", function(){
        setTheme(currentTheme() === "dark" ? "light" : "dark");
      });

      var showRegister = {{ "true" if (show_register or register_error) else "false" }};
      var showForgot = {{ "true" if (show_forgot or forgot_error or forgot_success) else "false" }};
      function switchTab(which){
        document.getElementById("loginPanel").classList.toggle("active", which === "login");
        document.getElementById("registerPanel").classList.toggle("active", which === "register");
        document.getElementById("forgotPanel").classList.toggle("active", which === "forgot");
        document.getElementById("tabLoginBtn").classList.toggle("active", which !== "register");
        document.getElementById("tabRegisterBtn").classList.toggle("active", which === "register");
      }
      document.getElementById("tabLoginBtn").addEventListener("click", function(){ switchTab("login"); });
      document.getElementById("tabRegisterBtn").addEventListener("click", function(){ switchTab("register"); });
      document.getElementById("goRegister").addEventListener("click", function(){ switchTab("register"); });
      document.getElementById("goLogin").addEventListener("click", function(){ switchTab("login"); });
      document.getElementById("goForgot").addEventListener("click", function(){ switchTab("forgot"); });
      document.getElementById("goLoginFromForgot").addEventListener("click", function(){ switchTab("login"); });
      switchTab(showForgot ? "forgot" : (showRegister ? "register" : "login"));

      document.querySelectorAll(".pw-toggle").forEach(function(btn){
        btn.addEventListener("click", function(){
          var input = document.getElementById(btn.dataset.target);
          var show = input.type === "password";
          input.type = show ? "text" : "password";
          btn.querySelector(".eye-open").style.display = show ? "none" : "inline";
          btn.querySelector(".eye-closed").style.display = show ? "inline" : "none";
          btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
        });
      });

      var PASSWORD_RULES = {
        length: function(v){ return v.length >= 8; },
        upper: function(v){ return /[A-Z]/.test(v); },
        lower: function(v){ return /[a-z]/.test(v); },
        number: function(v){ return /[0-9]/.test(v); },
        symbol: function(v){ return /[^A-Za-z0-9]/.test(v); },
      };
      function wirePasswordRequirements(inputId, reqsId){
        var input = document.getElementById(inputId);
        var reqs = document.getElementById(reqsId);
        if (!input || !reqs) return;
        var items = reqs.querySelectorAll(".pw-req");
        input.addEventListener("input", function(){
          var value = input.value;
          items.forEach(function(item){
            item.classList.toggle("met", PASSWORD_RULES[item.dataset.rule](value));
          });
        });
      }
      wirePasswordRequirements("registerPassword", "registerPasswordReqs");
      wirePasswordRequirements("forgotPassword", "forgotPasswordReqs");
    })();
  </script>
</body>
</html>"""



HOME_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Receptionist Dashboard</title>
<script>
  try {
    var pdSavedTheme = localStorage.getItem("pd_theme");
    if (pdSavedTheme === "dark" || pdSavedTheme === "light") {
      document.documentElement.setAttribute("data-theme", pdSavedTheme);
    }
  } catch (e) {}
</script>
<style>
  :root {
    --void: #f5f6f8; --charcoal: #1c1d1f; --glass: rgba(255,255,255,0.7);
    --ink: #1c1d1f; --ink-soft: #6b6d70; --brand-blue: #0a5cff;
    --border: rgba(0,0,0,0.08); --ease: cubic-bezier(.4,0,.2,1);
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  @media (prefers-color-scheme: dark) {
    :root { --void: #0d0e10; --charcoal: #f5f6f8; --ink: #f5f6f8; --ink-soft: #9a9ca0;
      --glass: rgba(30,31,34,0.7); --border: rgba(255,255,255,0.08); }
  }
  :root[data-theme="dark"] {
    --void: #0d0e10; --charcoal: #f5f6f8; --ink: #f5f6f8; --ink-soft: #9a9ca0;
    --glass: rgba(30,31,34,0.7); --border: rgba(255,255,255,0.08);
  }
  :root[data-theme="light"] {
    --void: #f5f6f8; --charcoal: #1c1d1f; --glass: rgba(255,255,255,0.7);
    --ink: #1c1d1f; --ink-soft: #6b6d70; --border: rgba(0,0,0,0.08);
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--void); font-family: var(--font); color: var(--ink); }
  .theme-toggle { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px;
    background: none; border: 1px solid var(--border); color: var(--ink-soft); border-radius: 50%;
    cursor: pointer; padding: 0; transition: all .2s var(--ease); }
  .theme-toggle:hover { border-color: var(--brand-blue); color: var(--brand-blue); }
  .theme-toggle svg { width: 16px; height: 16px; display: block; }
  .theme-toggle .icon-moon { display: none; }
  :root[data-theme="light"] .theme-toggle .icon-sun { display: none; }
  :root[data-theme="light"] .theme-toggle .icon-moon { display: block; }
  @media (prefers-color-scheme: light) {
    .theme-toggle .icon-sun { display: none; }
    .theme-toggle .icon-moon { display: block; }
  }
  :root[data-theme="dark"] .theme-toggle .icon-sun { display: block; }
  :root[data-theme="dark"] .theme-toggle .icon-moon { display: none; }
  .topbar { display: flex; align-items: center; justify-content: space-between;
    padding: 16px 28px; border-bottom: 1px solid var(--border); background: var(--glass);
    backdrop-filter: blur(20px); position: sticky; top: 0; z-index: 5; }
  .topbar h1 { font-size: 19px; font-weight: 600; margin: 0; }
  .topbar .who { display: flex; align-items: center; gap: 14px; font-size: 15px; color: var(--ink-soft); }
  .topbar a { color: var(--ink-soft); text-decoration: none; }
  .topbar a:hover { color: var(--brand-blue); }
  .layout { max-width: 1680px; margin: 0 auto; padding: 48px 24px; display: flex; gap: 28px;
    align-items: flex-start; }
  .history-panel { flex: 0 0 240px; border: 1px solid var(--border); border-radius: 14px;
    background: var(--glass); padding: 16px; position: sticky; top: 88px; }
  .history-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;
    color: var(--ink-soft); margin-bottom: 10px; }
  .history-item { display: block; width: 100%; text-align: left; border: none; background: none;
    color: var(--ink); font: inherit; padding: 8px 6px; border-radius: 8px; cursor: pointer; }
  .history-item:hover { background: var(--border); }
  .history-item-name { font-size: 15px; }
  .history-item-time { font-size: 13px; color: var(--ink-soft); margin-top: 2px; }
  main { flex: 1; min-width: 0; width: 100%; max-width: 100%; }
  @media (max-width: 720px) {
    .layout { flex-direction: column; }
    .history-panel { flex: 0 0 auto; width: 100%; position: static; }
  }
  h2 { font-size: 26px; margin: 0 0 6px; }
  .sub { color: var(--ink-soft); margin: 0 0 28px; font-size: 16px; }
  .page-header-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px;
    flex-wrap: wrap; }
  .service-filter { border: 1px solid var(--border); border-radius: 10px; background: var(--glass);
    color: var(--ink); font: inherit; font-size: 15px; padding: 9px 12px; cursor: pointer;
    max-width: 220px; align-self: flex-start; }
  .search-box { position: relative; }
  .search-box input { width: 100%; padding: 14px 16px; border-radius: 12px;
    border: 1px solid var(--border); background: var(--glass); color: var(--ink);
    font-size: 17px; outline: none; transition: border-color .15s var(--ease); }
  .search-box input:focus { border-color: var(--brand-blue); }
  .results { position: absolute; top: calc(100% + 8px); left: 0; right: 0; z-index: 20;
    display: none; flex-direction: column; gap: 4px; max-height: 360px; overflow-y: auto;
    background: var(--glass); border: 1px solid var(--border);
    border-radius: 12px; padding: 6px; box-shadow: 0 12px 32px rgba(0,0,0,0.18); }
  .results.open { display: flex; }
  .result-row { border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: background .1s var(--ease); }
  .result-row:hover { background: var(--border); }
  .result-row .name { font-weight: 600; font-size: 16px; }
  .result-row .hint { font-size: 14px; color: var(--ink-soft); margin-top: 2px; }
  .empty { color: var(--ink-soft); font-size: 16px; padding: 20px 0; }
  .pending-link { display: inline-block; margin-top: 20px; font-size: 15px; }

  .profile-area { margin-top: 18px; }
  .profile { border: 1px solid var(--border); border-radius: 16px;
    background: var(--glass); padding: 22px 24px; }
  .profile-header { font-size: 21px; font-weight: 700; margin: 0 0 16px; }
  .service-group { margin-bottom: 20px; }
  .service-label { font-size: 14px; font-weight: 700; color: var(--brand-blue); margin-bottom: 8px; }
  .owner-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
  .owner-card { border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; }
  .owner-card .role { font-size: 13px; text-transform: uppercase; letter-spacing: .04em;
    color: var(--ink-soft); margin-bottom: 6px; }
  .owner-card .field-line { font-size: 15px; color: var(--ink); margin-top: 3px; line-height: 1.6; }
  .owner-card .field-line .field-label { color: var(--ink-soft); }
  .owner-card .field-line a { color: var(--brand-blue); text-decoration: none; }
  .owner-card .field-line a:hover { text-decoration: underline; }

  .contacts-title { font-size: 15px; font-weight: 600; text-transform: uppercase; letter-spacing: .03em;
    color: var(--ink-soft); margin: 4px 0 10px; }
  table.contacts { width: 100%; border-collapse: collapse; font-size: 15px; }
  table.contacts th { text-align: left; font-size: 13px; text-transform: uppercase; letter-spacing: .03em;
    color: var(--ink-soft); padding: 6px 10px; border-bottom: 1px solid var(--border); }
  table.contacts td { padding: 9px 10px; border-bottom: 1px solid var(--border); }
  table.contacts a { color: var(--brand-blue); text-decoration: none; }
  table.contacts a:hover { text-decoration: underline; }
  .contacts-title:not(:first-of-type) { margin-top: 22px; }
  .state-pill { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 13px;
    background: var(--brand-blue); color: #fff; opacity: .85; }

  .profile-tabs-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; }
  .profile-tabs { display: flex; gap: 6px; margin-bottom: 16px; border-bottom: 1px solid var(--border); flex: 1; }
  .profile-tab { border: none; background: none; font: inherit; font-size: 15px; font-weight: 600;
    color: var(--ink-soft); padding: 10px 4px; margin-bottom: -1px; border-bottom: 2px solid transparent;
    cursor: pointer; }
  .profile-tab.active { color: var(--brand-blue); border-bottom-color: var(--brand-blue); }
  .profile-tab-panel { display: none; }
  .profile-tab-panel.active { display: block; }

  .table-scroll { overflow-x: auto; max-width: 100%; }
  table.jobs-table { white-space: nowrap; }
  .see-more-wrap { margin-top: 10px; }
  .see-more-btn { border: 1px solid var(--border); background: var(--glass); color: var(--brand-blue);
    font: inherit; font-size: 14px; font-weight: 600; padding: 7px 15px; border-radius: 8px; cursor: pointer; }
  .see-more-btn:hover { background: var(--border); }
  .other-job-check-cell { width: 1%; }
  .select-all-label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; white-space: nowrap; }
  .copy-jobs-btn { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border);
    background: var(--glass); color: var(--brand-blue); font: inherit; font-size: 13px; font-weight: 600;
    padding: 6px 12px; border-radius: 8px; cursor: pointer; }
  .copy-jobs-btn:hover { background: var(--border); }
  .copy-jobs-btn svg { width: 15px; height: 15px; display: block; }
</style>
</head>
<body>
  <div class="topbar">
    <h1>Receptionist Dashboard</h1>
    <div class="who">
      <button type="button" class="theme-toggle" id="themeToggle" aria-label="Toggle dark/light theme" title="Toggle dark/light theme">
        <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path></svg>
        <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
      </button>
      <span>{{ user.name }}</span>
      {% if is_approver %}<a href="{{ url_for('pending_approvals') }}">Pending approvals</a>{% endif %}
      {% if is_super_admin %}<a href="{{ url_for('members_page') }}">Members</a>{% endif %}
      <a href="{{ url_for('logout') }}">Log out</a>
    </div>
  </div>
  <div class="layout">
    <aside class="history-panel">
      <div class="history-title">History</div>
      <div id="historyList"><div class="empty">No searches yet.</div></div>
    </aside>
    <main>
      <div class="page-header-row">
        <div>
          <h2>Find a customer</h2>
          <p class="sub">Search by company name to see who's in charge of their account.</p>
        </div>
        <select id="serviceFilter" class="service-filter">
          <option value="">All services</option>
          {% for group in service_groups %}
            {% if group.options %}
            <optgroup label="{{ group.label }}">
              {% for opt in group.options %}
              <option value="{{ group.key }}::{{ opt.value }}" {{ "selected" if (selected_service_division == group.key and selected_service_value == opt.value) else "" }}>{{ "✓ " if opt.is_default else "" }}{{ opt.value }}</option>
              {% endfor %}
            </optgroup>
            {% else %}
            <option value="{{ group.key }}::" {{ "selected" if (selected_service_division == group.key and not selected_service_value) else "" }}>{{ group.label }} (any)</option>
            {% endif %}
          {% endfor %}
        </select>
      </div>
      <div class="search-box">
        <input type="text" id="q" placeholder="Start typing a company name..." autocomplete="off" value="{{ selected_company or query }}" />
        <div class="results" id="results"></div>
      </div>
      <div class="profile-area" id="profileArea"></div>
    </main>
  </div>
  <script>
    const input = document.getElementById("q");
    const results = document.getElementById("results");
    const profileArea = document.getElementById("profileArea");
    const serviceFilter = document.getElementById("serviceFilter");
    let timer = null;

    function hideResults() {
      results.classList.remove("open");
      results.innerHTML = "";
    }

    function renderResults(rows, q) {
      if (!rows.length) {
        if (q) {
          results.innerHTML = `<div class="empty">No customers matched "${q}".</div>`;
          results.classList.add("open");
        } else {
          hideResults();
        }
        return;
      }
      results.innerHTML = rows.map(r => `
        <div class="result-row" data-company="${r.CompanyName.replace(/"/g, '&quot;')}">
          <div class="name">${r.CompanyName}</div>
          <div class="hint">Partner: ${r.PartnerInCharge || "-"} &middot; Portfolio: ${r.PortfolioHolder || "-"}</div>
        </div>
      `).join("");
      results.classList.add("open");
      results.querySelectorAll(".result-row").forEach(row => {
        row.addEventListener("click", () => {
          input.value = row.dataset.company;
          hideResults();
          setSelectedCompanyInUrl(row.dataset.company);
          loadProfile(row.dataset.company, profileArea);
        });
      });
    }

    function setSelectedCompanyInUrl(company) {
      const url = new URL(window.location.href);
      url.searchParams.set("company", company);
      url.searchParams.delete("q");
      window.history.replaceState({}, "", url);
    }

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-box")) hideResults();
    });

    function personCard(role, p) {
      if (!p || !p.name) {
        return `<div class="owner-card"><div class="role">${role}</div><div class="field-line"><span class="field-label">Name:</span> -</div></div>`;
      }
      const nameHtml = p.email
        ? `<a href="https://teams.microsoft.com/l/chat/0/0?users=${encodeURIComponent(p.email)}" target="_blank" rel="noopener">${p.name}</a>`
        : p.name;
      const waDigits = p.mobile ? p.mobile.replace(/[^0-9]/g, "") : "";
      const mobileHtml = waDigits
        ? `<a href="https://wa.me/${waDigits}" target="_blank" rel="noopener">${p.mobile}</a>`
        : "-";
      return `
        <div class="owner-card">
          <div class="role">${role}</div>
          <div class="field-line"><span class="field-label">Name:</span> ${nameHtml}</div>
          <div class="field-line"><span class="field-label">Designation:</span> ${p.designation || "-"}</div>
          <div class="field-line"><span class="field-label">Ext No.:</span> ${p.extension || "-"}</div>
          <div class="field-line"><span class="field-label">Mobile No.:</span> ${mobileHtml}</div>
        </div>
      `;
    }

    function serviceGroupHtml(service, cards) {
      return `
        <div class="service-group">
          ${service ? `<div class="service-label">${service}</div>` : ""}
          <div class="owner-grid" style="grid-template-columns: repeat(${cards.length}, 1fr);">
            ${cards.map(c => personCard(c.label, c.person)).join("")}
          </div>
        </div>
      `;
    }

    function serviceGroupsFromAuditJobs(auditJobs, fallback, hasFilter) {
      const seen = new Set();
      const groups = [];
      for (const j of (auditJobs || [])) {
        const svc = (j.service || "").trim();
        // Item 17: some old archived TaskAll rows have the bare literal
        // Service value "Audit" instead of "Statutory Audit" (a legacy data
        // quirk, not a real distinct service) — skip it so it doesn't show
        // as its own group.
        if (!svc || svc.toUpperCase() === "AUDIT" || seen.has(svc)) continue;
        seen.add(svc);
        groups.push(serviceGroupHtml(svc, [
          { label: "Partner in charge", person: j.partner },
          { label: "Portfolio holder", person: j.portfolio },
          { label: "Staff in charge", person: j.staff },
        ]));
      }
      if (!groups.length) {
        // Item 28: when a filter is active and it has no match for this
        // customer, show empty cards rather than silently falling back to
        // this customer's unrelated default (e.g. Statutory Audit) info —
        // that fallback is only correct for the unfiltered "All services" view.
        const owners = hasFilter
          ? { partner: null, portfolio: null, staff: null }
          : fallback;
        return serviceGroupHtml(null, [
          { label: "Partner in charge", person: owners.partner },
          { label: "Portfolio holder", person: owners.portfolio },
          { label: "Staff in charge", person: owners.staff },
        ]);
      }
      return groups.join("");
    }

    function serviceGroupsFromOtherJobs(otherJobs) {
      const seen = new Set();
      const groups = [];
      for (const j of (otherJobs || [])) {
        const svc = (j.service || "").trim();
        if (!svc || seen.has(svc)) continue;
        seen.add(svc);
        groups.push(serviceGroupHtml(svc, [
          { label: "Partner in charge", person: j.partner },
          { label: "Portfolio holder", person: j.portfolio },
        ]));
      }
      return groups.join("");
    }

    function contactRow(type, c) {
      if (!c || (!c.name && !c.phone && !c.email)) return "";
      const email = c.email ? `<a href="mailto:${c.email}">${c.email}</a>` : "-";
      return `<tr><td>${type}</td><td>${c.name || "-"}</td><td>${c.phone || "-"}</td><td>${email}</td></tr>`;
    }

    function personLink(p) {
      if (!p || !p.name) return "-";
      return p.email
        ? `<a href="https://teams.microsoft.com/l/chat/0/0?users=${encodeURIComponent(p.email)}" target="_blank" rel="noopener">${p.name}</a>`
        : p.name;
    }

    function auditJobRow(j, index) {
      // Item 3 follow-up (2026-09-10): a checkbox per row, same as Other
      // Divisions -- Quek Fung confirmed both tables feed ONE shared Copy
      // button (see copyBothTablesAsImage).
      return `<tr>
        <td class="other-job-check-cell"><input type="checkbox" class="audit-job-check" data-index="${index}" checked></td>
        <td>${j.fye || "-"}</td>
        <td>${j.book_in_date || "-"}</td>
        <td>${j.job || "-"}</td>
        <td>${j.service || "-"}</td>
        <td><span class="state-pill">${j.state || "-"}</span></td>
        <td>${personLink(j.partner)}</td>
        <td>${j.team || "-"}</td>
        <td>${personLink(j.staff)}</td>
        <td>${personLink(j.portfolio)}</td>
      </tr>`;
    }

    function otherDivisionRow(j, index) {
      // Item 2 (2026-09-10): a checkbox per row, ticked by default, feeds
      // the shared "Copy" button above the tables.
      return `<tr>
        <td class="other-job-check-cell"><input type="checkbox" class="other-job-check" data-index="${index}" checked></td>
        <td>${j.division || "-"}</td>
        <td>${j.fye || "-"}</td>
        <td>${j.book_in_date || "-"}</td>
        <td>${j.job || "-"}</td>
        <td>${j.service || "-"}</td>
        <td><span class="state-pill">${j.state || "-"}</span></td>
        <td>${j.team || "-"}</td>
        <td>${personLink(j.portfolio)}</td>
      </tr>`;
    }

    let seeMoreSeq = 0;

    function jobTableHtml(items, rowFn, headerCells, emptyText) {
      if (!items || !items.length) {
        return `<div class="empty">${emptyText}</div>`;
      }
      const top = items.slice(0, 5);
      const rest = items.slice(5);
      const headHtml = `<tr>${headerCells.map(h => `<th>${h}</th>`).join("")}</tr>`;
      // rowFn gets the item's index in the FULL `items` list (not just
      // `top`/`rest`'s own slice-relative index) so otherDivisionRow's
      // checkbox data-index reliably maps back into jobs.other_jobs for the
      // Copy button, even for rows revealed by "See more".
      const topRows = top.map((item, i) => rowFn(item, i)).join("");
      let extraTbody = "";
      let btnHtml = "";
      if (rest.length) {
        const extraId = "jobExtra" + (seeMoreSeq++);
        extraTbody = `<tbody class="see-more-extra" id="${extraId}" style="display:none;">${rest.map((item, i) => rowFn(item, i + top.length)).join("")}</tbody>`;
        btnHtml = `<div class="see-more-wrap"><button type="button" class="see-more-btn" data-target="${extraId}" data-more-label="See more (${rest.length})" data-less-label="See less">See more (${rest.length})</button></div>`;
      }
      return `
        <div class="table-scroll">
          <table class="contacts jobs-table">
            <thead>${headHtml}</thead>
            <tbody>${topRows}</tbody>
            ${extraTbody}
          </table>
        </div>
        ${btnHtml}
      `;
    }

    function parseServiceFilter() {
      const raw = serviceFilter.value;
      const sepIdx = raw.indexOf("::");
      return {
        division: sepIdx === -1 ? "" : raw.slice(0, sepIdx),
        value: sepIdx === -1 ? "" : raw.slice(sepIdx + 2),
      };
    }

    let currentOpenCompany = null;

    function loadProfile(company, detail) {
      currentOpenCompany = company;
      const activeTabLabel = detail.querySelector(".profile-tab.active")?.textContent;
      detail.innerHTML = `<div class="profile"><div class="empty">Loading...</div></div>`;
      const filter = parseServiceFilter();
      const jobsQuery = "company=" + encodeURIComponent(company)
        + "&service_division=" + encodeURIComponent(filter.division)
        + "&service_value=" + encodeURIComponent(filter.value);
      Promise.all([
        fetch("/api/customer-profile?company=" + encodeURIComponent(company)).then(r => r.json()),
        fetch("/api/customer-jobs?" + jobsQuery).then(r => r.json()),
      ]).then(([p, jobs]) => {
          if (!p) {
            detail.innerHTML = `<div class="profile"><div class="empty">Couldn't load that customer.</div></div>`;
            return;
          }
          const contactRows = [
            contactRow("Director", p.contacts.director),
            contactRow("Client / Account", p.contacts.account),
            contactRow("Payment", p.contacts.payment),
            contactRow("SP / Final PIC", p.contacts.sp_final),
          ].filter(Boolean).join("");
          const auditListHtml = jobTableHtml(
            jobs.audit_jobs, auditJobRow,
            ["", `<label class="select-all-label"><input type="checkbox" class="audit-job-select-all" checked> FYE</label>`,
             "Book In Date", "Job", "Service", "State", "Partner", "Team", "Staff", "Portfolio"],
            "No Audit division tasks on file."
          );
          // Item 3 follow-up (2026-09-10): a select-all/deselect-all
          // checkbox inside the "Division" header itself, instead of a
          // separate control -- one click ticks/unticks every row.
          const otherListHtml = jobTableHtml(
            jobs.other_jobs, otherDivisionRow,
            ["", `<label class="select-all-label"><input type="checkbox" class="other-job-select-all" checked> Division</label>`,
             "FYE", "Book In Date", "Job", "Service", "State", "Team", "Portfolio"],
            "No other-division tasks on file."
          );
          const ownerSections = serviceGroupsFromAuditJobs(jobs.audit_jobs, p, !!filter.division);
          const otherOwnerSections = serviceGroupsFromOtherJobs(jobs.other_jobs);
          const tabId = "tab" + Math.floor(Math.random() * 1e9);
          detail.innerHTML = `
            <div class="profile">
              <div class="profile-header">${p.company_name}</div>
              <div class="profile-tabs-row">
                <div class="profile-tabs">
                  <button type="button" class="profile-tab active" data-tab="${tabId}-info">Customer information</button>
                  <button type="button" class="profile-tab" data-tab="${tabId}-jobs">Job status</button>
                </div>
                ${(jobs.audit_jobs && jobs.audit_jobs.length) || (jobs.other_jobs && jobs.other_jobs.length)
                  ? `<button type="button" class="copy-jobs-btn" id="${tabId}-copyBtn" style="display:none;" title="Copy ticked rows as an image, ready to paste into WhatsApp">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                      Copy
                     </button>`
                  : ""}
              </div>

              <div class="profile-tab-panel active" id="${tabId}-info">
                ${ownerSections}
                ${otherOwnerSections}
                <div class="contacts-title">Contact persons</div>
                ${contactRows
                  ? `<table class="contacts"><thead><tr><th>Contact type</th><th>Name</th><th>Phone / Mobile</th><th>Email</th></tr></thead><tbody>${contactRows}</tbody></table>`
                  : `<div class="empty">No contact persons on file.</div>`}
              </div>

              <div class="profile-tab-panel" id="${tabId}-jobs">
                <div class="contacts-title">Audit division task status</div>
                ${auditListHtml}

                <div class="contacts-title">Other divisions task status</div>
                ${otherListHtml}
              </div>
            </div>
          `;
          const copyBtnEl = document.getElementById(tabId + "-copyBtn");
          detail.querySelectorAll(".profile-tab").forEach(tab => {
            tab.addEventListener("click", () => {
              detail.querySelectorAll(".profile-tab").forEach(t => t.classList.remove("active"));
              detail.querySelectorAll(".profile-tab-panel").forEach(panel => panel.classList.remove("active"));
              tab.classList.add("active");
              document.getElementById(tab.dataset.tab).classList.add("active");
              // Item 3 (2026-09-10): Copy lives up in the tab row now, but
              // only makes sense while the Job status tab (its data source)
              // is the one showing.
              if (copyBtnEl) copyBtnEl.style.display = (tab.dataset.tab === tabId + "-jobs") ? "" : "none";
            });
          });
          if (activeTabLabel) {
            const tabToRestore = Array.from(detail.querySelectorAll(".profile-tab")).find(t => t.textContent === activeTabLabel);
            if (tabToRestore) tabToRestore.click();
          }
          detail.querySelectorAll(".see-more-btn").forEach(btn => {
            btn.addEventListener("click", () => {
              const extra = document.getElementById(btn.dataset.target);
              const expanded = extra.style.display !== "none";
              extra.style.display = expanded ? "none" : "table-row-group";
              btn.textContent = expanded ? btn.dataset.moreLabel : btn.dataset.lessLabel;
            });
          });
          const selectAllAudit = detail.querySelector(".audit-job-select-all");
          if (selectAllAudit) {
            selectAllAudit.addEventListener("change", () => {
              detail.querySelectorAll(".audit-job-check").forEach(cb => { cb.checked = selectAllAudit.checked; });
            });
          }
          const selectAllOther = detail.querySelector(".other-job-select-all");
          if (selectAllOther) {
            selectAllOther.addEventListener("change", () => {
              detail.querySelectorAll(".other-job-check").forEach(cb => { cb.checked = selectAllOther.checked; });
            });
          }
          const copyBtn = document.getElementById(tabId + "-copyBtn");
          if (copyBtn) {
            copyBtn.addEventListener("click", () => {
              // Quek Fung, 2026-09-10: one shared Copy button for both
              // tables -- ticked rows from Audit Division and Other
              // Divisions both feed the same image.
              const auditChecked = Array.from(detail.querySelectorAll(".audit-job-check:checked"))
                .map(cb => jobs.audit_jobs[parseInt(cb.dataset.index, 10)])
                .filter(Boolean);
              const otherChecked = Array.from(detail.querySelectorAll(".other-job-check:checked"))
                .map(cb => jobs.other_jobs[parseInt(cb.dataset.index, 10)])
                .filter(Boolean);
              copyBothTablesAsImage(p.company_name, auditChecked, otherChecked, copyBtn);
            });
          }
          loadHistory();
        });
    }

    // Item 2 (2026-09-10): "copy ticked rows as an image, pasteable into
    // WhatsApp" -- WhatsApp's paste box only accepts an actual image on the
    // clipboard, not text/HTML, so this draws the ticked rows onto a canvas
    // and writes that as a PNG via the Clipboard API. That API only works in
    // a secure context (HTTPS or localhost); if it's unavailable or denied
    // (e.g. this dashboard is only ever served over plain HTTP), falls back
    // to opening the image in a new tab so the person can save/copy it by
    // hand instead of silently doing nothing.
    function personName(p) {
      return (p && p.name) ? p.name : "-";
    }

    // A section's column widths are measured on its own content, so Audit
    // (9 columns) and Other Divisions (8, different ones) each size
    // naturally rather than being forced into a shared grid.
    function measureSection(cols, cellText) {
      const pad = 14;
      const ctxMeasure = document.createElement("canvas").getContext("2d");
      ctxMeasure.font = "600 13px Arial";
      const colWidths = cols.map((c, i) => {
        let w = ctxMeasure.measureText(c).width;
        for (const r of cellText) w = Math.max(w, ctxMeasure.measureText(r[i]).width);
        return Math.ceil(w) + pad * 2;
      });
      return { colWidths, width: colWidths.reduce((a, b) => a + b, 0), pad };
    }

    function drawSection(ctx, x0, y0, sectionTitle, cols, cellText, colWidths, pad) {
      const rowH = 28, headH = 32, subTitleH = 26;
      ctx.fillStyle = "#111111";
      ctx.font = "700 14px Arial";
      ctx.fillText(sectionTitle, x0, y0 + subTitleH - 8);
      const width = colWidths.reduce((a, b) => a + b, 0);
      let y = y0 + subTitleH;
      ctx.fillStyle = "#f2f2f2";
      ctx.fillRect(x0, y, width, headH);
      ctx.fillStyle = "#333333";
      ctx.font = "700 13px Arial";
      let x = x0;
      cols.forEach((c, i) => {
        ctx.fillText(c, x + pad, y + headH / 2 + 4);
        x += colWidths[i];
      });
      y += headH;
      ctx.font = "400 13px Arial";
      cellText.forEach((r, ri) => {
        const rowY = y + ri * rowH;
        if (ri % 2 === 1) { ctx.fillStyle = "#fafafa"; ctx.fillRect(x0, rowY, width, rowH); }
        ctx.fillStyle = "#222222";
        x = x0;
        r.forEach((val, ci) => {
          ctx.fillText(val, x + pad, rowY + rowH / 2 + 4);
          x += colWidths[ci];
        });
      });
      return subTitleH + headH + rowH * cellText.length;
    }

    function buildBothTablesCanvas(companyName, auditRows, otherRows) {
      // Item 4 (2026-09-10): the copied image excludes Team -- it's still
      // shown live on the dashboard table itself, just left out of what
      // gets pasted into WhatsApp.
      const auditCols = ["FYE", "Book In Date", "Job", "Service", "State", "Partner", "Staff", "Portfolio"];
      const auditText = auditRows.map(j => [
        j.fye || "-", j.book_in_date || "-", j.job || "-", j.service || "-", j.state || "-",
        personName(j.partner), personName(j.staff), personName(j.portfolio),
      ]);
      const otherCols = ["Division", "FYE", "Book In Date", "Job", "Service", "State", "Portfolio"];
      const otherText = otherRows.map(j => [
        j.division || "-", j.fye || "-", j.book_in_date || "-", j.job || "-",
        j.service || "-", j.state || "-", personName(j.portfolio),
      ]);
      const auditM = auditRows.length ? measureSection(auditCols, auditText) : null;
      const otherM = otherRows.length ? measureSection(otherCols, otherText) : null;
      const pad = 14, titleH = 40, sectionGap = 20;
      const width = Math.max(auditM ? auditM.width : 0, otherM ? otherM.width : 0) + pad * 2;

      // Measure total height first with an offscreen context (canvas
      // dimensions must be set before anything is drawn).
      const rowH = 28, headH = 32, subTitleH = 26;
      let height = titleH;
      if (auditM) height += subTitleH + headH + rowH * auditText.length + sectionGap;
      if (otherM) height += subTitleH + headH + rowH * otherText.length;
      height += pad;

      const canvas = document.createElement("canvas");
      const scale = 2; // render at 2x for a crisp paste, not a blurry one
      canvas.width = width * scale;
      canvas.height = height * scale;
      const ctx = canvas.getContext("2d");
      ctx.scale(scale, scale);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);
      ctx.fillStyle = "#111111";
      ctx.font = "700 17px Arial";
      ctx.fillText(companyName || "", pad, titleH - 12);

      let y = titleH;
      if (auditM) {
        y += drawSection(ctx, pad, y, "Audit Division Task Status", auditCols, auditText, auditM.colWidths, auditM.pad);
        y += sectionGap;
      }
      if (otherM) {
        drawSection(ctx, pad, y, "Other Divisions Task Status", otherCols, otherText, otherM.colWidths, otherM.pad);
      }
      return canvas;
    }

    function copyBothTablesAsImage(companyName, auditRows, otherRows, btn) {
      const originalHtml = btn.innerHTML;
      const flash = (label) => {
        btn.textContent = label;
        setTimeout(() => { btn.innerHTML = originalHtml; }, 1600);
      };
      if (!auditRows.length && !otherRows.length) { flash("Nothing ticked"); return; }
      const canvas = buildBothTablesCanvas(companyName, auditRows, otherRows);
      canvas.toBlob((blob) => {
        if (!blob) { flash("Couldn't build image"); return; }
        if (navigator.clipboard && window.ClipboardItem) {
          navigator.clipboard.write([new ClipboardItem({ "image/png": blob })])
            .then(() => flash("Copied!"))
            .catch(() => openImageFallback(blob, flash));
        } else {
          openImageFallback(blob, flash);
        }
      }, "image/png");
    }

    function openImageFallback(blob, flash) {
      // Clipboard image-write needs a secure context -- if that's not
      // available, open the image in a new tab so the person can still
      // right-click "Copy image" (or long-press on mobile) themselves.
      const url = URL.createObjectURL(blob);
      const win = window.open(url, "_blank");
      if (win) {
        flash("Opened image tab");
      } else {
        flash("Pop-up blocked");
      }
    }

    function runSearch(q) {
      const filter = parseServiceFilter();
      const url = new URL(window.location.href);
      url.searchParams.set("q", q);
      if (filter.division) { url.searchParams.set("service_division", filter.division); url.searchParams.set("service_value", filter.value); }
      else { url.searchParams.delete("service_division"); url.searchParams.delete("service_value"); }
      window.history.replaceState({}, "", url);
      fetch("/api/customer-lookup?q=" + encodeURIComponent(q)
        + "&service_division=" + encodeURIComponent(filter.division) + "&service_value=" + encodeURIComponent(filter.value))
        .then(r => r.json())
        .then(rows => renderResults(rows, q));
    }

    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      timer = setTimeout(() => runSearch(q), 200);
    });

    serviceFilter.addEventListener("change", () => {
      runSearch(input.value.trim());
      // Item 27: if a customer's profile is already open, its Job Status
      // tables update to reflect the newly selected filter too — not just
      // the search results list.
      if (currentOpenCompany) loadProfile(currentOpenCompany, profileArea);
    });

    function openCompanyFromHistory(company) {
      input.value = company;
      fetch("/api/customer-lookup?q=" + encodeURIComponent(company))
        .then(r => r.json())
        .then(rows => {
          renderResults(rows, company);
          const match = results.querySelector(`.result-row[data-company="${company.replace(/"/g, '&quot;')}"]`)
            || results.querySelector(".result-row");
          if (match) match.click();
        });
    }

    function formatHistoryTimestamp(isoString) {
      if (!isoString) return "";
      const d = new Date(isoString);
      if (isNaN(d.getTime())) return "";
      const dayName = d.toLocaleDateString(undefined, { weekday: "short" });
      const dd = String(d.getDate()).padStart(2, "0");
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      const yy = String(d.getFullYear()).slice(-2);
      const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
      return `${dayName}, ${dd}-${mm}-${yy}, ${time}`;
    }

    function loadHistory() {
      const historyList = document.getElementById("historyList");
      fetch("/api/search-history")
        .then(r => r.json())
        .then(entries => {
          if (!entries.length) {
            historyList.innerHTML = `<div class="empty">No searches yet.</div>`;
            return;
          }
          historyList.innerHTML = entries.map(e => `
            <button type="button" class="history-item" data-company="${e.company.replace(/"/g, '&quot;')}">
              <div class="history-item-name">${e.company}</div>
              <div class="history-item-time">${formatHistoryTimestamp(e.viewed_at)}</div>
            </button>
          `).join("");
          historyList.querySelectorAll(".history-item").forEach(btn => {
            btn.addEventListener("click", () => openCompanyFromHistory(btn.dataset.company));
          });
        });
    }

    loadHistory();
    if ("{{ selected_company }}") {
      input.value = {{ selected_company | tojson }};
      loadProfile({{ selected_company | tojson }}, profileArea);
    } else if ("{{ query }}") {
      renderResults({{ results | tojson }}, "{{ query }}");
    }

    (function() {
      function currentTheme() {
        var explicit = document.documentElement.getAttribute("data-theme");
        if (explicit === "dark" || explicit === "light") return explicit;
        return (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
      }
      function setTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        try { localStorage.setItem("pd_theme", theme); } catch (e) {}
      }
      document.getElementById("themeToggle").addEventListener("click", function() {
        setTheme(currentTheme() === "dark" ? "light" : "dark");
      });
    })();
  </script>
</body>
</html>"""


PENDING_APPROVALS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Pending approvals - Receptionist Dashboard</title>
<style>
  :root { --void: #f5f6f8; --ink: #1c1d1f; --ink-soft: #6b6d70; --brand-blue: #0a5cff;
    --border: rgba(0,0,0,0.08); --glass: rgba(255,255,255,0.7);
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  @media (prefers-color-scheme: dark) {
    :root { --void: #0d0e10; --ink: #f5f6f8; --ink-soft: #9a9ca0;
      --glass: rgba(30,31,34,0.7); --border: rgba(255,255,255,0.08); }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--void); font-family: var(--font); color: var(--ink); }
  .topbar { display: flex; align-items: center; justify-content: space-between;
    padding: 16px 28px; border-bottom: 1px solid var(--border); background: var(--glass); }
  .topbar h1 { font-size: 19px; font-weight: 600; margin: 0; }
  .topbar a { color: var(--ink-soft); text-decoration: none; }
  main { max-width: 640px; margin: 0 auto; padding: 40px 24px; }
  h2 { font-size: 24px; margin: 0 0 20px; }
  .row { display: flex; align-items: center; justify-content: space-between;
    border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin-bottom: 10px;
    background: var(--glass); }
  .row .info .name { font-weight: 600; font-size: 16px; }
  .row .info .email { font-size: 15px; color: var(--ink-soft); }
  .row button { border: none; background: var(--brand-blue); color: #fff; padding: 9px 18px;
    border-radius: 8px; font-size: 15px; cursor: pointer; }
  .empty { color: var(--ink-soft); font-size: 16px; }
</style>
</head>
<body>
  <div class="topbar">
    <h1>Pending approvals</h1>
    <a href="{{ url_for('index') }}">Back to dashboard</a>
  </div>
  <main>
    <h2>Accounts awaiting approval</h2>
    {% if not pending %}
      <div class="empty">No accounts waiting right now.</div>
    {% endif %}
    {% for p in pending %}
      <div class="row">
        <div class="info">
          <div class="name">{{ p.name }}</div>
          <div class="email">{{ p.email }}</div>
        </div>
        <form method="post" action="{{ url_for('approve_account_route') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
          <input type="hidden" name="email" value="{{ p.email }}" />
          <button type="submit">Approve</button>
        </form>
      </div>
    {% endfor %}
  </main>
</body>
</html>"""


MEMBERS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Members - Receptionist Dashboard</title>
<style>
  :root { --void: #f5f6f8; --ink: #1c1d1f; --ink-soft: #6b6d70; --brand-blue: #0a5cff;
    --acc-rose: #ff453a; --acc-teal: #34c77b;
    --border: rgba(0,0,0,0.08); --glass: rgba(255,255,255,0.7);
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  @media (prefers-color-scheme: dark) {
    :root { --void: #0d0e10; --ink: #f5f6f8; --ink-soft: #9a9ca0;
      --glass: rgba(30,31,34,0.7); --border: rgba(255,255,255,0.08); }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--void); font-family: var(--font); color: var(--ink); }
  .topbar { display: flex; align-items: center; justify-content: space-between;
    padding: 16px 28px; border-bottom: 1px solid var(--border); background: var(--glass); }
  .topbar h1 { font-size: 19px; font-weight: 600; margin: 0; }
  .topbar a { color: var(--ink-soft); text-decoration: none; }
  main { max-width: 860px; margin: 0 auto; padding: 40px 24px; }
  h2 { font-size: 24px; margin: 0 0 20px; }
  .row { display: flex; align-items: center; justify-content: space-between; gap: 16px;
    border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin-bottom: 10px;
    background: var(--glass); flex-wrap: wrap; }
  .row .info .name { font-weight: 600; font-size: 16px; }
  .row .info .email { font-size: 14px; color: var(--ink-soft); }
  .badges { display: flex; gap: 6px; margin-top: 4px; }
  .badge { font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 999px; text-transform: uppercase; letter-spacing: .03em; }
  .badge.approved { background: color-mix(in srgb, var(--acc-teal) 15%, transparent); color: var(--acc-teal); }
  .badge.pending { background: color-mix(in srgb, #f0a020 15%, transparent); color: #f0a020; }
  .badge.approver { background: color-mix(in srgb, var(--brand-blue) 15%, transparent); color: var(--brand-blue); }
  .actions { display: flex; gap: 8px; flex-shrink: 0; }
  .actions button { border: none; padding: 9px 16px; border-radius: 8px; font-size: 14px; cursor: pointer; font-weight: 600; }
  .btn-approver { background: var(--brand-blue); color: #fff; }
  .btn-revoke { background: transparent; border: 1px solid var(--border) !important; color: var(--ink); }
  .btn-delete { background: var(--acc-rose); color: #fff; }
  .empty { color: var(--ink-soft); font-size: 16px; }
</style>
</head>
<body>
  <div class="topbar">
    <h1>Members</h1>
    <a href="{{ url_for('index') }}">Back to dashboard</a>
  </div>
  <main>
    <h2>All registered members</h2>
    {% if not members %}
      <div class="empty">No members yet.</div>
    {% endif %}
    {% for m in members %}
      <div class="row">
        <div class="info">
          <div class="name">{{ m.name }}</div>
          <div class="email">{{ m.email }}</div>
          <div class="badges">
            {% if m.approved %}<span class="badge approved">Approved</span>{% else %}<span class="badge pending">Pending</span>{% endif %}
            {% if m.is_approver or m.email.lower() in approver_emails %}<span class="badge approver">Approver</span>{% endif %}
          </div>
        </div>
        <div class="actions">
          {% if m.email.lower() not in bootstrap_approver_emails %}
            <form method="post" action="{{ url_for('set_member_approver_route') }}">
              <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
              <input type="hidden" name="email" value="{{ m.email }}" />
              {% if m.is_approver %}
                <input type="hidden" name="make_approver" value="0" />
                <button type="submit" class="btn-revoke">Revoke approver</button>
              {% else %}
                <input type="hidden" name="make_approver" value="1" />
                <button type="submit" class="btn-approver">Make approver</button>
              {% endif %}
            </form>
          {% endif %}
          <form method="post" action="{{ url_for('delete_member_route') }}" onsubmit="return confirm('Delete {{ m.name }} ({{ m.email }})? This cannot be undone.');">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
            <input type="hidden" name="email" value="{{ m.email }}" />
            <button type="submit" class="btn-delete">Delete</button>
          </form>
        </div>
      </div>
    {% endfor %}
  </main>
</body>
</html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    registered = request.args.get("registered") == "1"
    prefill_email = request.args.get("email", "")
    # Landing back on the login page (e.g. via the Back button) while still
    # authenticated force-logs-out instead of just showing the form — this is
    # a shared front-desk computer, so seeing the login screen should always
    # mean "no one is logged in," not "someone still is, just on this page."
    if request.method == "GET" and current_user():
        session.clear()
    if request.method == "POST":
        if not csrf_valid(request.form.get("csrf_token")):
            error = "Your session expired — please try again."
        elif is_login_throttled():
            error = "Too many failed attempts. Please wait 15 minutes and try again."
        else:
            identifier = request.form.get("email", "").strip()
            password = request.form.get("password", "")

            account = find_account(identifier)
            if account and check_password_hash(account["password_hash"], password):
                if not account["approved"]:
                    error = "Your account is still waiting for approval."
                else:
                    clear_login_attempts()
                    session.clear()
                    session.permanent = True
                    session["user_id"] = account["email"]
                    session["name"] = account["name"]
                    return redirect(url_for("index"))
            else:
                record_failed_login()
                error = "Invalid email or password."
    return render_template_string(LOGIN_PAGE, error=error, registered=registered, prefill_email=prefill_email,
                                   register_error="", register_values={}, show_register=False,
                                   forgot_error="", forgot_success=False, forgot_email="", show_forgot=False,
                                   csrf_token=get_csrf_token())


@app.route("/register", methods=["POST"])
def register():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    values = {"name": name, "email": email}

    def fail(message):
        return render_template_string(LOGIN_PAGE, error="", registered=False, prefill_email="",
                                       register_error=message, register_values=values, show_register=True,
                                       forgot_error="", forgot_success=False, forgot_email="", show_forgot=False,
                                       csrf_token=get_csrf_token())

    if not csrf_valid(request.form.get("csrf_token")):
        return fail("Your session expired — please try again.")
    if not name or not email or not password:
        return fail("Please fill in every field.")
    if not _re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        return fail("Enter a valid email address.")
    if not validate_email_domain(email):
        return fail("Please use your Daxin, ASQ, or SQ Partners company email address.")
    if not validate_password_strength(password):
        return fail("Password must be at least 8 characters and include an uppercase letter, a lowercase letter, a number, and a special character.")
    if password != confirm:
        return fail("Passwords don't match.")
    if find_account(email):
        return fail("An account with that email already exists — try logging in.")

    token = secrets.token_urlsafe(32)
    insert_account(name, email, generate_password_hash(password), datetime.now(timezone.utc).isoformat(), token)
    try:
        send_approval_request_email(name, email, token, PUBLIC_BASE_URL)
    except Exception:
        # Registration itself still succeeds — approvers can still act from
        # inside the dashboard's Pending approvals page even if the email
        # notification fails to send (e.g. a transient SMTP hiccup).
        pass
    return redirect(url_for("login", registered=1, email=email))


@app.route("/forgot-password", methods=["POST"])
def forgot_password():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    def fail(message):
        return render_template_string(LOGIN_PAGE, error="", registered=False, prefill_email="",
                                       register_error="", register_values={}, show_register=False,
                                       forgot_error=message, forgot_success=False, forgot_email=email, show_forgot=True,
                                       csrf_token=get_csrf_token())

    if not csrf_valid(request.form.get("csrf_token")):
        return fail("Your session expired — please try again.")
    if not email or not password or not confirm:
        return fail("Please fill in every field.")
    account = find_account(email)
    if not account:
        return fail("No account found for that email address.")
    if not validate_password_strength(password):
        return fail("Password must be at least 8 characters and include an uppercase letter, a lowercase letter, a number, and a special character.")
    if password != confirm:
        return fail("Passwords don't match.")

    update_account_password(email, generate_password_hash(password), datetime.now(timezone.utc).isoformat())
    return render_template_string(LOGIN_PAGE, error="", registered=False, prefill_email=email,
                                   register_error="", register_values={}, show_register=False,
                                   forgot_error="", forgot_success=True, forgot_email="", show_forgot=True,
                                   csrf_token=get_csrf_token())


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def _normalize_company_key(name):
    """dbo.Customer has real duplicate rows for the same company — e.g. 'VORK
    TECHNOLOGIES (MALAYSIA) SDN. BHD.' (fully populated) alongside a blank
    stub 'VORK TECHNOLOGIES (MALAYSIA) SDN BHD' (no punctuation, no Partner/
    Portfolio) — a known data issue (see customer_module_duplicate_review_for_
    norhayati_ismail.xlsx), not something this read-only app can fix at the
    source. Normalizing by stripping punctuation/whitespace lets the search
    treat these as one company and only show the more complete row."""
    name = (name or "").upper()
    name = _re.sub(r"[^\w\s]", "", name)
    return " ".join(name.split())


# Item 23/26: the flat Service dropdown mixed every TaskAll Service value
# together, regardless of which division it actually belongs to. Split it
# into the same 4 divisions the Job Status tabs use — Audit, Tax, Accounting,
# Secretarial — each pulling from its own table/field per Quek Fung's spec:
#   AUD -> TaskAll.[Service]: Statutory Audit / Transfer Pricing are the two
#          defaults (ticked in the dropdown); Quek Fung asked whether OTHER
#          audit-type services should also be listed under Audit rather than
#          Accounting — Internal Audit, LMW Audit, Sijil Audit, Details of
#          Special Audit, Agreed Upon Procedures, and Certified True Copy
#          (CTC) are all real, used Service values that are clearly
#          audit-adjacent, so they're grouped under Audit too (just without
#          the tick, since they're not the 2 defaults).
#   TAX -> TaskTax.[Job] (all values)
#   ACC -> TaskAll.[Service], everything else non-audit (MBRS, Winding Up, etc.)
#   SEC -> TaskCosec.[Job] — turns out this column is blank on every single
#          one of TaskCosec's 2222 rows, so there's nothing to list; Secretarial
#          shows as one catch-all option instead of a real breakdown.
_DEFAULT_AUDIT_SERVICES = ("Statutory Audit", "Transfer Pricing")
_OTHER_AUDIT_SERVICES = (
    "Internal Audit", "LMW Audit", "Sijil Audit", "Details of Special Audit",
    "Agreed Upon Procedures", "Certified True Copy (CTC)",
)
# Item 23's spec: "Tax: use Job: Default as Tax Compliance" — same tick
# treatment as Audit's 2 defaults.
_DEFAULT_TAX_JOB = "Tax Compliance"


def get_service_filter_groups():
    all_audit_services = _DEFAULT_AUDIT_SERVICES + _OTHER_AUDIT_SERVICES
    placeholders = ", ".join("?" for _ in all_audit_services)
    audit_rows = fetch_rows(
        f"SELECT DISTINCT [Service] FROM dbo.TaskAll ta "
        f"WHERE ta.[Service] IN ({placeholders}) ORDER BY [Service]",
        list(all_audit_services),
    )
    audit_services = [
        {"value": row["Service"], "is_default": row["Service"] in _DEFAULT_AUDIT_SERVICES}
        for row in audit_rows
    ]
    acc_rows = fetch_rows(
        f"SELECT DISTINCT [Service] FROM dbo.TaskAll ta "
        f"WHERE ta.[Service] IS NOT NULL AND LTRIM(RTRIM(ta.[Service])) != '' "
        f"AND NOT {_AUDIT_DIVISION_FILTER_SQL} AND ta.[Service] NOT IN ({placeholders}) "
        "ORDER BY [Service]",
        list(all_audit_services),
    )
    acc_services = [{"value": row["Service"], "is_default": False} for row in acc_rows]
    tax_rows = fetch_rows(
        "SELECT DISTINCT [Job] FROM dbo.TaskTax "
        "WHERE [Job] IS NOT NULL AND LTRIM(RTRIM([Job])) != '' "
        "ORDER BY [Job]"
    )
    tax_jobs = [{"value": row["Job"], "is_default": row["Job"] == _DEFAULT_TAX_JOB} for row in tax_rows]
    return [
        {"key": "AUD", "label": "Audit", "options": audit_services},
        {"key": "TAX", "label": "Tax", "options": tax_jobs},
        {"key": "ACC", "label": "Accounting", "options": acc_services},
        {"key": "SEC", "label": "Secretarial", "options": []},
    ]


def _customer_lookup(query, service_division="", service_value=""):
    query = (query or "").strip()
    service_division = (service_division or "").strip().upper()
    service_value = (service_value or "").strip()
    has_filter = service_division in ("AUD", "TAX", "ACC", "SEC")
    if len(query) < 1 and not has_filter:
        return []
    # Prefix match ("starts with"), not "contains anywhere" — a broad
    # contains-match on a common single letter (e.g. "a") can match thousands
    # of rows firm-wide, and since results are capped at TOP (60), a handful
    # of junk company names with a stray leading space/tab (which sort before
    # real letters) could crowd out every real match before the cap even
    # reaches them. Prefix matching keeps the candidate set tight from the
    # first letter typed.
    like_text = f"{query}%"
    params = [like_text]
    service_filter_sql = ""
    if service_division in ("AUD", "ACC"):
        service_filter_sql = """
          AND EXISTS (
              SELECT 1 FROM dbo.TaskAll ta
              WHERE UPPER(LTRIM(RTRIM(CAST(ta.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(CAST(c.[Company Name] AS nvarchar(500)))))
                {service_value_clause}
          )
        """.format(service_value_clause="AND ta.[Service] = ?" if service_value else "")
        if service_value:
            params.append(service_value)
    elif service_division == "TAX":
        service_filter_sql = """
          AND EXISTS (
              SELECT 1 FROM dbo.TaskTax tt
              WHERE UPPER(LTRIM(RTRIM(CAST(tt.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(CAST(c.[Company Name] AS nvarchar(500)))))
                {job_value_clause}
          )
        """.format(job_value_clause="AND tt.[Job] = ?" if service_value else "")
        if service_value:
            params.append(service_value)
    elif service_division == "SEC":
        # TaskCosec has no usable [Job] breakdown (blank on every row — see
        # get_service_filter_groups), so Secretarial is just "has any open
        # TaskCosec case", no sub-value to match.
        service_filter_sql = """
          AND EXISTS (
              SELECT 1 FROM dbo.TaskCosec tc
              WHERE UPPER(LTRIM(RTRIM(CAST(tc.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(CAST(c.[Company Name] AS nvarchar(500)))))
          )
        """
    rows = fetch_rows(
        f"""
        SELECT TOP (60)
            c.[Company Name] AS CompanyName,
            (
                SELECT TOP (1) ta.[Partner In Charge]
                FROM dbo.TaskAll ta
                WHERE UPPER(LTRIM(RTRIM(CAST(ta.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(CAST(c.[Company Name] AS nvarchar(500)))))
                ORDER BY ta.[Financial Year End] DESC, ta.[Doc Date] DESC
            ) AS PartnerInCharge,
            (
                SELECT TOP (1) ta.[Portfolio Holder]
                FROM dbo.TaskAll ta
                WHERE UPPER(LTRIM(RTRIM(CAST(ta.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(CAST(c.[Company Name] AS nvarchar(500)))))
                ORDER BY ta.[Financial Year End] DESC, ta.[Doc Date] DESC
            ) AS PortfolioHolder,
            (
                SELECT TOP (1) ta.[Staff In Charge]
                FROM dbo.TaskAll ta
                WHERE UPPER(LTRIM(RTRIM(CAST(ta.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(CAST(c.[Company Name] AS nvarchar(500)))))
                ORDER BY ta.[Financial Year End] DESC, ta.[Doc Date] DESC
            ) AS StaffInCharge
        FROM dbo.Customer c
        WHERE c.[Company Name] IS NOT NULL
          AND LTRIM(RTRIM(c.[Company Name])) != ''
          AND UPPER(LTRIM(RTRIM(CAST(c.[Company Name] AS nvarchar(500))))) LIKE UPPER(?)
          {service_filter_sql}
        ORDER BY LTRIM(RTRIM(c.[Company Name]))
        """,
        params,
    )

    best_by_key = {}
    for row in rows:
        key = _normalize_company_key(row["CompanyName"])
        completeness = sum(
            1 for field in ("PartnerInCharge", "PortfolioHolder", "StaffInCharge")
            if (row.get(field) or "").strip()
        )
        current_best = best_by_key.get(key)
        if current_best is None or completeness > current_best[0]:
            best_by_key[key] = (completeness, row)

    deduped = [entry[1] for entry in best_by_key.values()]
    deduped.sort(key=lambda r: r["CompanyName"])
    return deduped[:20]


def _staff_lookup(name):
    """Staff names on Customer/TaskAll are free-text, so match by containment
    rather than equality — StaffName often includes a title/suffix the task
    tables don't repeat exactly."""
    name = (name or "").strip()
    if not name:
        return None
    rows = fetch_rows(
        """
        SELECT TOP (1) [StaffName] AS name, [Username] AS email,
            [Designation] AS designation, [ExtensionNumber] AS extension, [OfficeMobileNo] AS mobile
        FROM dbo.Staff
        WHERE UPPER(LTRIM(RTRIM(CAST([StaffName] AS nvarchar(255))))) = UPPER(LTRIM(RTRIM(?)))
           OR UPPER(LTRIM(RTRIM(CAST([StaffName] AS nvarchar(255))))) LIKE UPPER(?)
        ORDER BY CASE WHEN [Username] IS NOT NULL AND LTRIM(RTRIM([Username])) != '' THEN 0 ELSE 1 END
        """,
        [name, f"%{name}%"],
    )
    if rows:
        row = rows[0]
        return {
            "name": name,
            "email": row["email"],
            "designation": row["designation"],
            "extension": (row["extension"] or "").strip() or None,
            "mobile": row["mobile"],
        }
    return {"name": name, "email": None, "designation": None, "extension": None, "mobile": None}


def _customer_profile(company_name):
    company_name = (company_name or "").strip()
    if not company_name:
        return None
    rows = fetch_rows(
        """
        SELECT TOP (1)
            c.[Company Name] AS CompanyName,
            c.[Partner In Charge] AS PartnerInCharge,
            c.[Portfolio Holder] AS PortfolioHolder,
            c.[Director PIC] AS DirectorName, c.[Director PIC Email] AS DirectorEmail, c.[Director PIC Phone No] AS DirectorPhone,
            c.[Account PIC] AS AccountName, c.[Account PIC Email] AS AccountEmail, c.[Account PIC Phone No] AS AccountPhone,
            c.[Payment PIC] AS PaymentName, c.[Payment PIC Email] AS PaymentEmail, c.[Payment PIC Phone No] AS PaymentPhone,
            c.[SP/ Final PIC] AS SPName, c.[SP/ Final PIC Email] AS SPEmail, c.[SP/ Final PIC Phone No] AS SPPhone,
            (
                SELECT TOP (1) ta.[Staff In Charge]
                FROM dbo.TaskAll ta
                WHERE UPPER(LTRIM(RTRIM(CAST(ta.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(CAST(c.[Company Name] AS nvarchar(500)))))
                ORDER BY ta.[Financial Year End] DESC, ta.[Doc Date] DESC
            ) AS StaffInCharge
        FROM dbo.Customer c
        WHERE UPPER(LTRIM(RTRIM(CAST(c.[Company Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(?)))
        """,
        [company_name],
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "company_name": row["CompanyName"],
        "partner": _staff_lookup(row["PartnerInCharge"]),
        "portfolio": _staff_lookup(row["PortfolioHolder"]),
        "staff": _staff_lookup(row["StaffInCharge"]),
        "contacts": {
            "director": {"name": row["DirectorName"], "phone": row["DirectorPhone"], "email": row["DirectorEmail"]},
            "account": {"name": row["AccountName"], "phone": row["AccountPhone"], "email": row["AccountEmail"]},
            "payment": {"name": row["PaymentName"], "phone": row["PaymentPhone"], "email": row["PaymentEmail"]},
            "sp_final": {"name": row["SPName"], "phone": row["SPPhone"], "email": row["SPEmail"]},
        },
    }


# "Audit Division" vs "Other Divisions" (2026-08, item 4): TaskAll has no
# literal Division column shared across all rows — Firm='Audit' turned out to
# be stale/legacy data (only 50 rows, capped at Jan 2022), not the real audit
# jobs, which are actually spread across several Firm entities (SQ Partners,
# Daxin Malaysia, ECOVIS, etc). So "Audit Division" is identified by JOB TYPE
# instead (Statutory Audit / Transfer Pricing), and "Other Divisions" is
# everything else, labelled by that row's own [Firm] value.
_AUDIT_DIVISION_FILTER_SQL = (
    "(ta.[Job] = 'Audit' OR ta.[Job] LIKE 'Transfer Pricing%' "
    "OR ta.[Service] IN ('Statutory Audit', 'Transfer Pricing'))"
)


# Item 21: job status tables should exclude Archived/Cancelled tasks — those
# are done-and-dusted history, not something a receptionist needs to see when
# checking who's currently handling a customer's work.
def _ACTIVE_STATE_FILTER_SQL(alias=""):
    return f"{alias}[State] NOT IN ('Archived', 'Cancelled')"


def _customer_jobs(company_name, service_division="", service_value=""):
    company_name = (company_name or "").strip()
    service_division = (service_division or "").strip().upper()
    service_value = (service_value or "").strip()
    if not company_name:
        return {"audit_jobs": [], "other_division_job": None, "other_jobs": []}

    audit_rows = fetch_rows(
        f"""
        SELECT
            ta.[Financial Year End] AS FYE, ta.[Date Of Commence] AS BookInDate,
            ta.[Job] AS Job, ta.[Service] AS Service, ta.[State] AS State,
            ta.[Partner In Charge] AS PartnerInCharge, ta.[Team In Charge] AS TeamInCharge,
            ta.[Staff In Charge] AS StaffInCharge, ta.[Portfolio Holder] AS PortfolioHolder
        FROM dbo.TaskAll ta
        WHERE UPPER(LTRIM(RTRIM(CAST(ta.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(?)))
          AND {_AUDIT_DIVISION_FILTER_SQL}
          AND {_ACTIVE_STATE_FILTER_SQL("ta.")}
        ORDER BY ta.[Financial Year End] DESC
        """,
        [company_name],
    )
    # Item 27: when a Service filter is active, the currently-open profile's
    # Job Status tables narrow to match it too, not just the search list.
    if service_division and service_division != "AUD":
        audit_rows = []
    elif service_division == "AUD" and service_value:
        audit_rows = [row for row in audit_rows if row["Service"] == service_value]

    # Item 4 asked to show Other Divisions "up to Portfolio Holder only" (no
    # Staff In Charge) in the table, "only the latest" row. Item 7's follow-up
    # asked for the same Partner/Portfolio-holder-card summary concept applied
    # to Other Divisions too — that needs full history (grouped by Service in
    # the frontend, same as Audit), so fetch all rows, not just the latest one.
    #
    # Item 22: "Other Divisions" must include Secretarial (Sec) and Tax work,
    # not just whatever non-audit rows happen to sit in dbo.TaskAll. TaskAll's
    # own [Job] breakdown is almost entirely 'Audit'/'MBRS Preparation'/
    # 'Transfer Pricing' — real Tax and Secretarial casework lives in their
    # own dedicated tables (dbo.TaskTax, dbo.TaskCosec) and never gets a
    # mirrored row in TaskAll, so it was silently missing before. Union all
    # three sources into one shape. (Consultancy-type engagements — e.g.
    # Share Valuation / Due Diligence, FIC/Pioneer Status — don't have a
    # dedicated task table; they're tracked directly in TaskAll and were
    # already included via the "not audit" branch below.)
    other_rows = fetch_rows(
        f"""
        SELECT
            'ACC' AS Source, ta.[Firm] AS Division, ta.[Financial Year End] AS FYE, ta.[Date Of Commence] AS BookInDate,
            ta.[Job] AS Job, ta.[Service] AS Service, ta.[State] AS State,
            ta.[Partner In Charge] AS PartnerInCharge, ta.[Team In Charge] AS TeamInCharge,
            ta.[Portfolio Holder] AS PortfolioHolder
        FROM dbo.TaskAll ta
        WHERE UPPER(LTRIM(RTRIM(CAST(ta.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(?)))
          AND NOT {_AUDIT_DIVISION_FILTER_SQL}
          AND {_ACTIVE_STATE_FILTER_SQL("ta.")}

        UNION ALL

        SELECT
            'TAX' AS Source, 'Tax' AS Division, tt.[Financial Year End] AS FYE, tt.[Date Book-In] AS BookInDate,
            tt.[Job] AS Job, tt.[Service] AS Service, tt.[State] AS State,
            CAST(NULL AS nvarchar(255)) AS PartnerInCharge, tt.[Team In Charge] AS TeamInCharge,
            tt.[Portfolio Reviewer] AS PortfolioHolder
        FROM dbo.TaskTax tt
        WHERE UPPER(LTRIM(RTRIM(CAST(tt.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(?)))
          AND tt.[State] NOT IN ('Cancelled', 'Closed')

        UNION ALL

        SELECT
            'SEC' AS Source, 'Secretarial' AS Division, tc.[Financial Year End] AS FYE, CAST(NULL AS datetime) AS BookInDate,
            tc.[Job] AS Job, CAST(NULL AS nvarchar(255)) AS Service, tc.[State] AS State,
            CAST(NULL AS nvarchar(255)) AS PartnerInCharge, tc.[Team In Charge] AS TeamInCharge,
            CAST(NULL AS nvarchar(255)) AS PortfolioHolder
        FROM dbo.TaskCosec tc
        WHERE UPPER(LTRIM(RTRIM(CAST(tc.[Customer Name] AS nvarchar(500))))) = UPPER(LTRIM(RTRIM(?)))
          AND tc.[State] NOT IN ('Cancelled', 'Closed')

        ORDER BY FYE DESC
        """,
        [company_name, company_name, company_name],
    )

    if service_division == "AUD":
        other_rows = []
    elif service_division in ("TAX", "SEC"):
        other_rows = [row for row in other_rows if row["Source"] == service_division]
        if service_division == "TAX" and service_value:
            other_rows = [row for row in other_rows if row["Job"] == service_value]
    elif service_division == "ACC":
        other_rows = [row for row in other_rows if row["Source"] == "ACC"]
        if service_value:
            other_rows = [row for row in other_rows if row["Service"] == service_value]

    def _other_job_dict(row):
        return {
            "division": row["Division"],
            # FYE comes from 3 different tables — TaskAll's is a plain date,
            # but TaskTax/TaskCosec's is a datetime (always midnight) — format
            # both the same way so the UI never shows a stray "T00:00:00".
            # dd-mm-yy per item 1 (2026-09-10).
            "fye": row["FYE"].strftime("%d-%m-%y") if row["FYE"] else None,
            "book_in_date": row["BookInDate"].strftime("%d-%m-%y") if row["BookInDate"] else None,
            "job": row["Job"], "service": row["Service"], "state": row["State"],
            "partner": _staff_lookup(row["PartnerInCharge"]),
            "team": row["TeamInCharge"],
            "portfolio": _staff_lookup(row["PortfolioHolder"]),
        }

    other_jobs = [_other_job_dict(row) for row in other_rows]

    return {
        "audit_jobs": [
            {
                "fye": row["FYE"].strftime("%d-%m-%y") if row["FYE"] else None,
                "book_in_date": row["BookInDate"].strftime("%d-%m-%y") if row["BookInDate"] else None,
                "job": row["Job"], "service": row["Service"], "state": row["State"],
                "partner": _staff_lookup(row["PartnerInCharge"]),
                "team": row["TeamInCharge"],
                "staff": _staff_lookup(row["StaffInCharge"]),
                "portfolio": _staff_lookup(row["PortfolioHolder"]),
            }
            for row in audit_rows
        ],
        "other_division_job": other_jobs[0] if other_jobs else None,
        "other_jobs": other_jobs,
    }


@app.route("/")
def index():
    guard = require_login()
    if guard:
        return guard
    user = current_user()
    query = request.args.get("q", "")
    selected_company = request.args.get("company", "")
    selected_service_division = request.args.get("service_division", "")
    selected_service_value = request.args.get("service_value", "")
    results = (
        _customer_lookup(query, selected_service_division, selected_service_value)
        if (query or selected_service_division) else []
    )
    is_approver = is_approver_email(user["email"])
    is_super_admin = user["email"].lower() in SUPER_ADMIN_EMAILS
    return render_template_string(
        HOME_PAGE, user=user, query=query, results=results, is_approver=is_approver, is_super_admin=is_super_admin,
        selected_company=selected_company,
        selected_service_division=selected_service_division, selected_service_value=selected_service_value,
        service_groups=get_service_filter_groups(),
    )


@app.route("/api/customer-lookup")
def customer_lookup_api():
    guard = require_login()
    if guard:
        return jsonify([])
    return jsonify(_customer_lookup(
        request.args.get("q", ""),
        request.args.get("service_division", ""),
        request.args.get("service_value", ""),
    ))


@app.route("/api/customer-profile")
def customer_profile_api():
    guard = require_login()
    if guard:
        return jsonify(None)
    company = request.args.get("company", "")
    profile = _customer_profile(company)
    if profile:
        record_search_history(current_user()["email"], profile["company_name"])
    return jsonify(profile)


@app.route("/api/customer-jobs")
def customer_jobs_api():
    guard = require_login()
    if guard:
        return jsonify({"audit_jobs": [], "other_division_job": None, "other_jobs": []})
    return jsonify(_customer_jobs(
        request.args.get("company", ""),
        request.args.get("service_division", ""),
        request.args.get("service_value", ""),
    ))


@app.route("/api/search-history")
def search_history_api():
    guard = require_login()
    if guard:
        return jsonify([])
    return jsonify(get_search_history(current_user()["email"]))


@app.route("/pending-approvals")
def pending_approvals():
    guard = require_login() or require_approver()
    if guard:
        return guard
    return render_template_string(PENDING_APPROVALS_PAGE, pending=list_pending_accounts(), csrf_token=get_csrf_token())


@app.route("/pending-approvals/approve", methods=["POST"])
def approve_account_route():
    guard = require_login() or require_approver()
    if guard:
        return guard
    if not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("pending_approvals"))
    email = request.form.get("email", "").strip()
    if email:
        account = find_account(email)
        approve_account(email, current_user()["email"])
        if account:
            try:
                send_decision_email(account["name"], account["email"], approved=True)
            except Exception:
                pass
    return redirect(url_for("pending_approvals"))


@app.route("/admin/members")
def members_page():
    guard = require_login() or require_super_admin()
    if guard:
        return guard
    return render_template_string(
        MEMBERS_PAGE, members=list_all_accounts(), approver_emails=APPROVER_EMAILS,
        bootstrap_approver_emails=APPROVER_EMAILS, csrf_token=get_csrf_token(),
    )


@app.route("/admin/members/set-approver", methods=["POST"])
def set_member_approver_route():
    guard = require_login() or require_super_admin()
    if guard:
        return guard
    if not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("members_page"))
    email = request.form.get("email", "").strip()
    make_approver = request.form.get("make_approver") == "1"
    # Bootstrap approvers (APPROVER_EMAILS) can't be revoked from here — that
    # set is hardcoded in source, not DB-driven, so this action wouldn't do
    # anything to them anyway; the template already hides the button for them.
    if email and email.lower() not in APPROVER_EMAILS:
        set_account_approver(email, make_approver)
    return redirect(url_for("members_page"))


@app.route("/admin/members/delete", methods=["POST"])
def delete_member_route():
    guard = require_login() or require_super_admin()
    if guard:
        return guard
    if not csrf_valid(request.form.get("csrf_token")):
        return redirect(url_for("members_page"))
    email = request.form.get("email", "").strip()
    # Never let this delete a super admin's own account, even by mistake —
    # there'd be no one left with access to undo it.
    if email and email.lower() not in SUPER_ADMIN_EMAILS:
        delete_account(email)
    return redirect(url_for("members_page"))


EMAIL_ACTION_RESULT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Receptionist Dashboard</title>
<style>
  body { margin: 0; background: #0d0e10; color: #f5f6f8; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .card { max-width: 420px; text-align: center; padding: 32px; }
  .card h1 { font-size: 20px; margin: 0 0 10px; }
  .card p { color: #9a9ca0; font-size: 14px; }
  a { color: #2997ff; text-decoration: none; }
  button { border: none; background: #2997ff; color: #fff; font: inherit; font-weight: 600;
    padding: 10px 22px; border-radius: 8px; cursor: pointer; margin: 6px; }
  button.decline { background: #ff453a; }
</style>
</head>
<body>
  <div class="card">
    <h1>{{ title }}</h1>
    <p>{{ message }}</p>
    {{ actions | safe }}
    <p><a href="{{ url_for('login') }}">Go to Receptionist Dashboard</a></p>
  </div>
</body>
</html>"""


# Item: registration approval emails. GET links must stay side-effect-free —
# corporate email security (Microsoft Defender "Safe Links" and similar)
# automatically pre-fetches every URL in an incoming email to scan it for
# malware, BEFORE any human opens the message. Confirmed live: a test
# registration got auto-approved within ~15 seconds of the email going out,
# with ApprovedBy recording the scanner's request, not a person. So the GET
# route only ever shows a confirmation page; the actual approve/decline is a
# separate POST that a real click on the page's button triggers — an
# automated scanner fetching the GET page never submits that form.
@app.route("/approve-via-email")
def approve_via_email():
    token = request.args.get("token", "").strip()
    account = find_account_by_token(token)
    if not account:
        return render_template_string(
            EMAIL_ACTION_RESULT_PAGE, title="Nothing to approve",
            message="This link has already been used, or the request no longer exists.",
            actions="",
        )
    actions = f"""
        <form method="post" action="{url_for('approve_via_email_confirm')}">
          <input type="hidden" name="token" value="{token}">
          <button type="submit">Approve {escape(account['name'])}</button>
        </form>
    """
    return render_template_string(
        EMAIL_ACTION_RESULT_PAGE, title="Confirm approval",
        message=f"Approve {account['name']} ({account['email']}) for the Receptionist Dashboard?",
        actions=actions,
    )


@app.route("/approve-via-email-confirm", methods=["POST"])
def approve_via_email_confirm():
    token = request.form.get("token", "").strip()
    account = find_account_by_token(token)
    if not account:
        return render_template_string(
            EMAIL_ACTION_RESULT_PAGE, title="Nothing to approve",
            message="This link has already been used, or the request no longer exists.",
            actions="",
        )
    approve_account(account["email"], "email-link")
    try:
        send_decision_email(account["name"], account["email"], approved=True)
    except Exception:
        pass
    return render_template_string(
        EMAIL_ACTION_RESULT_PAGE, title="Account approved",
        message=f"{account['name']} ({account['email']}) can now log in to the Receptionist Dashboard.",
        actions="",
    )


@app.route("/decline-via-email")
def decline_via_email():
    token = request.args.get("token", "").strip()
    account = find_account_by_token(token)
    if not account:
        return render_template_string(
            EMAIL_ACTION_RESULT_PAGE, title="Nothing to decline",
            message="This link has already been used, or the request no longer exists.",
            actions="",
        )
    actions = f"""
        <form method="post" action="{url_for('decline_via_email_confirm')}">
          <input type="hidden" name="token" value="{token}">
          <button type="submit" class="decline">Decline {escape(account['name'])}</button>
        </form>
    """
    return render_template_string(
        EMAIL_ACTION_RESULT_PAGE, title="Confirm decline",
        message=f"Decline {account['name']} ({account['email']})'s registration?",
        actions=actions,
    )


@app.route("/decline-via-email-confirm", methods=["POST"])
def decline_via_email_confirm():
    token = request.form.get("token", "").strip()
    account = find_account_by_token(token)
    if not account:
        return render_template_string(
            EMAIL_ACTION_RESULT_PAGE, title="Nothing to decline",
            message="This link has already been used, or the request no longer exists.",
            actions="",
        )
    decline_account(account["email"])
    try:
        send_decision_email(account["name"], account["email"], approved=False)
    except Exception:
        pass
    return render_template_string(
        EMAIL_ACTION_RESULT_PAGE, title="Account declined",
        message=f"{account['name']} ({account['email']})'s registration has been removed.",
        actions="",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5090, debug=False)
