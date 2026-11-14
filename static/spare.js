// spare: save a card the moment its amount changes, keep the scroll place when the page
// reloads, and let a card type be dragged into the month as well as tapped. No timers.

const SPOT = "spare-scroll";

function saveScroll() {
  const cols = document.querySelector(".cols");
  try {
    sessionStorage.setItem(SPOT, JSON.stringify([location.pathname, scrollY, cols ? cols.scrollLeft : 0]));
  } catch (e) {}
}

function restoreScroll() {
  let spot = [];
  try {
    spot = JSON.parse(sessionStorage.getItem(SPOT) || "[]");
  } catch (e) {}
  if (spot[0] !== location.pathname) return;
  const cols = document.querySelector(".cols");
  if (cols) cols.scrollLeft = spot[2];
  scrollTo(0, spot[1]);
}
restoreScroll();
addEventListener("load", restoreScroll);

// A card in the month saves in the background, so the keyboard and the cursor stay where they
// are. The server answers with the month page, and the sums, balances and chart come from it.
async function saveCard(form) {
  const body = new FormData(form);
  const sent = new URLSearchParams(body).toString();
  if (form.dataset.sent === sent) return; // Enter and the change it causes send it once
  form.dataset.sent = sent;
  try {
    const answer = await fetch(form.action, { method: "POST", body, keepalive: true });
    if (!answer.ok || new URL(answer.url).pathname === "/login") return location.reload();
    const page = new DOMParser().parseFromString(await answer.text(), "text/html");
    for (const part of document.querySelectorAll("[data-fresh]")) {
      const fresh = page.getElementById(part.id);
      if (fresh) part.replaceWith(fresh);
    }
  } catch (e) {
    form.submit();
  }
}

document.addEventListener("change", (e) => {
  const form = e.target.closest("form");
  if (!form) return;
  if (form.matches("[data-live]")) saveCard(form);
  else if (form.matches("[data-autosubmit]")) form.requestSubmit();
});

document.addEventListener("submit", (e) => {
  const form = e.target;
  if (form.matches("[data-live]") && !(e.submitter && e.submitter.name)) {
    e.preventDefault(); // Enter in a card saves it; only the delete button reloads the page
    saveCard(form);
    return;
  }
  saveScroll();
});

// Drag works with a mouse. On a phone a tap adds the card, and a swipe still scrolls.
let drag = null;
let skipClickUntil = 0;

function dropColumn(e) {
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const col = under && under.closest(".col");
  return col && col.contains(drag.chip) ? col : null;
}

document.addEventListener("pointerdown", (e) => {
  const chip = e.target.closest(".chip");
  if (chip && e.pointerType === "mouse" && e.button === 0) drag = { chip, x: e.clientX, y: e.clientY };
});

document.addEventListener("pointermove", (e) => {
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
});

document.addEventListener("pointerup", (e) => {
  if (!drag) return;
  if (drag.ghost) {
    const col = dropColumn(e);
    drag.ghost.remove();
    if (col) col.classList.remove("over");
    skipClickUntil = Date.now() + 400; // the click after a drag must not add a second card
    if (col) drag.chip.form.requestSubmit(drag.chip);
  }
  drag = null;
});

document.addEventListener(
  "click",
  (e) => {
    if (Date.now() < skipClickUntil && e.target.closest(".chip")) e.preventDefault();
  },
  true
);
