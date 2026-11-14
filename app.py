"""spare: a monthly budget on coloured cards. Income, expenses, and the accounts the rest goes to."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import struct
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import segno
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

__version__ = "0.3.0"

ROOT = Path(__file__).resolve().parent

# Settings come from the environment, or from a .env file next to this one.
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _key, _sep, _value = _line.partition("=")
        if _sep and not _line.lstrip().startswith("#"):
            os.environ.setdefault(_key.strip(), _value.strip())

DATA = Path(os.environ.get("SPARE_DATA", str(ROOT / "data")))
DEV = os.environ.get("SPARE_DEV") == "1"
BASE_URL = os.environ.get("SPARE_URL", "http://127.0.0.1:8094").rstrip("/")
INVITE_DAYS = 7
LANGS = ("nb", "en")
CURRENCIES = ("NOK", "EUR", "USD")
COLOURS = ("green", "teal", "blue", "orange", "red", "purple", "yellow", "pink", "brown")
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config.update(
    SECRET_KEY=os.environ.get("SPARE_SECRET") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not DEV,
    PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  pw_hash TEXT, totp_secret TEXT, is_admin INTEGER NOT NULL DEFAULT 0,
  invite_token TEXT, invite_expires INTEGER, lang TEXT NOT NULL DEFAULT 'nb',
  currency TEXT NOT NULL DEFAULT 'NOK', theme TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL);
-- The card types a user taps or drags into a month. A type made at the start has a key and
-- no name until it is renamed, so its name follows the language switch. A new card starts at
-- the type's usual amount, and a type marked fixed comes into each new month by itself.
CREATE TABLE IF NOT EXISTS types (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  side TEXT NOT NULL, key TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
  colour TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0,
  amount REAL NOT NULL DEFAULT 0, fixed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  type_id INTEGER NOT NULL REFERENCES types(id), month TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS entries_month ON entries(user_id, month);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  key TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
  start REAL NOT NULL DEFAULT 0, savings INTEGER NOT NULL DEFAULT 0);
-- A month gets a row the first time it is opened: what the user plans to save that month.
CREATE TABLE IF NOT EXISTS months (
  user_id INTEGER NOT NULL REFERENCES users(id), month TEXT NOT NULL,
  saving REAL NOT NULL DEFAULT 0, PRIMARY KEY (user_id, month));
"""

START_TYPES = (
    ("in", "salary", "green"),
    ("in", "other_income", "teal"),
    ("out", "housing", "blue"),
    ("out", "food", "orange"),
    ("out", "restaurant", "red"),
    ("out", "clothes", "purple"),
    ("out", "transport", "yellow"),
)
START_ACCOUNTS = (("spending_account", 0), ("savings_account", 1))

