# spare

A simple monthly budget on coloured cards. The main screen answers one question:
with this month's income, fixed costs and saving, how much can I still spend?

"Spare" is Norwegian for "save", and English for money left over.

## What it does

- **Where the budget is kept**: the first visit asks. "On this device" keeps
  it in the browser, works with no internet and never sends it anywhere.
  "Log in to an account" keeps it on the server, the same on every device.
  The two are never mixed.
- **Backup**: on a device, Oppsett has "Save to file" and "Open file". The
  file is yours; opening one replaces the budget on that device.
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
- **Tour**: the first visit to Plan shows what each part does, one step at a
  time. "Show me around" at the foot of Plan starts it again.
- **Light and dark**: follows the phone or computer, with a switch at the top.
- **Login**: for a budget on the server, a password plus a six-digit code
  from an authenticator app. Only the admin makes users, and each user sees
  only their own budget.

## Install it as an app

spare installs from the browser, with its own icon and window. The first
install needs the https address of a server; after that, a budget kept on the
device opens with no internet. The front page has a download button for
Windows and one for Mac, which install it or show how.

- **Chrome or Edge, on Windows or Mac**: click the install button at the right
  end of the address bar.
- **Safari on a Mac**: File, then Add to Dock.
- **iPhone**: Safari's share button, then Add to Home Screen.

The app fetches a new version of spare by itself the next time it is online.

## Run it on your own machine

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
SPARE_DEV=1 .venv/bin/python app.py adduser you@example.com "Your Name" --admin
SPARE_DEV=1 .venv/bin/python app.py
```

Open the invite link the first command prints, set a password and scan the
code with your phone. The app runs at http://127.0.0.1:8094. For a budget on
the device only, skip the first command and pick "On this device".

Tests: `.venv/bin/pytest` for the server, `node --test tests/*.test.js` for
the budget rules.

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

All data on the server is in one file, `data/spare.db`. Back it up.

## Choices

- No timers in the page, and no work on the server while nobody uses it.
- The budget rules run in the browser, in one script (`static/spare.js`), so
  a budget on the device needs no server. The Python file only logs people
  in and keeps each user's budget as one document.
- A save to the server names the version it was made from. If another device
  saved first, the save is refused and the page shows the newer budget.
- No outside services.
- Public domain (Unlicense).
