# spare

A simple monthly budget on coloured cards. The main screen answers one question:
with this month's income, fixed costs and saving, how much can I still spend?

"Spare" is Norwegian for "save", and English for money left over.

## What it does

- **Plan**: type this month's income, and set what to save. The page shows
  the fixed costs, other spending, and in large type what you can still spend.
- **Overview**: income, expenses and accounts as three columns of cards. Tap
  a card type, or drag it with a mouse, into the month and type the amount.
  It saves as soon as you leave the box.
- **Card types**: rename them, pick a colour, give a usual amount, add your
  own, or remove one. A new card starts at the usual amount. Cards already in
  your months stay when a type is removed.
- **Every month**: a card type marked this way, such as rent or a bus pass,
  comes into each new month by itself. Delete it from one month and it stays
  gone there.
- **Accounts**: one card per bank account, with a starting balance. What you
  save goes to the savings account; the rest stays in the spending account.
- **Chart**: a bar for what each month saved, and a line for the sum so far
  this year.
- **Language and money**: Norwegian or English, and NOK, EUR or USD.
- **Phone**: on a narrow screen the three columns sit side by side; swipe
  between them, or tap the coloured tabs.
- **Light and dark**: follows the phone or computer, with a switch at the top.
- **Login**: password plus a six-digit code from an authenticator app. Only
  the admin makes users, and each user sees only their own budget.

## Run it on your own machine

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
SPARE_DEV=1 .venv/bin/python app.py adduser you@example.com "Your Name" --admin
SPARE_DEV=1 .venv/bin/python app.py
```

Open the invite link the first command prints, set a password and scan the
code with your phone. The app runs at http://127.0.0.1:8094.

## Settings

Put these in the environment, or in a `.env` file next to `app.py`:

| Name | What it is |
|------|------------|
| `SPARE_SECRET` | A long random string that signs the login cookie. Required on a server. |
| `SPARE_URL` | The address people use, such as `https://spare.example.com`. Invite links use it. |
| `SPARE_PORT` | The local port, 8094 if not set. |
| `SPARE_DATA` | The folder for the database, `data/` if not set. |
| `SPARE_DEV` | `1` lets the login cookie work over plain http, for testing only. |
| `SPARE_DEMO` | `1`, together with `SPARE_DEV=1`, makes `/demo` log in as the first user with no password. For testing on your own machine only. |

## Server

First setup, as the app's own user, on a server with Apache and certbot:

```sh
git clone https://github.com/isene/spare ~/spare && cd ~/spare
# write .env, then once as root:
sudo sh deploy/root-setup.sh spare.example.com you@example.com
.venv/bin/python app.py adduser you@example.com "Your Name" --admin
```

The root script installs the Python packages, the system service, Apache and
the certificate. It also lets the app's user restart the service.

Deploy a change:

```sh
cd ~/spare && git pull && .venv/bin/pip install -q -r requirements.txt && sudo systemctl restart spare
```

All data is in one file, `data/spare.db`. Back it up.

## Choices

- No timers in the page, and no work on the server while nobody uses it.
- One Python file, plain HTML forms, and a small script for saving in the
  background and for dragging. No outside services.
- Public domain (Unlicense).
