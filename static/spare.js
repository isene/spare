// spare: the whole budget runs here, in the browser. A budget is one plain object, kept either
// on this device (in the browser's own storage) or on the server (one document per user), never
// both. The rules for months, cards and accounts are the same for both. Nothing runs on a timer.
"use strict";

// The rules: plain functions on a budget. The tests run them in Node.

const COLOURS = ["green", "teal", "blue", "orange", "red", "purple", "yellow", "pink", "brown"];
const CURRENCIES = ["NOK", "EUR", "USD"];
const START_TYPES = [
  ["in", "salary", "green"],
  ["in", "other_income", "teal"],
  ["out", "housing", "blue"],
  ["out", "food", "orange"],
  ["out", "restaurant", "red"],
  ["out", "clothes", "purple"],
  ["out", "transport", "yellow"],
];
const START_ACCOUNTS = [
  ["spending_account", false],
  ["savings_account", true],
];
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

// A type made at the start has a key and no name until it is renamed, so its name follows the
// language. A new card starts at the type's usual amount.
function newBudget() {
  const b = { v: 1, currency: "NOK", next: 1, types: [], entries: [], accounts: [], months: {} };
  for (const [side, key, colour] of START_TYPES) {
    b.types.push({ id: b.next++, side, key, name: "", colour, hidden: false, amount: 0, fixed: false });
  }
  for (const [key, savings] of START_ACCOUNTS) {
    b.accounts.push({ id: b.next++, key, name: "", start: 0, savings });
  }
  return b;
}

// A budget read from storage, the server or a file, with every value made the right kind, or
// null if it is not a budget. The page only ever shows a budget that has been through this.
function clean(b) {
  if (!b || typeof b !== "object" || !b.months || typeof b.months !== "object") return null;
  if (![b.types, b.entries, b.accounts].every(Array.isArray)) return null;
  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
  const str = (v, n) => String(v == null ? "" : v).slice(0, n);
  const id = (v) => Math.max(0, Math.floor(num(v)));
  const out = {
    v: 1,
    currency: CURRENCIES.includes(b.currency) ? b.currency : "NOK",
    types: b.types.map((t) => ({
      id: id(t.id),
      side: t.side === "in" ? "in" : "out",
      key: str(t.key, 40),
      name: str(t.name, 40),
      colour: COLOURS.includes(t.colour) ? t.colour : COLOURS[0],
      hidden: !!t.hidden,
      amount: num(t.amount),
      fixed: !!t.fixed,
    })),
    entries: b.entries
      .filter((e) => MONTH.test(e.month))
      .map((e) => ({ id: id(e.id), type: id(e.type), month: e.month, amount: num(e.amount), note: str(e.note, 80) })),
    accounts: b.accounts.map((a) => ({
      id: id(a.id),
      key: str(a.key, 40),
      name: str(a.name, 40),
      start: num(a.start),
      savings: !!a.savings,
    })),
    months: {},
  };
  for (const [m, v] of Object.entries(b.months)) if (MONTH.test(m)) out.months[m] = num(v);
  let top = id(b.next) - 1;
  for (const row of [...out.types, ...out.entries, ...out.accounts]) top = Math.max(top, row.id);
  out.next = top + 1;
  return out;
}

