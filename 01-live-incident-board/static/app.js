// Live incident board client. No framework and no build step:
// the server is the source of truth and this file renders whatever it says.

const STATUSES = ["investigating", "identified", "monitoring", "resolved"];
const SEV_RANK = { sev1: 0, sev2: 1, sev3: 2 };
const room = location.pathname.split("/").pop();
const $ = (id) => document.getElementById(id);

const state = {
  me: null,
  incidents: new Map(),
  people: [],
  activity: [],
  selected: null,
  drill: false,
};

let socket = null;
let retry = 0;
let heartbeat = null;
let toastTimer = null;

// ---------- storage (optional nicety, never required) ----------
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode */ }
}
function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

// ---------- helpers ----------
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text; // textContent only: user text is never parsed as HTML
  return node;
}

function ago(seconds) {
  const diff = Math.max(0, Date.now() / 1000 - seconds);
  if (diff < 10) return "just now";
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), 3200);
}

function send(payload) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
    return true;
  }
  toast("Not connected yet. Reconnecting…");
  return false;
}

// ---------- connection ----------
function setConnection(text, live) {
  $("conn-text").textContent = text;
  $("conn-dot").classList.toggle("live", live);
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws/${room}`);

  socket.addEventListener("open", () => {
    retry = 0;
    const saved = recall("board-name");
    if (saved) socket.send(JSON.stringify({ type: "rename", name: saved }));
    heartbeat = setInterval(() => socket.readyState === WebSocket.OPEN && socket.send('{"type":"ping"}'), 25000);
  });

  socket.addEventListener("message", (event) => receive(JSON.parse(event.data)));

  socket.addEventListener("close", (event) => {
    clearInterval(heartbeat);
    if (event.code === 4000) return setConnection("Unavailable", false); // server refused: don't hammer it
    setConnection("Reconnecting…", false);
    // Exponential backoff with jitter, capped at 10s. On reconnect the server
    // sends a full snapshot, so anything missed while offline is recovered.
    const wait = Math.min(10000, 500 * 2 ** retry++) * (0.6 + Math.random() * 0.8);
    setTimeout(connect, wait);
  });
}

function receive(msg) {
  switch (msg.type) {
    case "snapshot":
      state.me = msg.you;
      state.incidents = new Map(msg.incidents.map((i) => [i.id, i]));
      state.people = msg.people;
      state.activity = msg.activity;
      state.drill = msg.drill;
      setConnection("Live", true);
      render();
      break;
    case "incident": {
      if (msg.removed) state.incidents.delete(msg.removed);
      state.incidents.set(msg.incident.id, msg.incident);
      state.activity.push(msg.activity);
      state.activity = state.activity.slice(-30);
      render(msg.incident.id);
      break;
    }
    case "presence":
      state.people = msg.people;
      state.me = msg.people.find((p) => state.me && p.id === state.me.id) || state.me;
      renderPeople();
      break;
    case "drill":
      state.drill = msg.running;
      renderDrill();
      break;
    case "error":
      // On a conflict the server sends the current incident so we can show the truth.
      if (msg.incident) state.incidents.set(msg.incident.id, msg.incident);
      if (msg.code !== "no_change") toast(msg.message);
      render();
      break;
  }
}

// ---------- actions ----------
function move(incident, status) {
  if (incident.status === status) return;
  send({ type: "move", id: incident.id, status, version: incident.version });
}

// ---------- rendering ----------
function render(changedId) {
  renderBoard(changedId);
  renderActivity();
  renderPeople();
  renderDrawer();
  renderDrill();
}

function renderBoard(changedId) {
  const board = $("board");
  const scroll = board.scrollLeft;
  board.replaceChildren();
  for (const status of STATUSES) {
    const items = [...state.incidents.values()]
      .filter((i) => i.status === status)
      .sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity] || b.updated_at - a.updated_at);

    const column = el("section", `column ${status}`);
    column.dataset.status = status;
    const head = el("header");
    head.append(el("h2", "", status), el("span", "count", String(items.length)));
    column.append(head);

    const list = el("div", "cards");
    if (!items.length) list.append(el("p", "empty", status === "resolved" ? "Nothing resolved yet" : "All clear"));
    for (const incident of items) list.append(card(incident, incident.id === changedId));
    column.append(list);

    column.addEventListener("dragover", (e) => { e.preventDefault(); column.classList.add("over"); });
    column.addEventListener("dragleave", () => column.classList.remove("over"));
    column.addEventListener("drop", (e) => {
      e.preventDefault();
      column.classList.remove("over");
      const incident = state.incidents.get(e.dataTransfer.getData("text/plain"));
      if (incident) move(incident, status);
    });
    board.append(column);
  }
  board.scrollLeft = scroll;
}

function card(incident, changed) {
  const node = el("article", `card ${incident.severity}${changed ? " flash" : ""}`);
  node.draggable = true;
  node.tabIndex = 0;
  node.setAttribute("role", "button");
  node.setAttribute("aria-label", `${incident.severity} ${incident.title}, ${incident.status}. Open detail.`);

  const top = el("div", "card-top");
  top.append(el("span", "sev", incident.severity.toUpperCase()), el("span", "when", ago(incident.updated_at)));
  const last = incident.updates[incident.updates.length - 1];
  node.append(top, el("h3", "", incident.title), el("p", "last", last ? `${last.by}: ${last.text}` : ""));

  const next = STATUSES[STATUSES.indexOf(incident.status) + 1];
  if (next) {
    const advance = el("button", "advance", `Move to ${next} →`);
    advance.type = "button";
    advance.addEventListener("click", (e) => { e.stopPropagation(); move(incident, next); });
    node.append(advance);
  }

  node.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", incident.id));
  const open = () => { state.selected = incident.id; renderDrawer(); $("note").focus({ preventScroll: true }); };
  node.addEventListener("click", open);
  node.addEventListener("keydown", (e) => {
    if (e.target === node && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); open(); }
  });
  return node;
}

function renderActivity() {
  const list = $("activity");
  list.replaceChildren();
  if (!state.activity.length) list.append(el("li", "empty", "No activity yet"));
  for (const entry of [...state.activity].reverse()) {
    const item = el("li");
    const line = el("p");
    line.append(el("strong", "", entry.by), document.createTextNode(` ${entry.text}`));
    item.append(line, el("time", "", ago(entry.at)));
    list.append(item);
  }
}

function renderPeople() {
  const list = $("people");
  list.replaceChildren();
  for (const person of state.people.slice(0, 6)) {
    const chip = el("li", "avatar", person.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase());
    chip.title = person.id === state.me?.id ? `${person.name} (you)` : person.name;
    chip.style.backgroundColor = person.colour;
    list.append(chip);
  }
  if (state.people.length > 6) list.append(el("li", "avatar more", `+${state.people.length - 6}`));
  list.setAttribute("aria-label", `${state.people.length} on this board`);
  if (state.me) $("me").textContent = state.me.name;
}

function renderDrill() {
  $("drill").disabled = state.drill;
  $("drill").textContent = state.drill ? "Drill running…" : "Run drill";
}

function renderDrawer() {
  const incident = state.incidents.get(state.selected);
  const open = Boolean(incident);
  $("drawer").hidden = !open;
  $("scrim").hidden = !open;
  if (!open) { state.selected = null; return; }

  $("d-sev").textContent = incident.severity.toUpperCase();
  $("d-sev").className = `sev ${incident.severity}`;
  $("d-title").textContent = incident.title;
  $("d-meta").textContent = `Declared by ${incident.created_by} · ${ago(incident.created_at)}`;

  const steps = $("d-steps");
  steps.replaceChildren();
  for (const status of STATUSES) {
    const button = el("button", status === incident.status ? "step current" : "step", status);
    button.type = "button";
    button.setAttribute("aria-pressed", String(status === incident.status));
    button.addEventListener("click", () => move(incident, status));
    steps.append(button);
  }

  const timeline = $("d-timeline");
  timeline.replaceChildren();
  for (const update of [...incident.updates].reverse()) {
    const item = el("li", update.kind);
    item.append(el("p", "", update.text), el("time", "", `${update.by} · ${ago(update.at)}`));
    timeline.append(item);
  }
}

function closeDrawer() {
  state.selected = null;
  renderDrawer();
}

// ---------- wiring ----------
$("room-id").textContent = room;

$("declare").addEventListener("submit", (e) => {
  e.preventDefault();
  if (send({ type: "create", title: $("title").value, severity: $("severity").value })) $("title").value = "";
});

$("note-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.selected && send({ type: "note", id: state.selected, text: $("note").value })) $("note").value = "";
});

$("drill").addEventListener("click", () => send({ type: "drill" }));
$("d-close").addEventListener("click", closeDrawer);
$("scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());

$("invite").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(location.href);
    toast("Link copied. Open it in another tab to see live sync.");
  } catch {
    toast("Copy the address bar link to invite someone.");
  }
});

$("me").addEventListener("click", () => {
  $("name-form").hidden = false;
  $("name").value = state.me?.name || "";
  $("name").focus();
});

$("name-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const name = $("name").value.trim();
  if (name && send({ type: "rename", name })) remember("board-name", name);
  $("name-form").hidden = true;
});

if (recall("board-hint") === "off") $("hint").hidden = true;
$("hint-close").addEventListener("click", () => {
  $("hint").hidden = true;
  remember("board-hint", "off");
});

setInterval(() => { renderBoard(); renderActivity(); renderDrawer(); }, 30000); // keep "2m ago" fresh
connect();
