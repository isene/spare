import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ.update(SPARE_DATA=str(TMP / "data"), SPARE_DEV="1", SPARE_SECRET="test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as spare

spare.init_db()
BUDGET = {
    "v": 1,
    "currency": "NOK",
    "next": 3,
    "types": [],
    "entries": [],
    "accounts": [],
    "months": {},
}


def post(client, path, **form):
    with client.session_transaction() as s:
        form.setdefault("csrf", s.get("csrf", ""))
    return client.post(path, data=form)


def user(email, name, admin=False):
    """Make a user and finish the invite, so the user is logged in."""
    with spare.connect() as con:
        uid = spare.add_user(con, email, name, admin)
        link = spare.make_invite(con, uid)
        secret = con.execute("SELECT totp_secret FROM users WHERE id = ?", (uid,)).fetchone()[0]
    client = spare.app.test_client()
    path = link.replace(spare.BASE_URL, "")
    client.get(path)
    pw = "correct horse battery"
    r = post(client, path, password=pw, password2=pw, code=spare.totp(secret))
    assert r.status_code == 302
    client.get(r.headers["Location"])  # as a browser does; the page sets the form token
    return client, uid


def load(client):
    r = client.get("/api/budget")
    assert r.status_code == 200
    return r.get_json()


def put(client, budget, version, csrf=None):
    csrf = load(client)["csrf"] if csrf is None else csrf
    body = json.dumps({"budget": budget, "version": version})
    return client.put(
        "/api/budget", data=body, content_type="application/json", headers={"X-CSRF": csrf}
    )


def test_the_app_page_is_open_and_the_budget_needs_login():
    anon = spare.app.test_client()
    page = anon.get("/")
    assert page.status_code == 200 and 'id="words"' in page.data.decode()  # no user in it
    assert anon.get("/api/budget").status_code == 401
    assert anon.get("/admin").status_code == 302
    sw = anon.get("/sw.js")
    assert sw.status_code == 200 and spare.__version__ in sw.data.decode()


def test_a_save_without_the_form_token_is_refused():
    client, _ = user("una@example.com", "Una")
    assert client.post("/prefs", data={"lang": "en"}).status_code == 400
    assert put(client, BUDGET, 0, csrf="wrong").status_code == 400


def test_a_new_user_saves_a_budget_and_gets_it_back():
    client, _ = user("vera@example.com", "Vera")
    first = load(client)
    assert first["budget"] is None and first["version"] == 0 and first["me"]["name"] == "Vera"
    assert put(client, BUDGET, 0).get_json() == {"version": 1}
    again = load(client)
    assert again["budget"] == BUDGET and again["version"] == 1


def test_a_save_from_a_page_that_missed_the_last_save_is_refused():
    client, _ = user("vic@example.com", "Vic")
    put(client, BUDGET, 0)
    newer = dict(BUDGET, currency="EUR")
    assert put(client, newer, 1).status_code == 200
    r = put(client, dict(BUDGET, currency="USD"), 1)  # made from version 1, but 2 is there
    assert r.status_code == 409 and r.get_json() == {"budget": newer, "version": 2}


def test_something_that_is_not_a_budget_is_refused():
    client, _ = user("walt@example.com", "Walt")
    assert put(client, {"types": "x"}, 0).status_code == 400
    assert load(client)["budget"] is None


def test_each_user_sees_only_their_own_budget():
    a, _ = user("wim@example.com", "Wim")
    b, _ = user("xia@example.com", "Xia")
    put(
        a,
        dict(
            BUDGET,
            entries=[{"id": 1, "type": 1, "month": "2026-11", "amount": 9, "note": "Secret"}],
        ),
        0,
    )
    assert load(b)["budget"] is None


def test_a_budget_from_before_moves_into_one_document():
    client, uid = user("yrsa@example.com", "Yrsa")
    with spare.connect() as con:
        con.execute("UPDATE users SET currency = 'EUR' WHERE id = ?", (uid,))
        tid = con.execute(
            "INSERT INTO types (user_id, side, key, colour, amount, fixed) VALUES (?, 'out', 'housing', 'blue', 10000, 1)",
            (uid,),
        ).lastrowid
        con.execute(
            "INSERT INTO entries (user_id, type_id, month, amount, note) VALUES (?, ?, '2026-11', 10000, 'Rent')",
            (uid, tid),
        )
        con.execute(
            "INSERT INTO accounts (user_id, key, savings) VALUES (?, 'savings_account', 1)", (uid,)
        )
        con.execute(
            "INSERT INTO months (user_id, month, saving) VALUES (?, '2026-11', 3000)", (uid,)
        )
    got = load(client)
    budget = got["budget"]
    assert (
        got["version"] == 1
        and budget["currency"] == "EUR"
        and budget["months"] == {"2026-11": 3000}
    )
    assert budget["types"][0]["fixed"] is True and budget["entries"][0]["note"] == "Rent"
    assert budget["entries"][0]["type"] == tid and budget["accounts"][0]["savings"] is True
    assert budget["next"] > max(
        r["id"] for k in ("types", "entries", "accounts") for r in budget[k]
    )


def test_the_password_changes_only_with_the_present_one():
    client, _ = user("zack@example.com", "Zack")
    csrf = load(client)["csrf"]
    new = {"new_password": "a much longer password", "new_password2": "a much longer password"}
    r = client.post("/api/password", data=dict(new, password="wrong"), headers={"X-CSRF": csrf})
    assert r.get_json()["ok"] is False
    r = client.post(
        "/api/password", data=dict(new, password="correct horse battery"), headers={"X-CSRF": csrf}
    )
    assert r.get_json() == {"ok": True, "message": "Passordet er byttet."}


def test_only_the_admin_adds_users():
    admin, _ = user("ada@example.com", "Ada", admin=True)
    other, _ = user("bob@example.com", "Bob")
    assert other.get("/admin").status_code == 403
    post(admin, "/admin/new", email="cleo@example.com", name="Cleo")
    page = admin.get("/admin").data.decode()
    assert "cleo@example.com" in page and "/invite/" in page


def test_the_qr_code_scales_and_the_demo_login_is_off():
    with spare.connect() as con:
        link = spare.make_invite(con, spare.add_user(con, "dan@example.com", "Dan"))
    page = spare.app.test_client().get(link.replace(spare.BASE_URL, "")).data.decode()
    svg = page[page.index("<svg") : page.index(">", page.index("<svg"))]
    assert "viewBox" in svg and "width=" not in svg  # a fixed size gets cut off when shrunk
    assert spare.app.test_client().get("/demo").status_code == 404


def test_the_app_can_be_installed_and_used_without_an_account():
    anon = spare.app.test_client()
    assert "Bruk spare uten konto" in anon.get("/login").data.decode()
    app = anon.get("/static/manifest.json").get_json()
    assert app["display"] == "standalone" and app["start_url"] == "/"
    for icon in app["icons"]:
        assert anon.get(icon["src"]).status_code == 200
    words = anon.get("/").data.decode()
    for key in ("use_device", "backup_save", "tour_1"):
        assert f'"{key}"' in words  # the page carries every word it shows, for use offline
