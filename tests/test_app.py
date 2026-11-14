import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ.update(SPARE_DATA=str(TMP / "data"), SPARE_DEV="1", SPARE_SECRET="test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as spare

spare.init_db()
NB = " "  # the space that keeps 12 500 kr on one line


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
    client.get("/", follow_redirects=True)  # as the browser does; it sets a new form token
    return client, uid


def type_id(uid, key):
    with spare.connect() as con:
        sql = "SELECT id FROM types WHERE user_id = ? AND key = ?"
        return con.execute(sql, (uid, key)).fetchone()[0]


def add(client, month, tid, amount, note=""):
    r = post(client, f"/m/{month}/add", type=str(tid))
    eid = int(re.search(r"new=(\d+)", r.headers["Location"]).group(1))
    post(client, f"/e/{eid}", amount=amount, note=note)
    return eid


def test_pages_need_login():
    r = spare.app.test_client().get("/m/2026-11")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_post_without_form_token_is_refused():
    client, _ = user("una@example.com", "Una")
    assert client.post("/prefs", data={"lang": "en"}).status_code == 400


def test_amounts_are_read_both_ways_of_writing():
    assert spare.parse_amount("32 500,50") == 32500.5
    assert spare.parse_amount("12,500") == 12500
    assert spare.parse_amount("1.234,5") == 1234.5
    assert spare.parse_amount("-200 kr") == -200
    assert spare.parse_amount("") == 0
    assert spare.parse_amount("abc") is None


def test_a_month_adds_up_and_the_rest_goes_to_savings():
    client, uid = user("vera@example.com", "Vera")
    add(client, "2026-10", type_id(uid, "salary"), "30000")
    add(client, "2026-11", type_id(uid, "salary"), "32 500,50", "Main job")
    add(client, "2026-11", type_id(uid, "food"), "1200")
    with spare.connect() as con:
        con.execute("UPDATE accounts SET start = 1000 WHERE user_id = ? AND savings", (uid,))
    page = client.get("/m/2026-11").data.decode()
    assert f"Spart i november: 31{NB}300,50{NB}kr" in page
    assert f"62{NB}300,50{NB}kr" in page  # 1000 at the start, plus October and November
    assert "Main job" in page and 'class="bar up"' in page
    assert f"Spart i 2026: 61{NB}300,50{NB}kr" in page


def test_each_user_sees_only_their_own_cards():
    a, a_uid = user("wim@example.com", "Wim")
    b, _ = user("xia@example.com", "Xia")
    eid = add(a, "2026-11", type_id(a_uid, "salary"), "100", "Secret pay")
    assert post(b, f"/e/{eid}", amount="1").status_code == 404
    assert post(b, "/m/2026-11/add", type=str(type_id(a_uid, "food"))).status_code == 404
    assert "Secret pay" not in b.get("/m/2026-11").data.decode()


def test_english_and_euro():
    client, uid = user("yrsa@example.com", "Yrsa")
    add(client, "2026-11", type_id(uid, "salary"), "1234.5")
    post(client, "/prefs", lang="en", currency="EUR")
    page = client.get("/m/2026-11").data.decode()
    assert "Income" in page and "+ Salary" in page and "€1,234.50" in page


def test_a_removed_card_type_keeps_its_old_cards():
    client, uid = user("zack@example.com", "Zack")
    tid = type_id(uid, "clothes")
    add(client, "2026-11", tid, "500")
    post(client, f"/types/{tid}", hide="1")
    page = client.get("/m/2026-11").data.decode()
    assert f'name="type" value="{tid}"' not in page and 'value="500"' in page


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