# Every word on the pages, in Norwegian and English.
WORDS = {
    "setup": ("Oppsett", "Setup"),
    "users": ("Brukere", "Users"),
    "logout": ("Logg ut", "Log out"),
    "dark": ("Mørk", "Dark"),
    "light": ("Lys", "Light"),
    "login": ("Logg inn", "Log in"),
    "email": ("E-post", "Email"),
    "password": ("Passord", "Password"),
    "code": ("Koden fra appen på telefonen", "The code from the app on your phone"),
    "tagline": ("Et enkelt budsjett, måned for måned.", "A simple budget, month by month."),
    "install_win": ("Last ned for Windows", "Download for Windows"),
    "install_mac": ("Last ned for Mac", "Download for Mac"),
    "install_win_help": (
        "Åpne denne siden i Edge eller Chrome, og trykk på installer-knappen til høyre i adresselinja.",
        "Open this page in Edge or Chrome, and click the install button at the right end of the address bar.",
    ),
    "install_mac_help": (
        "I Safari: velg Arkiv, så Legg til i Dock. I Chrome: trykk på installer-knappen til høyre i adresselinja.",
        "In Safari: choose File, then Add to Dock. In Chrome: click the install button at the right end of the address bar.",
    ),
    "wrong_login": ("Feil e-post, passord eller kode.", "Wrong email, password or code."),
    "too_many": (
        "For mange feil forsøk. Vent 15 minutter.",
        "Too many failed tries. Wait 15 minutes.",
    ),
    "welcome": ("Velkommen, {name}", "Welcome, {name}"),
    "invite_intro": (
        "Velg et passord og koble en kodeapp på telefonen. Du trenger begge hver gang du logger inn.",
        (
            "Set a password and link an authenticator app on your phone."
            " You need both each time you log in."
        ),
    ),
    "pw12": ("Passord, minst 12 tegn", "Password, at least 12 characters"),
    "pw_again": ("Det samme passordet igjen", "The same password again"),
    "scan": (
        "Skann koden med en kodeapp, som Microsoft Authenticator eller Google Authenticator.",
        (
            "Scan this code with an authenticator app, such as Microsoft Authenticator"
            " or Google Authenticator."
        ),
    ),
    "or_type": ("Eller skriv denne nøkkelen inn i appen:", "Or type this key into the app:"),
    "six_digits": ("Den sekssifrede koden appen viser", "The 6-digit code the app shows"),
    "save_login": ("Lagre og logg inn", "Save and log in"),
    "invite_gone": (
        "Denne lenken er brukt eller utløpt. Be om en ny.",
        "This invite link has expired or was used. Ask for a new one.",
    ),
    "short_pw": ("Bruk minst 12 tegn.", "Use at least 12 characters."),
    "pw_differ": ("De to passordene er ulike.", "The two passwords differ."),
    "bad_code": (
        "Koden er feil. Sjekk at klokka på telefonen går riktig, og prøv neste kode.",
        "The code is wrong. Check that the phone clock is right, and try the next code.",
    ),
    "income": ("Inntekter", "Income"),
    "expenses": ("Utgifter", "Expenses"),
    "accounts": ("Kontoer", "Accounts"),
    "saved_in": ("Spart i {month}", "Saved in {month}"),
    "plan": ("Plan", "Plan"),
    "overview": ("Oversikt", "Overview"),
    "can_spend": ("Du kan bruke", "You can spend"),
    "left_to_spend": ("Igjen å bruke", "Left to spend"),
    "fixed_costs": ("Faste utgifter", "Fixed costs"),
    "saving": ("Sparing", "Saving"),
    "other_spending": ("Annet brukt", "Other spending"),
    "see_cards": ("Se alle kortene", "See all the cards"),
    "plan_help": (
        "Faste utgifter og vanlige beløp endrer du i Oppsett. Annet du bruker, legger du inn i Oversikt.",
        "Change fixed costs and usual amounts in Setup. Put other spending in Overview.",
    ),
    "rest_here": ("Resten går hit", "The rest goes here"),
    "usual_amount": ("Vanlig beløp", "Usual amount"),
    "every_month": ("Hver måned", "Every month"),
    "empty": ("Trykk på et kort over, eller dra det hit.", "Tap a card above, or drag it here."),
    "delete": ("Slett", "Delete"),
    "save": ("Lagre", "Save"),
    "this_month": ("denne måneden", "this month"),
    "savings_here": ("Det du sparer, går hit", "What you save goes here"),
    "edit_accounts": ("Endre kontoer", "Edit accounts"),
    "chart_help": (
        "Stolpene viser hva du sparte hver måned, linja summen så langt i år.",
        "The bars show what you saved each month, the line the sum so far this year.",
    ),
    "prev": ("Forrige måned", "Previous month"),
    "next": ("Neste måned", "Next month"),
    "note": ("Merknad", "Note"),
    "amount": ("Beløp", "Amount"),
    "card_types": ("Korttyper", "Card types"),
    "new_type": ("Ny korttype", "New card type"),
    "name": ("Navn", "Name"),
    "add": ("Legg til", "Add"),
    "remove": ("Fjern", "Remove"),
    "types_help": (
        (
            "Et kort merket «Hver måned» kommer inn i hver ny måned av seg selv, med det vanlige"
            " beløpet. Fjerner du en korttype, blir kortene du alt har lagt inn, stående."
        ),
        (
            'A card marked "Every month" comes into each new month by itself, at its usual amount.'
            " Removing a card type keeps the cards already in your months."
        ),
    ),
    "start": ("Startsaldo", "Starting balance"),
    "new_account": ("Ny konto", "New account"),
    "make_savings": ("La sparingen gå hit", "Send savings here"),
    "language": ("Språk", "Language"),
    "currency": ("Valuta", "Currency"),
    "change_pw": ("Bytt passord", "Change password"),
    "present_pw": ("Nåværende passord", "Present password"),
    "new_pw": ("Nytt passord, minst 12 tegn", "New password, at least 12 characters"),
    "new_pw2": ("Det nye passordet igjen", "The new password again"),
    "pw_wrong": ("Nåværende passord er feil.", "The present password is wrong."),
    "pw_changed": ("Passordet er byttet.", "Your password is changed."),
    "add_user": ("Ny bruker", "New user"),
    "user_exists": (
        "Det finnes alt en bruker med den e-posten.",
        "A user with that email already exists.",
    ),
    "invite_link": ("Innloggingslenke for {name}: {link}", "Invite link for {name}: {link}"),
    "new_link": ("Ny innloggingslenke", "New invite link"),
    "active": ("aktiv", "active"),
    "invited": ("invitert, lenken virker til {date}", "invited, the link works until {date}"),
    "no_login": ("ikke innlogget ennå", "no login yet"),
    "admin_help": (
        "En ny lenke logger personen ut og ber om nytt passord og ny kode.",
        "A new invite link logs the person out and asks for a new password and phone code.",
    ),
    "salary": ("Lønn", "Salary"),
    "other_income": ("Annen inntekt", "Other income"),
    "housing": ("Bolig", "Housing"),
    "food": ("Mat", "Food"),
    "restaurant": ("Restaurant", "Restaurant"),
    "clothes": ("Klær", "Clothes"),
    "transport": ("Transport", "Transport"),
    "spending_account": ("Brukskonto", "Spending account"),
    "savings_account": ("Sparekonto", "Savings account"),
    "green": ("Grønn", "Green"),
    "teal": ("Blågrønn", "Teal"),
    "blue": ("Blå", "Blue"),
    "orange": ("Oransje", "Orange"),
    "red": ("Rød", "Red"),
    "purple": ("Lilla", "Purple"),
    "yellow": ("Gul", "Yellow"),
    "pink": ("Rosa", "Pink"),
    "brown": ("Brun", "Brown"),
}
MONTHS = (
    (
        "januar",
        "februar",
        "mars",
        "april",
        "mai",
        "juni",
        "juli",
        "august",
        "september",
        "oktober",
        "november",
        "desember",
    ),
    (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
)
SYMBOLS = {"NOK": ("kr", "NOK "), "EUR": ("€", "€"), "USD": ("$", "$")}

FAILS = {}  # client address -> times of failed logins


def now() -> int:
    return int(time.time())


# Database


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DATA / "spare.db"), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc) -> None:
    con = g.pop("db", None)
    if con is not None:
        con.close()


