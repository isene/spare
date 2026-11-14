// spare: save a field, box or choice the moment it changes, without a page reload; keep the
// scroll place when the page does reload; let a card type be dragged into the month as well
// as tapped; show a short tour on the first visit. No timers.

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

// A form saves in the background, so the keyboard and the cursor stay where they are. The
// server answers with the page, and the parts marked data-fresh (sums, balances, chart) come
// from it. Language and currency change every word and number, so those still reload.
const quiet = (form) => form.matches("[data-live], [data-autosubmit]:not([data-reload])");

async function save(form) {
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
  if (e.target.name === "colour") form.className = form.className.replace(/\bc-\S+/, "c-" + e.target.value);
  if (quiet(form)) save(form);
  else if (form.matches("[data-autosubmit]")) form.requestSubmit();
});

document.addEventListener("submit", (e) => {
  const form = e.target;
  if (quiet(form) && !(e.submitter && e.submitter.name)) {
    e.preventDefault(); // Enter in a field saves it; only a named button (delete, remove) reloads
    save(form);
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

// The download buttons on the front page. Chrome and Edge install the app when asked;
// Safari and the rest show the steps to do it by hand.
let installer = null;
addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installer = e;
});

document.addEventListener("click", (e) => {
  const button = e.target.closest("[data-install]");
  if (!button) return;
  if (installer) {
    installer.prompt();
    installer = null;
    return;
  }
  for (const help of document.querySelectorAll(".install p")) help.hidden = help.id !== button.dataset.install;
});

// The tour: on the first visit to the plan, a box at the bottom explains one part at a time
// and rings it. "Show me around" at the foot of the plan starts it again.
const tour = document.getElementById("tour");
if (tour) {
  const steps = [...tour.querySelectorAll("[data-for]")];
  let at = -1;
  const show = (i) => {
    for (const old of document.querySelectorAll(".toured")) old.classList.remove("toured");
    steps.forEach((step, n) => (step.hidden = n !== i));
    at = i;
    if (i < 0 || i >= steps.length) {
      tour.close();
      try {
        localStorage.setItem("spare-tour", "seen");
      } catch (e) {}
      return;
    }
    const part = document.querySelector(steps[i].dataset.for);
    if (part) {
      part.classList.add("toured");
      part.scrollIntoView({ block: "center" });
    }
    const last = i === steps.length - 1;
    tour.querySelector("[data-next]").textContent = last ? tour.dataset.wordDone : tour.dataset.wordNext;
    if (!tour.open) tour.show();
  };
  tour.addEventListener("click", (e) => {
    if (e.target.closest("[data-next]")) show(at + 1);
    if (e.target.closest("[data-skip]")) show(-1);
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-tour]")) show(0);
  });
  let seen = "seen";
  try {
    seen = localStorage.getItem("spare-tour");
  } catch (e) {}
  if (!seen) show(0);
}
