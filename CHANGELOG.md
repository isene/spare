# Changelog

## 0.3.1 (2026-11-14)

- Oppsett saves a name, an amount, "every month" or a colour without reloading the page, the
  way the cards in the month already did. Language and currency still reload, since every
  word and number changes.

## 0.3.0 (2026-11-14)

- Plan and Oversikt sit in the top bar on every page, next to Oppsett, and keep the month shown.
- spare can be installed as an app from Chrome, Edge or Safari, on a laptop or a phone.
- The front page has download buttons for Windows and Mac. They install the app, or show how.

## 0.2.0 (2026-11-14)

- A new main screen, Plan: income, fixed costs, what you save and other spending, and at the
  top what you can still spend this month. The three columns are now the second screen, Oversikt.
- Card types have a usual amount; a new card starts at it.
- Card types marked "every month" come into each new month by themselves. A card deleted from a
  month stays deleted, and a new usual amount follows into this month and later.
- Each month has its own saving, filled in from the month before. It goes to the savings
  account, and the rest stays in the spending account.

## 0.1.0 (2026-11-14)

- First version: income, expenses and accounts as coloured cards, month by month.
- Card types with nine colours; tap or drag one into the month and type the amount.
- The month's savings go to the account you pick; a chart shows the year.
- Norwegian and English, NOK, EUR and USD, light and dark.
- Login with password and phone code; only the admin makes users.
- `/demo` logs in with no password when both `SPARE_DEV=1` and `SPARE_DEMO=1` are set, for local testing.