def init_db() -> None:
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)  # budgets are private
    with closing(connect()) as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)


def mine(table: str, rid: int | None) -> sqlite3.Row:
    """A row of the user's own, or 404. Nobody reaches another user's data."""
    row = (
        db()
        .execute(f"SELECT * FROM {table} WHERE id = ? AND user_id = ?", (rid, current_user()["id"]))
        .fetchone()
    )
    if row is None:
        abort(404)
    return row


# Words, numbers and money in the user's language


def lang() -> str:
    me = current_user()
    value = me["lang"] if me else session.get("lang", "nb")
    return value if value in LANGS else "nb"


def t(key: str, **kw) -> str:
    text = WORDS[key][LANGS.index(lang())]
    return text.format(**kw) if kw else text


@app.template_filter("date")
def date(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


@app.template_filter("label")
def label(row) -> str:
    """The name of a card type or account: the user's own name, or the starting one translated."""
    return row["name"] or (t(row["key"]) if row["key"] in WORDS else "?")


def number(v: float) -> str:
    """12500 as 12 500 in Norwegian and 12,500 in English. Decimals only when there are any."""
    v = abs(v)
    text = f"{v:,.0f}" if abs(v - round(v)) < 0.005 else f"{v:,.2f}"
    return text.replace(",", " ").replace(".", ",") if lang() == "nb" else text


def money(v: float) -> str:
    me = current_user()
    cur = me["currency"] if me and me["currency"] in SYMBOLS else "NOK"
    sign = "−" if v < -0.004 else ""
    nb, en = SYMBOLS[cur]
    return f"{sign}{number(v)} {nb}" if lang() == "nb" else f"{sign}{en}{number(v)}"


def plain(v: float) -> str:
    """An amount as it stands in an input box: digits only, empty for 0."""
    return ("-" if v < -0.004 else "") + number(v) if v else ""


def parse_amount(text: str) -> float | None:
    """Read 32 500,50 or 32,500.50 or 1.234,5. A comma or point before one or two last digits
    is the decimal mark; any other is a thousands mark. Empty is 0, no digits is None."""
    s = re.sub(r"[^\d,.\-−]", "", text).replace("−", "-")
    if not text.strip():
        return 0.0
    m = re.fullmatch(r"(-?)([\d.,]*?)(?:[.,](\d{1,2}))?", s)
    if not m or not re.search(r"\d", s):
        return None
    whole = re.sub(r"[.,]", "", m.group(2)) or "0"
    return float(f"{m.group(1)}{whole}.{m.group(3) or 0}")


def shift(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:]) - 1 + n
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def chart(totals: dict, year: int) -> dict:
    """Bars for what each month of the year saved, and a line for the sum so far."""
    vals = [totals.get(f"{year}-{m:02d}") for m in range(1, 13)]
    have = [i for i, v in enumerate(vals) if v is not None]
    runs, run = [], 0.0
    for v in vals[: have[-1] + 1 if have else 0]:
        run += v or 0
        runs.append(run)
    seen = [v for v in vals if v is not None] + runs
    lo, hi = min([0.0] + seen), max([0.0] + seen)
    span = (hi - lo) or 1.0

    def y(v: float) -> float:
        return round(10 + (hi - v) / span * 160, 1)

    bars = []
    for i, v in enumerate(vals):
        if v is not None:
            top, bottom = sorted((y(v), y(0)))
            bars.append({"i": i, "x": 12 + i * 48, "y": top, "h": max(bottom - top, 1), "v": v})
    dots = [(24 + i * 48, y(r)) for i, r in enumerate(runs)]
    return {"bars": bars, "dots": dots, "zero": y(0), "total": run}


