"""spare: a monthly budget on coloured cards. Income, expenses, and the accounts the rest goes to.

The budget itself runs in the browser (static/spare.js). This server only logs people in and
keeps each user's budget as one document; a budget kept on a device never comes here."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
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

__version__ = "0.5.1"

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
-- Each user's budget as one JSON document, the same shape the page keeps on a device. The
-- version goes up by one on each save, so a save from a page that missed the last one is refused.
CREATE TABLE IF NOT EXISTS budgets (
  user_id INTEGER PRIMARY KEY REFERENCES users(id), doc TEXT NOT NULL, version INTEGER NOT NULL);
-- Budgets from before 0.5 are in the four tables below. They move into budgets the first time
-- their user opens spare, and are not written to after that.
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
    "start_title": ("Hvor skal budsjettet ligge?", "Where should your budget be kept?"),
    "use_device": ("På denne enheten", "On this device"),
    "use_device_help": (
        "Virker uten nett. Budsjettet lagres bare her, og ingen andre ser det.",
        "Works with no internet. The budget is kept only here, and nobody else sees it.",
    ),
    "use_server": ("Logg inn med konto", "Log in to an account"),
    "use_server_help": (
        "For deg som har fått en invitasjon. Budsjettet ligger på serveren, likt på alle enhetene dine.",
        "For those who got an invite. The budget is kept on the server, the same on all your devices.",
    ),
    "no_account": ("Bruk spare uten konto", "Use spare without an account"),
    "no_account_help": (
        "Uten passord og kode-app. Budsjettet ligger bare på denne enheten, og du kan laste ned appen.",
        "No password and no code app. The budget is kept only on this device, and you can download the app.",
    ),
    "or_account": ("Eller lag en konto", "Or make an account"),
    "backup": ("Sikkerhetskopi", "Backup"),
    "backup_help": (
        (
            "Budsjettet ligger bare i denne nettleseren. Sletter du nettleserdataene, forsvinner det."
            " Lagre en kopi til fil av og til."
        ),
        (
            "The budget is kept only in this browser. Clearing the browser's data deletes it."
            " Save a copy to a file now and then."
        ),
    ),
    "backup_save": ("Lagre til fil", "Save to file"),
    "backup_open": ("Åpne fil", "Open file"),
    "backup_replace": (
        "Fila erstatter budsjettet på denne enheten. Fortsette?",
        "The file replaces the budget on this device. Go on?",
    ),
    "backup_bad": ("Fila er ikke en sikkerhetskopi fra spare.", "That file is not a spare backup."),
    "to_login": (
        "Logg inn på en konto i stedet. Budsjettet her blir liggende.",
        "Log in to an account instead. The budget here stays where it is.",
    ),
    "device_full": ("Nettleseren lot ikke spare lagre.", "The browser did not let spare save."),
    "not_saved": (
        "Ikke lagret ennå: ingen forbindelse. Det lagres ved neste endring eller når nettet er tilbake.",
        "Not saved yet: no connection. It saves with the next change or when the network is back.",
    ),
    "changed_elsewhere": (
        (
            "Budsjettet ble endret på en annen enhet, og siden viser det nå."
            " Den siste endringen her ble ikke lagret."
        ),
        (
            "The budget was changed on another device, and the page shows that now."
            " The last change here was not saved."
        ),
    ),
    "no_server": ("Får ikke kontakt med serveren.", "Cannot reach the server."),
    "tour": ("Vis meg rundt", "Show me around"),
    "tour_next": ("Neste", "Next"),
    "tour_done": ("Ferdig", "Done"),
    "tour_skip": ("Hopp over", "Skip"),
    "tour_1": (
        "Velkommen! Her ser du hva du kan bruke denne måneden, når alt annet er trukket fra.",
        "Welcome! This shows what you can spend this month, once everything else is taken off.",
    ),
    "tour_2": (
        "Trykk på en farget knapp for å legge inn en inntekt, og skriv beløpet. Alt lagres med en gang.",
        "Tap a coloured button to add income, then type the amount. Everything saves at once.",
    ),
    "tour_3": (
        "Faste utgifter, som husleie, kommer inn i hver ny måned av seg selv. Du velger dem i Oppsett.",
        "Fixed costs, such as rent, come into each new month by themselves. You pick them in Setup.",
    ),
    "tour_4": (
        "Skriv hva du vil spare denne måneden. Det går til sparekontoen.",
        "Type what you want to save this month. It goes to your savings account.",
    ),
    "tour_5": (
        "Annet du bruker, som mat og klær, legger du inn i Oversikt. Der ser du også hva du har spart i år.",
        "Put other spending, such as food and clothes, in Overview. It also shows what you saved this year.",
    ),
    "tour_6": (
        "I Oppsett velger du korttyper, kontoer, språk og valuta. Omvisningen finner du nederst på Plan.",
        "In Setup you pick card types, accounts, language and currency. The tour is at the foot of Plan.",
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


# Words in the user's language


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
    """Make a user. The page makes the starting budget on the first visit. The caller commits."""
    email = email.strip().lower()
    uid = con.execute(
        "INSERT INTO users (email, name, is_admin, created_at) VALUES (?, ?, ?, ?)",
        (email, name.strip() or email.split("@")[0], int(admin), now()),
    ).lastrowid
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


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


PUBLIC = ("login", "invite", "prefs", "demo", "static", "home", "service_worker")


@app.before_request
def guard():
    """Every post carries the form token, as a field or a header. Only a few pages work
    without a login; the budget API answers 401 so the page can send the person to log in."""
    if request.method in ("POST", "PUT"):
        token = session.get("csrf", "")
        sent = request.headers.get("X-CSRF") or request.form.get("csrf", "")
        if not token or not hmac.compare_digest(token, sent):
            abort(400)
    if request.endpoint in PUBLIC or current_user():
        return None
    if request.path.startswith("/api/"):
        return {"error": "login"}, 401
    return redirect(url_for("login"))


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
    me = current_user()
    return {
        "csrf": csrf_token(),
        "me": me,
        "now": now(),
        "t": t,
        "lang": lang(),
        "theme": me["theme"] if me else session.get("theme", ""),
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


def this_month() -> str:
    return time.strftime("%Y-%m")


# The app: one page for everyone, and each user's budget as one document


@app.route("/")
def home():
    page = render_template("app.html", words=dict(WORDS, _months=MONTHS))
    return page, 200, {"Cache-Control": "no-cache"}


@app.route("/sw.js")
def service_worker():
    """Keeps a copy of the page on the device. It lives at the top, so it covers the whole site."""
    js = (ROOT / "static" / "sw.js").read_text().replace("__VERSION__", __version__)
    return js, 200, {"Content-Type": "text/javascript", "Cache-Control": "no-cache"}


def move_old_budget(con: sqlite3.Connection, me: sqlite3.Row) -> sqlite3.Row | None:
    """A budget from before 0.5, in the tables types, entries, accounts and months, becomes one
    document the first time its user opens spare. None for a user who has no budget yet."""
    uid = me["id"]
    rows = {
        table: con.execute(
            f"SELECT * FROM {table} WHERE user_id = ? ORDER BY id", (uid,)
        ).fetchall()
        for table in ("types", "entries", "accounts")
    }
    if not rows["types"]:
        return None
    months = con.execute("SELECT month, saving FROM months WHERE user_id = ?", (uid,))
    budget = {
        "v": 1,
        "currency": me["currency"],
        "next": max(r["id"] for table in rows.values() for r in table) + 1,
        "types": [
            {
                "id": r["id"],
                "side": r["side"],
                "key": r["key"],
                "name": r["name"],
                "colour": r["colour"],
                "hidden": bool(r["hidden"]),
                "amount": r["amount"],
                "fixed": bool(r["fixed"]),
            }
            for r in rows["types"]
        ],
        "entries": [
            {
                "id": r["id"],
                "type": r["type_id"],
                "month": r["month"],
                "amount": r["amount"],
                "note": r["note"],
            }
            for r in rows["entries"]
        ],
        "accounts": [
            {
                "id": r["id"],
                "key": r["key"],
                "name": r["name"],
                "start": r["start"],
                "savings": bool(r["savings"]),
            }
            for r in rows["accounts"]
        ],
        "months": dict(months.fetchall()),
    }
    con.execute(
        "INSERT OR IGNORE INTO budgets (user_id, doc, version) VALUES (?, ?, 1)",
        (uid, json.dumps(budget, separators=(",", ":"))),
    )
    con.commit()
    return con.execute("SELECT doc, version FROM budgets WHERE user_id = ?", (uid,)).fetchone()


def is_budget(budget) -> bool:
    return (
        isinstance(budget, dict)
        and all(isinstance(budget.get(k), list) for k in ("types", "entries", "accounts"))
        and isinstance(budget.get("months"), dict)
    )


@app.route("/api/budget", methods=["GET", "PUT"])
def api_budget():
    """The user's budget, and a save of it. A save names the version it was made from; if
    another save came first, it is refused and the answer carries the budget as it now is."""
    me, con = current_user(), db()
    row = con.execute("SELECT doc, version FROM budgets WHERE user_id = ?", (me["id"],)).fetchone()
    row = row or move_old_budget(con, me)
    version = row["version"] if row else 0
    if request.method == "GET":
        return {
            "budget": json.loads(row["doc"]) if row else None,
            "version": version,
            "csrf": csrf_token(),
            "me": {
                "name": me["name"],
                "email": me["email"],
                "admin": bool(me["is_admin"]),
                "lang": me["lang"],
                "theme": me["theme"],
                "currency": me["currency"],
            },
        }
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not is_budget(data.get("budget")):
        abort(400)
    doc = json.dumps(data["budget"], separators=(",", ":"))
    if data.get("version") == version:
        if row:
            sql = "UPDATE budgets SET doc = ?, version = version + 1 WHERE user_id = ? AND version = ?"
            done = con.execute(sql, (doc, me["id"], version))
        else:
            sql = "INSERT OR IGNORE INTO budgets (user_id, doc, version) VALUES (?, ?, 1)"
            done = con.execute(sql, (me["id"], doc))
        if done.rowcount == 1:
            con.commit()
            return {"version": version + 1}
    row = con.execute("SELECT doc, version FROM budgets WHERE user_id = ?", (me["id"],)).fetchone()
    now_at = {
        "budget": json.loads(row["doc"]) if row else None,
        "version": row["version"] if row else 0,
    }
    return now_at, 409


@app.route("/api/password", methods=["POST"])
def api_password():
    me, f = current_user(), request.form
    new = f.get("new_password", "")
    if not check_password_hash(me["pw_hash"], f.get("password", "")):
        return {"ok": False, "message": t("pw_wrong")}
    if len(new) < 12:
        return {"ok": False, "message": t("short_pw")}
    if new != f.get("new_password2"):
        return {"ok": False, "message": t("pw_differ")}
    con = db()
    con.execute(
        "UPDATE users SET pw_hash = ? WHERE id = ?", (generate_password_hash(new), me["id"])
    )
    con.commit()
    return {"ok": True, "message": t("pw_changed")}


@app.route("/prefs", methods=["POST"])
def prefs():
    """Language and light or dark for the login and admin pages. Before login, only the session."""
    me, f, con = current_user(), request.form, db()
    for key, allowed in (("lang", LANGS), ("theme", ("", "light", "dark"))):
        if f.get(key) in allowed:
            if me:
                con.execute(f"UPDATE users SET {key} = ? WHERE id = ?", (f[key], me["id"]))
            else:
                session[key] = f[key]
    con.commit()
    nxt = f.get("next", "")
    return redirect(nxt if re.fullmatch(r"/(?![/\\])[^\\]*", nxt) else url_for("home"))


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
