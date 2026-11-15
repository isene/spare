// The budget rules in static/spare.js, the same for a budget on a device and on the server.
// Run with: node --test tests/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const r = require("../static/spare.js");

const NOV = "2026-11";
const key = (b, k) => b.types.find((t) => t.key === k);
const add = (b, k, month, amount, note = "") => b.entries.push({ id: b.next++, type: key(b, k).id, month, amount, note });

test("amounts are read both ways of writing", () => {
  assert.equal(r.parseAmount("32 500,50"), 32500.5);
  assert.equal(r.parseAmount("12,500"), 12500);
  assert.equal(r.parseAmount("1.234,5"), 1234.5);
  assert.equal(r.parseAmount("-200 kr"), -200);
  assert.equal(r.parseAmount(""), 0);
  assert.equal(r.parseAmount("abc"), null);
});

test("months step across the new year", () => {
  assert.equal(r.shift("2026-12", 1), "2027-01");
  assert.equal(r.shift("2026-01", -1), "2025-12");
});

test("the plan shows what is left to spend", () => {
  const b = r.newBudget();
  r.setUsual(b, key(b, "housing"), 10000, true, NOV);
  r.openMonth(b, NOV, NOV);
  add(b, "salary", NOV, 32500.5, "Main job");
  add(b, "food", NOV, 1200);
  b.months[NOV] = 5000;
  const v = r.monthValues(b, NOV);
  assert.equal(v.left, 16300.5); // 32 500,50 - 10 000 - 1200 - 5000
  assert.equal(v.fixed, 10000);
  assert.equal(v.other, 1200);
});

test("saving and the rest go to the accounts", () => {
  const b = r.newBudget();
  b.accounts.find((a) => a.savings).start = 1000;
  add(b, "salary", "2026-10", 30000);
  b.months["2026-10"] = 4000;
  add(b, "salary", NOV, 20000);
  add(b, "food", NOV, 1000);
  b.months[NOV] = 5000;
  const v = r.monthValues(b, NOV);
  assert.equal(v.accounts.find((a) => a.savings).balance, 10000); // 1000 + 4000 + 5000
  assert.equal(v.accounts.find((a) => a.rest).balance, 40000); // 26 000 + 14 000
  assert.equal(v.chart.total, 9000);
  assert.equal(v.chart.bars.length, 2);
});

test("a fixed card comes into each new month, and a deleted one stays deleted", () => {
  const b = r.newBudget();
  const bus = key(b, "transport");
  r.setUsual(b, bus, 890, true, NOV);
  r.openMonth(b, NOV, NOV);
  b.months[NOV] = 3000;
  r.openMonth(b, "2026-12", NOV);
  const dec = () => b.entries.filter((e) => e.month === "2026-12" && e.type === bus.id);
  assert.deepEqual(dec().map((e) => e.amount), [890]);
  assert.equal(b.months["2026-12"], 3000); // the saving of the month before
  assert.equal(r.openMonth(b, "2026-09", NOV), false); // past months stay as they were
  assert.equal(r.monthValues(b, NOV).chart.total, 3000); // December is not counted in November
  r.setUsual(b, bus, 950, true, NOV); // a new price moves the cards that had the old one
  assert.deepEqual(dec().map((e) => e.amount), [950]);
  b.entries = b.entries.filter((e) => !(e.month === "2026-12" && e.type === bus.id));
  assert.equal(r.openMonth(b, "2026-12", NOV), false);
  assert.equal(dec().length, 0);
});

test("a type made fixed joins the months already opened, from this month on", () => {
  const b = r.newBudget();
  b.months["2026-10"] = 0;
  b.months[NOV] = 0;
  r.setUsual(b, key(b, "housing"), 9000, true, NOV);
  assert.deepEqual(b.entries.map((e) => e.month), [NOV]);
});

test("a removed card type keeps its old cards", () => {
  const b = r.newBudget();
  add(b, "clothes", NOV, 500);
  key(b, "clothes").hidden = true;
  const v = r.monthValues(b, NOV);
  assert.equal(v.totalOut, 500);
  assert.ok(!v.types.some((t) => t.key === "clothes"));
});

test("a budget from a file or the server is made safe before it is shown", () => {
  assert.equal(r.clean({ types: "x" }), null);
  const b = r.clean({
    currency: "GBP",
    next: 1,
    types: [{ id: 7, side: "x", colour: '"><script>', amount: "12", name: 5 }],
    entries: [
      { id: 9, type: 7, month: "2026-11", amount: "nope" },
      { id: 10, type: 7, month: "bad" },
    ],
    accounts: [],
    months: { "2026-11": "300", bad: 1 },
  });
  assert.equal(b.currency, "NOK");
  assert.deepEqual(b.types[0], { id: 7, side: "out", key: "", name: "5", colour: "green", hidden: false, amount: 12, fixed: false });
  assert.deepEqual(b.entries, [{ id: 9, type: 7, month: "2026-11", amount: 0, note: "" }]);
  assert.deepEqual(b.months, { "2026-11": 300 });
  assert.equal(b.next, 10); // above every id, so a new card never takes an old number
});