# Login: password plus a six-digit code from an authenticator app


def totp(secret: str, at: float | None = None) -> str:
    counter = int((time.time() if at is None else at) // 30)
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def totp_ok(secret: str | None, code: str | None) -> bool:
    code = re.sub(r"\s", "", code or "")
    if not secret or not code:
        return False
    at = time.time()
    return any(hmac.compare_digest(totp(secret, at + d * 30), code) for d in (-1, 0, 1))


def add_user(con: sqlite3.Connection, email: str, name: str, admin: bool = False) -> int:
    """Make a user with the starting card types and accounts. The caller commits."""
    email = email.strip().lower()
    uid = con.execute(
        "INSERT INTO users (email, name, is_admin, created_at) VALUES (?, ?, ?, ?)",
        (email, name.strip() or email.split("@")[0], int(admin), now()),
    ).lastrowid
    con.executemany(
        "INSERT INTO types (user_id, side, key, colour) VALUES (?, ?, ?, ?)",
        [(uid, side, key, colour) for side, key, colour in START_TYPES],
    )
    con.executemany(
        "INSERT INTO accounts (user_id, key, savings) VALUES (?, ?, ?)",
        [(uid, key, savings) for key, savings in START_ACCOUNTS],
    )
    return uid


def make_invite(con: sqlite3.Connection, uid: int) -> str:
    """Give a user a fresh invite link. It also logs out the old login."""
    token = secrets.token_urlsafe(24)
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    con.execute(
        "UPDATE users SET pw_hash = NULL, totp_secret = ?, invite_token = ?,"
        " invite_expires = ? WHERE id = ?",
        (secret, token, now() + INVITE_DAYS * 86400, uid),
    )
    con.commit()
    return f"{BASE_URL}/invite/{token}"


def current_user() -> sqlite3.Row | None:
    if "user" not in g:
        uid = session.get("uid")
        g.user = (
            db()
            .execute("SELECT * FROM users WHERE id = ? AND pw_hash IS NOT NULL", (uid,))
            .fetchone()
            if uid
            else None
        )
    return g.user


def too_many_fails(addr: str) -> bool:
    recent = [x for x in FAILS.get(addr, []) if x > time.time() - 900]
    FAILS[addr] = recent
    return len(recent) >= 10


def log_in(uid: int) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = uid


def admin_only() -> None:
    if not current_user()["is_admin"]:
        abort(403)


@app.before_request
def guard():
    if request.method == "POST":
        token = session.get("csrf", "")
        if not token or not hmac.compare_digest(token, request.form.get("csrf", "")):
            abort(400)
    if (
        request.endpoint not in ("login", "invite", "prefs", "demo", "static")
        and not current_user()
    ):
        return redirect(url_for("login"))
    return None


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; frame-ancestors 'none';"
        " form-action 'self'; base-uri 'none'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.context_processor
def template_values():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    me = current_user()
    return {
        "csrf": session["csrf"],
        "me": me,
        "now": now(),
        "t": t,
        "lang": lang(),
        "theme": me["theme"] if me else session.get("theme", ""),
        "money": money,
        "plain": plain,
        "months": MONTHS[LANGS.index(lang())],
        "COLOURS": COLOURS,
        "version": __version__,
        "this_month": this_month(),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        addr = request.remote_addr or "?"
        if too_many_fails(addr):
            flash(t("too_many"))
            return render_template("login.html"), 429
        email = request.form.get("email", "").strip().lower()
        user = (
            db()
            .execute("SELECT * FROM users WHERE email = ? AND pw_hash IS NOT NULL", (email,))
            .fetchone()
        )
        if (
            user
            and check_password_hash(user["pw_hash"], request.form.get("password", ""))
            and totp_ok(user["totp_secret"], request.form.get("code"))
        ):
            log_in(user["id"])
            return redirect(url_for("home"))
        FAILS.setdefault(addr, []).append(time.time())
        flash(t("wrong_login"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/invite/<token>", methods=["GET", "POST"])
def invite(token):
    con = db()
    user = con.execute(
        "SELECT * FROM users WHERE invite_token = ? AND invite_expires > ?", (token, now())
    ).fetchone()
    if not user:
        flash(t("invite_gone"))
        return redirect(url_for("login"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 12:
            flash(t("short_pw"))
        elif password != request.form.get("password2"):
            flash(t("pw_differ"))
        elif not totp_ok(user["totp_secret"], request.form.get("code")):
            flash(t("bad_code"))
        else:
            con.execute(
                "UPDATE users SET pw_hash = ?, invite_token = NULL, invite_expires = NULL"
                " WHERE id = ?",
                (generate_password_hash(password), user["id"]),
            )
            con.commit()
            log_in(user["id"])
            return redirect(url_for("home"))
    uri = "otpauth://totp/spare:{}?secret={}&issuer=spare".format(
        quote(user["email"]), user["totp_secret"]
    )
    qr = Markup(segno.make(uri, error="m").svg_inline(scale=5, omitsize=True))
    return render_template("invite.html", user=user, qr=qr)


@app.route("/demo")
def demo():
    """Log in as the first user without a password, for testing on your own machine.
    Off unless both SPARE_DEV=1 and SPARE_DEMO=1 are set; never set them on a server."""
    if not (DEV and os.environ.get("SPARE_DEMO") == "1"):
        abort(404)
    row = db().execute("SELECT id FROM users WHERE pw_hash IS NOT NULL ORDER BY id").fetchone()
    if row is None:
        abort(404)
    log_in(row["id"])
    return redirect(url_for("home"))


# The month: a plan of what is left to spend, and an overview of every card


def this_month() -> str:
    return time.strftime("%Y-%m")


def join_months(con: sqlite3.Connection, uid: int, tid: int, amount: float) -> None:
    """A type just made fixed joins the months already opened, from this month on."""
    con.execute(
        "INSERT INTO entries (user_id, type_id, month, amount) SELECT user_id, ?, month, ?"
        " FROM months m WHERE user_id = ? AND month >= ? AND NOT EXISTS"
        " (SELECT 1 FROM entries e WHERE e.type_id = ? AND e.month = m.month)",
        (tid, amount, uid, this_month(), tid),
    )


def open_month(con: sqlite3.Connection, uid: int, month: str) -> None:
    """The first time a month is opened, it gets the fixed cards and the saving of the month
    before. A card deleted later stays deleted. Months already past are left as they are."""
    if (
        month < this_month()
        or con.execute(
            "SELECT 1 FROM months WHERE user_id = ? AND month = ?", (uid, month)
        ).fetchone()
    ):
        return
    last = con.execute(
        "SELECT saving FROM months WHERE user_id = ? AND month < ? ORDER BY month DESC LIMIT 1",
        (uid, month),
    ).fetchone()
    con.execute(
        "INSERT INTO months (user_id, month, saving) VALUES (?, ?, ?)",
        (uid, month, last[0] if last else 0.0),
    )
    con.execute(
        "INSERT INTO entries (user_id, type_id, month, amount) SELECT user_id, id, ?, amount"
        " FROM types WHERE user_id = ? AND fixed AND NOT hidden ORDER BY id",
        (month, uid),
    )
    con.commit()


def month_values(month: str) -> dict:
    """Everything both screens of a month show."""
    if not MONTH.fullmatch(month):
        abort(404)
    con, uid = db(), current_user()["id"]
    open_month(con, uid, month)
    types = con.execute(
        "SELECT * FROM types WHERE user_id = ? AND NOT hidden ORDER BY id", (uid,)
    ).fetchall()
    entries = con.execute(
        "SELECT e.*, t.side, t.key, t.name, t.colour, t.fixed FROM entries e"
        " JOIN types t ON t.id = e.type_id WHERE e.user_id = ? AND e.month = ? ORDER BY e.id",
        (uid, month),
    ).fetchall()
    savings = dict(con.execute("SELECT month, saving FROM months WHERE user_id = ?", (uid,)))
    saving = savings.get(month, 0.0)
    saved = sum(v for m, v in savings.items() if m <= month)
    net = con.execute(
        "SELECT SUM(CASE WHEN t.side = 'in' THEN e.amount ELSE -e.amount END) FROM entries e"
        " JOIN types t ON t.id = e.type_id WHERE e.user_id = ? AND e.month <= ?",
        (uid, month),
    ).fetchone()[0]
    # The savings account gets what was saved; the first other account keeps the rest.
    accounts, rest_given = [], False
    for a in con.execute("SELECT * FROM accounts WHERE user_id = ? ORDER BY id", (uid,)):
        rest = not a["savings"] and not rest_given
        rest_given = rest_given or rest
        add = saved if a["savings"] else (net or 0) - saved if rest else 0
        accounts.append(dict(a, balance=a["start"] + add, rest=rest))
    total_in = sum(e["amount"] for e in entries if e["side"] == "in")
    total_out = sum(e["amount"] for e in entries if e["side"] == "out")
    fixed = sum(e["amount"] for e in entries if e["side"] == "out" and e["fixed"])
    year = int(month[:4])
    return {
        "month": month,
        "name": MONTHS[LANGS.index(lang())][int(month[5:]) - 1],
        "year": year,
        "prev": shift(month, -1),
        "next": shift(month, 1),
        "types": types,
        "entries": entries,
        "total_in": total_in,
        "total_out": total_out,
        "fixed": fixed,
        "other": total_out - fixed,
        "saving": saving,
        "left": total_in - total_out - saving,
        "accounts": accounts,
        "total_acc": sum(a["balance"] for a in accounts),
        "chart": chart({m: v for m, v in savings.items() if m <= month}, year),
        "new": request.args.get("new", type=int),
    }


def back(month: str, **kw):
    """Back to the screen the form was on."""
    screen = "cards" if request.form.get("screen") == "cards" else "month"
    return redirect(url_for(screen, month=month, **kw))


@app.route("/")
def home():
    return redirect(url_for("month", month=this_month()))


@app.route("/m/<month>")
def month(month):
    return render_template("plan.html", **month_values(month))


@app.route("/m/<month>/cards")
def cards(month):
    return render_template("month.html", **month_values(month))


@app.route("/m/<month>/saving", methods=["POST"])
def set_saving(month):
    if not MONTH.fullmatch(month):
        abort(404)
    amount = parse_amount(request.form.get("saving", ""))
    if amount is not None:
        con, uid = db(), current_user()["id"]
        open_month(con, uid, month)
        con.execute(
            "INSERT OR REPLACE INTO months (user_id, month, saving) VALUES (?, ?, ?)",
            (uid, month, amount),
        )
        con.commit()
    return redirect(url_for("month", month=month))


@app.route("/m/<month>/add", methods=["POST"])
def add_entry(month):
    if not MONTH.fullmatch(month):
        abort(404)
    ty = mine("types", request.form.get("type", type=int))
    con = db()
    eid = con.execute(
        "INSERT INTO entries (user_id, type_id, month, amount) VALUES (?, ?, ?, ?)",
        (ty["user_id"], ty["id"], month, ty["amount"]),
    ).lastrowid
    con.commit()
    return back(month, new=eid)


@app.route("/e/<int:eid>", methods=["POST"])
def edit_entry(eid):
    e, f, con = mine("entries", eid), request.form, db()
    if f.get("delete"):
        con.execute("DELETE FROM entries WHERE id = ?", (eid,))
    else:
        amount = parse_amount(f.get("amount", ""))
        con.execute(
            "UPDATE entries SET note = ?, amount = ? WHERE id = ?",
            (
                f.get("note", e["note"]).strip()[:80],
                e["amount"] if amount is None else amount,
                eid,
            ),
        )
    con.commit()
    return back(e["month"])


# Setup: card types, accounts, language, currency and password


@app.route("/setup")
def setup():
    con, uid = db(), current_user()["id"]
    types = con.execute(
        "SELECT * FROM types WHERE user_id = ? AND NOT hidden ORDER BY id", (uid,)
    ).fetchall()
    free = {}
    for side in ("in", "out"):
        used = {ty["colour"] for ty in types if ty["side"] == side}
        free[side] = next((c for c in COLOURS if c not in used), COLOURS[0])
    accounts = con.execute("SELECT * FROM accounts WHERE user_id = ? ORDER BY id", (uid,))
    return render_template("setup.html", types=types, free=free, accounts=accounts.fetchall())


@app.route("/types/new", methods=["POST"])
def new_type():
    f, con, uid = request.form, db(), current_user()["id"]
    name = f.get("name", "").strip()[:40]
    if f.get("side") in ("in", "out") and name:
        colour = f.get("colour") if f.get("colour") in COLOURS else COLOURS[0]
        amount = parse_amount(f.get("amount", "")) or 0.0
        tid = con.execute(
            "INSERT INTO types (user_id, side, name, colour, amount, fixed) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, f["side"], name, colour, amount, int(bool(f.get("fixed")))),
        ).lastrowid
        if f.get("fixed"):
            join_months(con, uid, tid, amount)
        con.commit()
    return redirect(url_for("setup") + "#types")


@app.route("/types/<int:tid>", methods=["POST"])
def edit_type(tid):
    ty, f, con = mine("types", tid), request.form, db()
    if f.get("hide"):
        con.execute("UPDATE types SET hidden = 1 WHERE id = ?", (tid,))
    else:
        name = f.get("name", "").strip()[:40]
        if name and name != label(ty):
            con.execute("UPDATE types SET name = ? WHERE id = ?", (name, tid))
        if f.get("colour") in COLOURS:
            con.execute("UPDATE types SET colour = ? WHERE id = ?", (f["colour"], tid))
        amount = parse_amount(f.get("amount", ""))
        amount = ty["amount"] if amount is None else amount
        fixed = int(bool(f.get("fixed")))
        con.execute("UPDATE types SET amount = ?, fixed = ? WHERE id = ?", (amount, fixed, tid))
        if fixed and not ty["fixed"]:
            join_months(con, ty["user_id"], tid, amount)
        elif fixed and amount != ty["amount"]:
            # A new rent from this month on: cards that still have the old amount follow.
            con.execute(
                "UPDATE entries SET amount = ? WHERE type_id = ? AND month >= ? AND amount = ?",
                (amount, tid, this_month(), ty["amount"]),
            )
    con.commit()
    return redirect(url_for("setup") + "#types")


@app.route("/accounts/new", methods=["POST"])
def new_account():
    f, uid = request.form, current_user()["id"]
    name = f.get("name", "").strip()[:40]
    if name:
        con = db()
        first = not con.execute("SELECT 1 FROM accounts WHERE user_id = ?", (uid,)).fetchone()
        con.execute(
            "INSERT INTO accounts (user_id, name, start, savings) VALUES (?, ?, ?, ?)",
            (uid, name, parse_amount(f.get("start", "")) or 0.0, int(first)),
        )
        con.commit()
    return redirect(url_for("setup") + "#accounts")


@app.route("/accounts/<int:aid>", methods=["POST"])
def edit_account(aid):
    a, f, con = mine("accounts", aid), request.form, db()
    if f.get("delete"):
        con.execute("DELETE FROM accounts WHERE id = ?", (aid,))
    elif f.get("savings"):
        con.execute("UPDATE accounts SET savings = (id = ?) WHERE user_id = ?", (aid, a["user_id"]))
    else:
        name = f.get("name", "").strip()[:40]
        if name and name != label(a):
            con.execute("UPDATE accounts SET name = ? WHERE id = ?", (name, aid))
        start = parse_amount(f.get("start", ""))
        if start is not None:
            con.execute("UPDATE accounts SET start = ? WHERE id = ?", (start, aid))
    con.commit()
    return redirect(url_for("setup") + "#accounts")


@app.route("/prefs", methods=["POST"])
def prefs():
    """Language, currency, light or dark, and the password. Before login, only the session."""
    me, f, con = current_user(), request.form, db()
    for key, allowed in (
        ("lang", LANGS),
        ("currency", CURRENCIES),
        ("theme", ("", "light", "dark")),
    ):
        if f.get(key) in allowed:
            if me:
                con.execute(f"UPDATE users SET {key} = ? WHERE id = ?", (f[key], me["id"]))
            else:
                session[key] = f[key]
    if me and "password" in f:
        new = f.get("new_password", "")
        if not check_password_hash(me["pw_hash"], f["password"]):
            flash(t("pw_wrong"))
        elif len(new) < 12:
            flash(t("short_pw"))
        elif new != f.get("new_password2"):
            flash(t("pw_differ"))
        else:
            con.execute(
                "UPDATE users SET pw_hash = ? WHERE id = ?", (generate_password_hash(new), me["id"])
            )
            flash(t("pw_changed"))
    con.commit()
    nxt = f.get("next", "")
    return redirect(nxt if re.fullmatch(r"/(?![/\\])[^\\]*", nxt) else url_for("setup"))


# Admin: only the admin makes users, and each gets an invite link


@app.route("/admin")
def admin():
    admin_only()
    users = db().execute("SELECT * FROM users ORDER BY name").fetchall()
    return render_template("admin.html", users=users)


@app.route("/admin/new", methods=["POST"])
def admin_new():
    admin_only()
    con, f = db(), request.form
    email = f.get("email", "").strip().lower()
    if "@" in email:
        if con.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            flash(t("user_exists"))
        else:
            uid = add_user(con, email, f.get("name", ""))
            link = make_invite(con, uid)
            flash(t("invite_link", name=f.get("name", "").strip() or email, link=link))
    return redirect(url_for("admin"))


@app.route("/admin/<int:uid>/invite", methods=["POST"])
def admin_invite(uid):
    admin_only()
    user = db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user is None:
        abort(404)
    flash(t("invite_link", name=user["name"], link=make_invite(db(), uid)))
    return redirect(url_for("admin"))


def main(args: list) -> None:
    init_db()
    if args[:1] == ["adduser"] and len(args) >= 3:
        with closing(connect()) as con:
            name = " ".join(a for a in args[2:] if a != "--admin")
            print(make_invite(con, add_user(con, args[1], name, "--admin" in args)))
    elif args[:1] == ["invite"] and len(args) == 2:
        with closing(connect()) as con:
            row = con.execute("SELECT id FROM users WHERE email = ?", (args[1].lower(),)).fetchone()
            print(make_invite(con, row["id"]) if row else "No such user.")
    elif not args:
        from waitress import serve

        serve(app, host="127.0.0.1", port=int(os.environ.get("SPARE_PORT", "8094")), threads=8)
    else:
        print("usage: app.py [adduser EMAIL NAME [--admin] | invite EMAIL]")


if __name__ == "__main__":
    main(sys.argv[1:])