function thisMonth(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function shift(month, n) {
  const m = Number(month.slice(5)) - 1 + n;
  const y = Number(month.slice(0, 4)) + Math.floor(m / 12);
  return `${String(y).padStart(4, "0")}-${String((((m % 12) + 12) % 12) + 1).padStart(2, "0")}`;
}

// Read 32 500,50 or 32,500.50 or 1.234,5. A comma or point before one or two last digits is
// the decimal mark; any other is a thousands mark. Empty is 0, no digits is null.
function parseAmount(text) {
  text = String(text == null ? "" : text);
  if (!text.trim()) return 0;
  const s = text.replace(/[^\d,.\-−]/g, "").replace(/−/g, "-");
  const m = s.match(/^(-?)([\d.,]*?)(?:[.,](\d{1,2}))?$/);
  if (!m || !/\d/.test(s)) return null;
  const whole = m[2].replace(/[.,]/g, "") || "0";
  return Number(`${m[1]}${whole}.${m[3] || 0}`);
}

const typeOf = (b, id) => b.types.find((t) => t.id === id);

// The first time a month from this one on is opened, it gets the fixed cards and the saving of
// the month before. A card deleted later stays deleted. Months already past are left as they are.
function openMonth(b, month, today = thisMonth()) {
  if (month < today || month in b.months) return false;
  const before = Object.keys(b.months)
    .filter((m) => m < month)
    .sort();
  b.months[month] = before.length ? b.months[before[before.length - 1]] : 0;
  for (const t of b.types) {
    if (t.fixed && !t.hidden) b.entries.push({ id: b.next++, type: t.id, month, amount: t.amount, note: "" });
  }
  return true;
}

// A type just made fixed joins the months already opened, from this month on.
function joinMonths(b, t, today = thisMonth()) {
  for (const month of Object.keys(b.months).sort()) {
    if (month >= today && !b.entries.some((e) => e.type === t.id && e.month === month)) {
      b.entries.push({ id: b.next++, type: t.id, month, amount: t.amount, note: "" });
    }
  }
}

// A type's usual amount and its "every month" mark. A new rent on a fixed type moves the cards
// from this month on that still have the old amount.
function setUsual(b, t, amount, fixed, today = thisMonth()) {
  const was = { amount: t.amount, fixed: t.fixed };
  t.amount = amount;
  t.fixed = fixed;
  if (fixed && !was.fixed) joinMonths(b, t, today);
  else if (fixed && amount !== was.amount) {
    for (const e of b.entries) {
      if (e.type === t.id && e.month >= today && e.amount === was.amount) e.amount = amount;
    }
  }
}

// Bars for what each month of the year saved, and a line for the sum so far.
function chart(totals, year) {
  const vals = [];
  for (let m = 1; m <= 12; m++) {
    const key = `${year}-${String(m).padStart(2, "0")}`;
    vals.push(key in totals ? totals[key] : null);
  }
  const last = vals.reduce((at, v, i) => (v === null ? at : i), -1);
  const runs = [];
  let run = 0;
  for (const v of vals.slice(0, last + 1)) {
    run += v || 0;
    runs.push(run);
  }
  const seen = vals.filter((v) => v !== null).concat(runs);
  const lo = Math.min(0, ...seen);
  const hi = Math.max(0, ...seen);
  const span = hi - lo || 1;
  const y = (v) => Math.round((10 + ((hi - v) / span) * 160) * 10) / 10;
  const bars = [];
  vals.forEach((v, i) => {
    if (v === null) return;
    const [top, bottom] = [y(v), y(0)].sort((a, b) => a - b);
    bars.push({ i, x: 12 + i * 48, y: top, h: Math.max(bottom - top, 1), v });
  });
  return { bars, dots: runs.map((r, i) => [24 + i * 48, y(r)]), zero: y(0), total: run };
}

// Everything the plan and the overview of a month show.
function monthValues(b, month) {
  const entries = b.entries
    .filter((e) => e.month === month)
    .map((e) => ({ ...e, t: typeOf(b, e.type) }))
    .filter((e) => e.t);
  let saved = 0;
  const totals = {};
  for (const [m, v] of Object.entries(b.months)) {
    if (m <= month) {
      saved += v;
      totals[m] = v;
    }
  }
  let net = 0;
  for (const e of b.entries) {
    const t = e.month <= month && typeOf(b, e.type);
    if (t) net += t.side === "in" ? e.amount : -e.amount;
  }
  // The savings account gets what was saved; the first other account keeps the rest.
  let restGiven = false;
  const accounts = b.accounts.map((a) => {
    const rest = !a.savings && !restGiven;
    restGiven = restGiven || rest;
    return { ...a, rest, balance: a.start + (a.savings ? saved : rest ? net - saved : 0) };
  });
  const sum = (side, fixedOnly) =>
    entries.filter((e) => e.t.side === side && (!fixedOnly || e.t.fixed)).reduce((s, e) => s + e.amount, 0);
  const saving = b.months[month] || 0;
  const totalIn = sum("in");
  const totalOut = sum("out");
  const fixed = sum("out", true);
  return {
    month,
    types: b.types.filter((t) => !t.hidden),
    entries,
    saving,
    totalIn,
    totalOut,
    fixed,
    other: totalOut - fixed,
    left: totalIn - totalOut - saving,
    accounts,
    totalAcc: accounts.reduce((s, a) => s + a.balance, 0),
    chart: chart(totals, Number(month.slice(0, 4))),
  };
}

if (typeof module === "object") {
  module.exports = { newBudget, clean, shift, parseAmount, openMonth, joinMonths, setUsual, chart, monthValues };
}

// Words, numbers and money in the chosen language

let WORDS = {};
let lang = "nb";
const NBSP = " ";
const SYMBOLS = { NOK: ["kr", "NOK\u00a0"], EUR: ["€", "€"], USD: ["$", "$"] };

function t(key, kw) {
  const w = Object.hasOwn(WORDS, key) ? WORDS[key] : [key, key];
  let s = w[lang === "en" ? 1 : 0];
  for (const k in kw || {}) s = s.replace(`{${k}}`, kw[k]);
  return s;
}

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// The name of a card type or account: the user's own name, or the starting one translated.
const named = (row) => row.name || (Object.hasOwn(WORDS, row.key) ? t(row.key) : "?");
const label = (row) => esc(named(row));

const monthName = (month) => {
  const name = WORDS._months[lang === "en" ? 1 : 0][Number(month.slice(5)) - 1];
  return `${name[0].toUpperCase()}${name.slice(1)} ${month.slice(0, 4)}`;
};

// 12500 as 12 500 in Norwegian and 12,500 in English. Decimals only when there are any.
function number(v) {
  v = Math.abs(v);
  const [whole, frac] = v.toFixed(Math.abs(v - Math.round(v)) < 0.005 ? 0 : 2).split(".");
  const nb = lang === "nb";
  const text = whole.replace(/\B(?=(\d{3})+(?!\d))/g, nb ? NBSP : ",");
  return frac ? text + (nb ? "," : ".") + frac : text;
}

function money(v) {
  const sign = v < -0.004 ? "−" : "";
  const [nb, en] = SYMBOLS[budget.currency] || SYMBOLS.NOK;
  return lang === "nb" ? `${sign}${number(v)}${NBSP}${nb}` : `${sign}${en}${number(v)}`;
}

// An amount as it stands in an input box: digits only, empty for 0.
const plain = (v) => (v ? (v < -0.004 ? "-" : "") + number(v) : "");

// Where the budget is kept

let budget = null;
let store = null; // device or server
let me = null; // the logged-in user, when the budget is on the server

function local(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (e) {}
  return null;
}

const device = {
  load() {
    let b = null;
    try {
      b = clean(JSON.parse(local("spare-budget")));
    } catch (e) {}
    return b || newBudget();
  },
  save() {
    try {
      localStorage.setItem("spare-budget", JSON.stringify(budget));
    } catch (e) {
      flash(t("device_full"));
    }
  },
};

// The server keeps a version number. A save from a page that missed a save made elsewhere is
// refused, and the page shows the budget as it now is. One save at a time, the last one wins.
const server = {
  version: 0,
  csrf: "",
  busy: false,
  again: false,
  failed: false,
  async save() {
    if (this.busy) {
      this.again = true;
      return;
    }
    this.busy = true;
    do {
      this.again = false;
      const body = JSON.stringify({ version: this.version, budget });
      try {
        const r = await fetch("/api/budget", {
          method: "PUT",
          body,
          keepalive: body.length < 60000, // the save still goes if the page closes
          headers: { "Content-Type": "application/json", "X-CSRF": this.csrf },
        });
        if (r.status === 401) return location.assign("/login");
        const d = await r.json();
        if (r.status === 409) {
          budget = clean(d.budget) || budget;
          this.version = d.version;
          draw();
          flash(t("changed_elsewhere"));
        } else if (r.ok) {
          this.version = d.version;
          this.failed = false;
        } else throw new Error(r.status);
      } catch (e) {
        this.failed = true;
        flash(t("not_saved"));
      }
    } while (this.again);
    this.busy = false;
  },
  // Language and light or dark, so the login page follows the choice.
  post(fields) {
    const body = new FormData();
    for (const [k, v] of Object.entries(fields)) body.append(k, v);
    fetch("/prefs", { method: "POST", body, redirect: "manual", headers: { "X-CSRF": this.csrf } }).catch(() => {});
  },
};

function useDevice() {
  store = device;
  me = null;
  local("spare-mode", "device");
  budget = device.load();
  device.save();
}

function useServer(d) {
  store = server;
  me = d.me;
  server.csrf = d.csrf;
  server.version = d.version;
  local("spare-mode", "server");
  if (!local("spare-lang") && d.me.lang) lang = d.me.lang;
  if (!local("spare-theme") && d.me.theme) applyTheme(d.me.theme);
  budget = clean(d.budget);
  if (!budget) {
    budget = newBudget();
    budget.currency = CURRENCIES.includes(d.me.currency) ? d.me.currency : "NOK";
    server.save();
  }
}

// The pages

const $ = (sel, root = document) => root.querySelector(sel);
let screen = { name: "" };

function route() {
  const h = location.hash.replace(/^#\/?/, "");
  let m;
  if (h === "start") return { name: "start" };
  if ((m = h.match(/^setup(?:\/(\w+))?$/))) return { name: "setup", part: m[1] };
  if ((m = h.match(/^m\/(\d{4}-(?:0[1-9]|1[0-2]))(\/cards)?$/))) {
    return { name: m[2] ? "cards" : "plan", month: m[1] };
  }
  return { name: h === "cards" ? "cards" : "plan", month: thisMonth() };
}

function flash(text) {
  const box = $("#flash");
  box.textContent = text;
  box.hidden = !text;
}

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

function drawHeader() {
  const month = screen.month || thisMonth();
  const on = (name) => (screen.name === name ? ' class="on"' : "");
  const nav = store
    ? `<a id="nav-plan" href="#/m/${month}"${on("plan")}>${t("plan")}</a>
    <a id="nav-overview" href="#/m/${month}/cards"${on("cards")}>${t("overview")}</a>
    <a id="nav-setup" href="#/setup"${on("setup")}>${t("setup")}</a>
    ${me && me.admin ? `<a href="/admin">${t("users")}</a>` : ""}`
    : "";
  const logout = me
    ? `<form method="post" action="/logout" class="inline"><input type="hidden" name="csrf" value="${esc(server.csrf)}">
    <button class="link">${t("logout")}</button></form>`
    : "";
  $("header").innerHTML = `<a class="brand" href="#/"><img src="/static/logo.svg" alt="">spare</a>
  <nav>${nav}
    <button type="button" class="link" data-act="lang">${lang === "nb" ? "English" : "Norsk"}</button>
    <button type="button" class="link to-dark" data-act="dark">${t("dark")}</button>
    <button type="button" class="link to-light" data-act="light">${t("light")}</button>
    ${logout}
  </nav>`;
}

function drawStart() {
  return `<div class="box">
  <p class="tagline">${t("tagline")}</p>
  <h1>${t("start_title")}</h1>
  <button type="button" data-act="use-device">${t("use_device")}</button>
  <p class="small">${t("use_device_help")}</p>
  <a class="button c-blue" href="/login" data-act="use-server">${t("use_server")}</a>
  <p class="small">${t("use_server_help")}</p>
</div>
<div class="install">
  <button type="button" class="c-blue" data-install="win-help">${t("install_win")}</button>
  <button type="button" class="c-purple" data-install="mac-help">${t("install_mac")}</button>
  <p id="win-help" class="small" hidden>${t("install_win_help")}</p>
  <p id="mac-help" class="small" hidden>${t("install_mac_help")}</p>
</div>`;
}

function monthbar(v, cards, extra = "") {
  const end = cards ? "/cards" : "";
  return `<div class="monthbar">
  <a class="step" href="#/m/${shift(v.month, -1)}${end}" aria-label="${t("prev")}">‹</a>
  <h1>${monthName(v.month)}</h1>
  <a class="step" href="#/m/${shift(v.month, 1)}${end}" aria-label="${t("next")}">›</a>${extra}
</div>`;
}

function column(v, side, title, sum) {
  const chips = v.types
    .filter((ty) => ty.side === side)
    .map((ty) => `<button type="button" class="chip c-${ty.colour}" data-add="${ty.id}">+ ${label(ty)}</button>`)
    .join("");
  const cards =
    v.entries
      .filter((e) => e.t.side === side)
      .map(
        (e) => `<form class="card c-${e.t.colour}" data-kind="entry" data-id="${e.id}">
      <input name="note" value="${esc(e.note)}" placeholder="${label(e.t)}" maxlength="80" aria-label="${t("note")}">
      <input name="amount" class="amount" inputmode="decimal" value="${plain(e.amount)}" placeholder="0" aria-label="${t("amount")}">
      <button class="vh">${t("save")}</button>
      <button type="button" class="x" data-act="del-entry" data-id="${e.id}" aria-label="${t("delete")}">×</button>
    </form>`
      )
      .join("") || `<p class="empty">${t("empty")}</p>`;
  return `<section class="col ${side}" id="${side}">
  <h2>${title} <span id="sum-${side}" data-fresh>${money(sum)}</span></h2>
  <div class="palette">${chips}</div>
  <div class="cards">${cards}</div>
</section>`;
}

const TOUR = ["#left", "#in", "#fixed", "#saving-row", "#nav-overview", "#nav-setup"];

function drawPlan(v) {
  return `${monthbar(v, false)}
<div class="plan">
  <p id="left" class="left ${v.left >= 0 ? "up" : "down"}" data-fresh>${t("can_spend")} <strong>${money(v.left)}</strong></p>
  ${column(v, "in", t("income"), v.totalIn)}
  <p class="card line out" id="fixed" data-fresh><span>${t("fixed_costs")}</span><strong>${money(-v.fixed)}</strong></p>
  <form class="card line acc" id="saving-row" data-kind="saving" data-month="${v.month}">
    <label for="saving">${t("saving")}</label>
    <input id="saving" name="saving" class="amount" inputmode="decimal" value="${plain(v.saving)}" placeholder="0">
    <button class="vh">${t("save")}</button>
  </form>
  <p class="card line c-red" id="other" data-fresh><span>${t("other_spending")}</span><strong>${money(-v.other)}</strong></p>
  <p class="small"><a href="#/m/${v.month}/cards">${t("see_cards")}</a>. ${t("plan_help")}
    <button type="button" class="link" data-act="tour">${t("tour")}</button></p>
</div>
<dialog id="tour" class="tour" aria-label="${t("tour")}">
  ${TOUR.map((part, i) => `<p data-for="${part}" hidden>${t(`tour_${i + 1}`)}</p>`).join("")}
  <div><button type="button" data-act="tour-next">${t("tour_next")}</button>
  <button type="button" class="link" data-act="tour-skip">${t("tour_skip")}</button></div>
</dialog>`;
}

function drawCards(v) {
  const names = WORDS._months[lang === "en" ? 1 : 0];
  const accounts = v.accounts
    .map(
      (a) => `<div class="card line ${a.savings ? "c-green" : "c-blue"}">
      <span>${label(a)}</span>
      <strong>${money(a.balance)}</strong>
      ${a.savings ? `<span class="small">${t("savings_here")}: ${money(v.saving)} ${t("this_month")}</span>` : ""}
      ${a.rest ? `<span class="small">${t("rest_here")}: ${money(v.left)} ${t("this_month")}</span>` : ""}
    </div>`
    )
    .join("");
  const c = v.chart;
  const bars = c.bars
    .map(
      (b) => `<rect class="bar ${b.v >= 0 ? "up" : "down"}" x="${b.x}" y="${b.y}" width="24" height="${b.h}" rx="3">
      <title>${names[b.i]}: ${money(b.v)}</title></rect>`
    )
    .join("");
  const line = c.dots.length > 1 ? `<polyline class="run" points="${c.dots.map((d) => d.join(",")).join(" ")}"/>` : "";
  const dots = c.dots.map(([x, y]) => `<circle class="dot" cx="${x}" cy="${y}" r="4"/>`).join("");
  const labels = names.map((m, i) => `<text x="${24 + i * 48}" y="194">${m.slice(0, 3)}</text>`).join("");
  const left = `<p id="left" class="saved ${v.left >= 0 ? "up" : "down"}" data-fresh>${t("left_to_spend")}: ${money(v.left)}</p>`;
  return `${monthbar(v, true, left)}
<nav class="tabs">
  <button type="button" class="in" data-tab="in">${t("income")}</button>
  <button type="button" class="out" data-tab="out">${t("expenses")}</button>
  <button type="button" class="acc" data-tab="acc">${t("accounts")}</button>
</nav>
<div class="cols">
${column(v, "in", t("income"), v.totalIn)}
${column(v, "out", t("expenses"), v.totalOut)}
<section class="col acc" id="acc">
  <h2>${t("accounts")} <span id="sum-acc" data-fresh>${money(v.totalAcc)}</span></h2>
  <div class="cards" id="accounts" data-fresh>${accounts}</div>
  <p class="small"><a href="#/setup/accounts">${t("edit_accounts")}</a></p>
</section>
</div>
<section class="chart" id="chart" data-fresh>
  <h2>${t("saved_in", { month: v.month.slice(0, 4) })}: ${money(c.total)}</h2>
  <svg viewBox="0 0 600 200" role="img" aria-label="${t("chart_help")}">
    <line class="zero" x1="0" x2="600" y1="${c.zero}" y2="${c.zero}"/>${bars}${line}${dots}${labels}
  </svg>
  <p class="small">${t("chart_help")}</p>
</section>`;
}

const swatches = (current) =>
  `<span class="swatches">${COLOURS.map(
    (c) => `<label class="swatch c-${c}" title="${t(c)}"><input type="radio" name="colour" value="${c}"${
      c === current ? " checked" : ""
    }><span></span></label>`
  ).join("")}</span>`;

function drawSetup() {
  const types = budget.types.filter((ty) => !ty.hidden);
  const sides = [
    ["in", t("income")],
    ["out", t("expenses")],
  ].map(([side, title]) => {
    const mine = types.filter((ty) => ty.side === side);
    const free = COLOURS.find((c) => !mine.some((ty) => ty.colour === c)) || COLOURS[0];
    const rows = mine
      .map(
        (ty) => `<form class="card typerow c-${ty.colour}" data-kind="type" data-id="${ty.id}">
      <input name="name" value="${label(ty)}" maxlength="40" required aria-label="${t("name")}">
      <label class="small">${t("usual_amount")} <input name="amount" class="amount" inputmode="decimal" value="${plain(ty.amount)}" placeholder="0"></label>
      <label class="small check"><input type="checkbox" name="fixed" value="1"${ty.fixed ? " checked" : ""}> ${t("every_month")}</label>
      ${swatches(ty.colour)}
      <button class="vh">${t("save")}</button>
      <button type="button" class="link" data-act="hide-type" data-id="${ty.id}">${t("remove")}</button>
    </form>`
      )
      .join("");
    return `<div class="col ${side}">
    <h3>${title}</h3>
    <div class="cards">${rows}</div>
    <form class="newrow" data-kind="new-type" data-side="${side}">
      <input name="name" maxlength="40" required placeholder="${t("new_type")}" aria-label="${t("new_type")}">
      <input name="amount" class="amount" inputmode="decimal" placeholder="${t("usual_amount")}" aria-label="${t("usual_amount")}">
      <label class="small check"><input type="checkbox" name="fixed" value="1"> ${t("every_month")}</label>
      ${swatches(free)}
      <button>${t("add")}</button>
    </form>
  </div>`;
  });
  const accounts = budget.accounts
    .map(
      (a) => `<form class="card typerow ${a.savings ? "c-green" : "c-blue"}" data-kind="account" data-id="${a.id}">
    <input name="name" value="${label(a)}" maxlength="40" required aria-label="${t("name")}">
    <label class="small">${t("start")} <input name="start" class="amount" inputmode="decimal" value="${plain(a.start)}" placeholder="0"></label>
    <button class="vh">${t("save")}</button>
    ${
      a.savings
        ? `<span class="small strong">${t("savings_here")}</span>`
        : `<button type="button" class="link" data-act="savings" data-id="${a.id}">${t("make_savings")}</button>`
    }
    <button type="button" class="link danger" data-act="del-account" data-id="${a.id}">${t("delete")}</button>
  </form>`
    )
    .join("");
  const option = (value, name, now) => `<option value="${value}"${value === now ? " selected" : ""}>${name}</option>`;
  return `<h1>${t("setup")}</h1>
<section id="types">
<h2>${t("card_types")}</h2>
<div class="cols two">${sides.join("")}</div>
<p class="small">${t("types_help")}</p>
</section>
<section id="accounts">
<h2>${t("accounts")}</h2>
<div class="cards">${accounts}</div>
<form class="newrow" data-kind="new-account">
  <input name="name" maxlength="40" required placeholder="${t("new_account")}" aria-label="${t("new_account")}">
  <input name="start" inputmode="decimal" placeholder="${t("start")}" aria-label="${t("start")}">
  <button>${t("add")}</button>
</form>
</section>
<section id="prefs">
<h2>${t("language")} · ${t("currency")}</h2>
<form class="newrow" data-kind="prefs">
  <label>${t("language")} <select name="lang">${option("nb", "Norsk", lang)}${option("en", "English", lang)}</select></label>
  <label>${t("currency")} <select name="currency">${CURRENCIES.map((c) => option(c, c, budget.currency)).join("")}</select></label>
</form>
</section>
${me ? drawPassword() : drawBackup()}`;
}

function drawPassword() {
  return `<section id="password">
<h2>${t("change_pw")}</h2>
<form class="box flat" data-kind="password">
  <input type="hidden" name="email" value="${esc(me.email)}" autocomplete="username">
  <label>${t("present_pw")} <input name="password" type="password" required autocomplete="current-password"></label>
  <label>${t("new_pw")} <input name="new_password" type="password" minlength="12" required autocomplete="new-password"></label>
  <label>${t("new_pw2")} <input name="new_password2" type="password" minlength="12" required autocomplete="new-password"></label>
  <button>${t("change_pw")}</button>
</form>
</section>`;
}

function drawBackup() {
  return `<section id="backup">
<h2>${t("backup")}</h2>
<p class="small">${t("backup_help")}</p>
<div class="newrow">
  <button type="button" data-act="backup-save">${t("backup_save")}</button>
  <label class="button c-blue">${t("backup_open")}<input type="file" accept=".json,application/json" data-act="backup-open" hidden></label>
</div>
<p class="small"><a href="#/start">${t("to_login")}</a></p>
</section>`;
}

// Draw the page the address asks for. On the same page again, the scroll place stays.
function draw() {
  const next = store ? route() : { name: "start" };
  const same = next.name === screen.name && next.month === screen.month;
  const cols = $(".cols");
  const spot = same ? [scrollY, cols ? cols.scrollLeft : 0] : null;
  screen = next;
  if (!same) flash("");
  let html;
  if (screen.name === "start") html = drawStart();
  else if (screen.name === "setup") html = drawSetup();
  else {
    if (openMonth(budget, screen.month)) store.save();
    const v = monthValues(budget, screen.month);
    html = screen.name === "cards" ? drawCards(v) : drawPlan(v);
  }
  drawHeader();
  $("main").innerHTML = html;
  document.title = `${screen.month ? monthName(screen.month) : screen.name === "setup" ? t("setup") : "spare"} · spare`;
  if (spot) {
    if ($(".cols")) $(".cols").scrollLeft = spot[1];
    scrollTo(0, spot[0]);
  } else if (screen.part && document.getElementById(screen.part)) document.getElementById(screen.part).scrollIntoView();
  else scrollTo(0, 0);
  if (screen.name === "plan" && !local("spare-tour")) tour(0);
}

// After a typed amount or a tick, only the sums, balances and chart are drawn again, so the
// cursor and the keyboard stay where they are.
function refresh() {
  if (!screen.month) return;
  const page = document.createElement("template");
  page.innerHTML = (screen.name === "cards" ? drawCards : drawPlan)(monthValues(budget, screen.month));
  for (const part of document.querySelectorAll("main [data-fresh]")) {
    const fresh = page.content.getElementById(part.id);
    if (fresh) part.replaceWith(fresh);
  }
}

// The tour: on the first visit to the plan, a box at the bottom explains one part at a time
// and rings it. "Show me around" at the foot of the plan starts it again.
let tourAt = -1;
function tour(i) {
  const box = $("#tour");
  if (!box) return;
  for (const old of document.querySelectorAll(".toured")) old.classList.remove("toured");
  const steps = [...box.querySelectorAll("[data-for]")];
  steps.forEach((step, n) => (step.hidden = n !== i));
  tourAt = i;
  if (i < 0 || i >= steps.length) {
    box.close();
    local("spare-tour", "seen");
    return;
  }
  const part = $(steps[i].dataset.for);
  if (part) {
    part.classList.add("toured");
    part.scrollIntoView({ block: "center" });
  }
  $("[data-act=tour-next]", box).textContent = t(i === steps.length - 1 ? "tour_done" : "tour_next");
  if (!box.open) box.show();
}

// Changes

function save() {
  store.save();
  draw();
}

function setLang(value) {
  value = value === "en" ? "en" : "nb";
  if (value !== lang) {
    lang = value;
    local("spare-lang", value);
    document.documentElement.lang = value;
    if (me) server.post({ lang: value });
  }
  draw();
}

function setTheme(value) {
  local("spare-theme", value);
  applyTheme(value);
  if (me) server.post({ theme: value });
}

function addEntry(typeId) {
  const ty = typeOf(budget, typeId);
  if (!ty || !screen.month) return;
  const e = { id: budget.next++, type: ty.id, month: screen.month, amount: ty.amount, note: "" };
  budget.entries.push(e);
  save();
  const amount = $(`form[data-kind=entry][data-id="${e.id}"] [name=amount]`);
  if (amount) {
    amount.focus();
    amount.select();
  }
}

// A form saves its fields the moment one changes, or when Enter is pressed in it.
function apply(form) {
  const f = new FormData(form);
  const sent = new URLSearchParams(f).toString();
  if (form.dataset.sent === sent) return; // Enter and the change it causes save it once
  form.dataset.sent = sent;
  const id = Number(form.dataset.id);
  const amount = (name, old) => {
    const v = parseAmount(f.get(name));
    return v === null ? old : v;
  };
  const name = (row) => {
    const text = String(f.get("name") || "").trim().slice(0, 40);
    if (text && text !== named(row)) row.name = text;
  };
  const kind = form.dataset.kind;
  if (kind === "entry") {
    const e = budget.entries.find((x) => x.id === id);
    if (!e) return;
    e.note = String(f.get("note") || "").trim().slice(0, 80);
    e.amount = amount("amount", e.amount);
  } else if (kind === "saving") {
    const month = form.dataset.month;
    openMonth(budget, month);
    budget.months[month] = amount("saving", budget.months[month] || 0);
  } else if (kind === "type") {
    const ty = typeOf(budget, id);
    if (!ty) return;
    name(ty);
    if (COLOURS.includes(f.get("colour"))) {
      ty.colour = f.get("colour");
      form.className = form.className.replace(/\bc-\S+/, `c-${ty.colour}`);
    }
    setUsual(budget, ty, amount("amount", ty.amount), f.get("fixed") === "1");
  } else if (kind === "account") {
    const a = budget.accounts.find((x) => x.id === id);
    if (!a) return;
    name(a);
    a.start = amount("start", a.start);
  } else if (kind === "prefs") {
    if (CURRENCIES.includes(f.get("currency"))) budget.currency = f.get("currency");
    store.save();
    return setLang(f.get("lang")); // every word and amount changes, so the page is drawn again
  }
  store.save();
  refresh();
}

function newType(form) {
  const f = new FormData(form);
  const ty = {
    id: budget.next++,
    side: form.dataset.side === "in" ? "in" : "out",
    key: "",
    name: String(f.get("name") || "").trim().slice(0, 40),
    colour: COLOURS.includes(f.get("colour")) ? f.get("colour") : COLOURS[0],
    hidden: false,
    amount: parseAmount(f.get("amount")) || 0,
    fixed: f.get("fixed") === "1",
  };
  if (!ty.name) return;
  budget.types.push(ty);
  if (ty.fixed) joinMonths(budget, ty);
  save();
}

function newAccount(form) {
  const f = new FormData(form);
  const name = String(f.get("name") || "").trim().slice(0, 40);
  if (!name) return;
  const start = parseAmount(f.get("start")) || 0;
  budget.accounts.push({ id: budget.next++, key: "", name, start, savings: !budget.accounts.length });
  save();
}

async function changePassword(form) {
  try {
    const r = await fetch("/api/password", { method: "POST", body: new FormData(form), headers: { "X-CSRF": server.csrf } });
    const d = await r.json();
    if (d.ok) form.reset();
    flash(d.message);
  } catch (e) {
    flash(t("no_server"));
  }
  scrollTo(0, 0);
}

const today = (d = new Date()) => `${thisMonth(d)}-${String(d.getDate()).padStart(2, "0")}`;

// A backup is a file on the person's own machine. Opening one replaces the budget on this
// device; nothing is ever mixed.
function saveBackup() {
  const file = new Blob([JSON.stringify({ spare: 1, saved: today(), budget }, null, 1)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(file);
  link.download = `${today()}_spare.json`;
  document.body.append(link);
  link.click();
  link.remove();
}

async function openBackup(input) {
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  let read = null;
  try {
    read = clean(JSON.parse(await file.text()).budget);
  } catch (e) {}
  if (!read) return flash(t("backup_bad"));
  if (!confirm(t("backup_replace"))) return;
  budget = read;
  save();
}

// Events: every handler waits for a click, a change or a key. Nothing polls.

let installer = null;
let drag = null;
let skipClickUntil = 0;

function onClick(e) {
  const el = e.target.closest("[data-act], [data-add], [data-tab], [data-install]");
  if (!el) return;
  if (el.dataset.add) return addEntry(Number(el.dataset.add));
  if (el.dataset.tab) return document.getElementById(el.dataset.tab).scrollIntoView({ block: "nearest", inline: "start" });
  if (el.dataset.install) {
    if (installer) {
      installer.prompt();
      installer = null;
    } else for (const help of document.querySelectorAll(".install p")) help.hidden = help.id !== el.dataset.install;
    return;
  }
  const id = Number(el.dataset.id);
  switch (el.dataset.act) {
    case "lang":
      return setLang(lang === "nb" ? "en" : "nb");
    case "dark":
    case "light":
      return setTheme(el.dataset.act);
    case "tour":
      return tour(0);
    case "tour-next":
      return tour(tourAt + 1);
    case "tour-skip":
      return tour(-1);
    case "use-device":
      useDevice();
      if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
      history.replaceState(null, "", "#/");
      return draw();
    case "use-server":
      return local("spare-mode", "server"); // the link goes on to the login page
    case "backup-save":
      return saveBackup();
    case "del-entry":
      budget.entries = budget.entries.filter((x) => x.id !== id);
      return save();
    case "hide-type":
      typeOf(budget, id).hidden = true; // the cards already in the months stay
      return save();
    case "savings":
      for (const a of budget.accounts) a.savings = a.id === id;
      return save();
    case "del-account":
      budget.accounts = budget.accounts.filter((x) => x.id !== id);
      return save();
  }
}

function onChange(e) {
  if (e.target.matches("[data-act=backup-open]")) return openBackup(e.target);
  const form = e.target.closest("form[data-kind]");
  if (form && ["entry", "saving", "type", "account", "prefs"].includes(form.dataset.kind)) apply(form);
}

function onSubmit(e) {
  const form = e.target.closest("form[data-kind]");
  if (!form) return; // the logout form posts as usual
  e.preventDefault();
  const kind = form.dataset.kind;
  if (kind === "new-type") newType(form);
  else if (kind === "new-account") newAccount(form);
  else if (kind === "password") changePassword(form);
  else apply(form);
}

// Drag a card type into the month with a mouse. On a phone a tap adds it, and a swipe scrolls.
function dropColumn(e) {
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const col = under && under.closest(".col");
  return col && col.contains(drag.chip) ? col : null;
}

function onPointerDown(e) {
  const chip = e.target.closest(".chip");
  if (chip && e.pointerType === "mouse" && e.button === 0) drag = { chip, x: e.clientX, y: e.clientY };
}

function onPointerMove(e) {
  if (!drag) return;
  if (!drag.ghost) {
    if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 6) return;
    drag.ghost = drag.chip.cloneNode(true);
    drag.ghost.classList.add("ghost");
    document.body.append(drag.ghost);
  }
  drag.ghost.style.transform = `translate(${e.clientX + 8}px, ${e.clientY + 8}px)`;
  const col = dropColumn(e);
  for (const c of document.querySelectorAll(".col.over")) if (c !== col) c.classList.remove("over");
  if (col) col.classList.add("over");
}

function onPointerUp(e) {
  if (!drag) return;
  const { chip, ghost } = drag;
  const col = ghost && dropColumn(e);
  drag = null;
  if (!ghost) return;
  ghost.remove();
  skipClickUntil = Date.now() + 400; // the click after a drag must not add a second card
  if (col) addEntry(Number(chip.dataset.add));
}

// Start: the device budget needs nothing from the server. Otherwise ask the server once.
async function start() {
  WORDS = JSON.parse($("#words").textContent);
  lang = local("spare-lang") || (/^n[bno]/i.test(navigator.language || "") ? "nb" : "en");
  document.addEventListener("click", onClick);
  document.addEventListener("change", onChange);
  document.addEventListener("submit", onSubmit);
  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", () => {
    if (drag && drag.ghost) drag.ghost.remove();
    drag = null;
  });
  document.addEventListener(
    "click",
    (e) => {
      if (Date.now() < skipClickUntil && e.target.closest(".chip")) e.stopPropagation();
    },
    true
  );
  addEventListener("hashchange", draw);
  addEventListener("online", () => me && server.failed && server.save());
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  const mode = local("spare-mode");
  if (mode === "device") useDevice();
  else if (route().name !== "start") {
    try {
      const r = await fetch("/api/budget");
      if (r.ok) useServer(await r.json());
      else if (r.status === 401 && mode === "server") return location.replace("/login");
    } catch (e) {
      if (mode === "server") flash(t("no_server"));
    }
  }
  document.documentElement.lang = lang;
  draw();
}

if (typeof document === "object") {
  applyTheme(local("spare-theme")); // before the page shows, so it never flashes the wrong colours
  addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    installer = e;
  });
  document.addEventListener("DOMContentLoaded", start);
}
