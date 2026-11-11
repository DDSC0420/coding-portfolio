"use strict";

// Intake front end. No build step, no libraries. All CV text is inserted with
// textContent, never as HTML, and the page's Content-Security-Policy blocks inline script.
(() => {
  const $ = (id) => document.getElementById(id);
  const STATUS = { found: "Found", needs_review: "Check", missing: "Not in CV" };
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  let current = null;
  const statusOf = new Map();

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function clickable(node, key) {
    node.dataset.key = key;
    node.tabIndex = 0;
    node.setAttribute("role", "button");
    node.addEventListener("click", () => activate(key));
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(key); }
    });
    return node;
  }

  async function init() {
    const input = $("file");
    const drop = $("drop");
    input.addEventListener("change", () => input.files[0] && upload(input.files[0]));
    for (const type of ["dragenter", "dragover"]) {
      drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add("over"); });
    }
    for (const type of ["dragleave", "drop"]) {
      drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.remove("over"); });
    }
    drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
    $("copy").addEventListener("click", copyJson);
    $("download").addEventListener("click", downloadJson);
    try {
      const info = await (await fetch("/api/info")).json();
      $("engine").textContent = info.extractor === "rules"
        ? "Rule-based extractor" : `LLM: ${info.extractor.replace(/^llm:/, "")}`;
      for (const sample of info.samples) {
        const button = el("button", "sample", sample.label);
        button.type = "button";
        button.dataset.name = sample.name;
        button.addEventListener("click", () => send(`/api/samples/${encodeURIComponent(sample.name)}`, null));
        $("samples").append(button);
      }
    } catch {
      showError("Couldn't reach the Intake server. Is it still running?");
    }
  }

  function upload(file) {
    const form = new FormData();
    form.append("file", file);
    send("/api/extract", form);
  }

  async function send(url, body) {
    showError(null);
    $("loading").hidden = false;
    try {
      const response = await fetch(url, { method: "POST", body });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        showError(typeof data.detail === "string" ? data.detail : `Something went wrong (HTTP ${response.status}).`);
        return;
      }
      render(data);
    } catch {
      showError("Couldn't reach the Intake server. Is it still running?");
    } finally {
      $("loading").hidden = true;
      $("file").value = "";
    }
  }

  function showError(message) {
    $("error").hidden = !message;
    $("error").textContent = message || "";
  }

  // --- rendering ---------------------------------------------------------------------------

  function month(value) {
    if (!value) return "";
    const [year, mon] = value.split("-");
    return mon ? `${MONTHS[Number(mon) - 1]} ${year}` : year;
  }

  function dates(role) {
    if (!role.start) return "No dates";
    return `${month(role.start)} – ${role.current ? "Present" : month(role.end)}`;
  }

  function label(field) {
    const c = current.candidate;
    const m = /^(experience|education)\[(\d+)\]/.exec(field);
    if (m && m[1] === "experience") {
      const role = c.experience[Number(m[2])];
      return `Job ${Number(m[2]) + 1}${role ? ": " + (role.title || role.company || "") : ""}`;
    }
    if (m) return `Education ${Number(m[2]) + 1}`;
    return { name: "Name", email: "Email", phone: "Phone", location: "Location", headline: "Headline",
             years_experience: "Years of experience", contact: "Contact details" }[field] || field;
  }

  function pill(status) {
    return el("span", `pill ${status}`, STATUS[status]);
  }

  function meter(confidence) {
    const box = el("span", "conf");
    box.title = `Confidence ${Math.round(confidence * 100)}%`;
    const bar = el("i");
    bar.style.width = `${Math.round(confidence * 100)}%`; // CSSOM, allowed by the CSP
    box.append(bar);
    return box;
  }

  function group(title, count) {
    const section = el("section", "group");
    const head = el("h2", null, title);
    if (count !== undefined) head.append(el("span", "count", String(count)));
    section.append(head);
    $("fields").append(section);
    return section;
  }

  function row(parent, name, key, field, show) {
    const node = el("div", `row ${field.status}`);
    node.append(el("span", "label", name));
    node.append(el("span", "value", field.value === null ? "—" : (show ? show(field.value) : String(field.value))));
    node.append(pill(field.status));
    if (field.status !== "missing") node.append(meter(field.confidence));
    if (field.note) node.append(el("span", "note", field.note));
    if (field.evidence.length) clickable(node, key);
    statusOf.set(key, field.status);
    parent.append(node);
    return field.evidence;
  }

  function render(result) {
    current = result;
    statusOf.clear();
    const c = result.candidate;
    const evidence = new Map();

    $("cand-name").textContent = c.name.value || "Name not found";
    $("cand-sub").textContent = [c.headline.value, c.location.value].filter(Boolean).join(" · ")
      || result.source.filename;
    const verdict = $("verdict");
    verdict.className = `verdict ${result.needs_review ? "warn" : "ok"}`;
    verdict.textContent = result.needs_review
      ? `${result.review.length} thing${result.review.length === 1 ? "" : "s"} to check` : "Ready to import";

    const notices = $("notices");
    notices.replaceChildren();
    if (result.review.length) {
      const box = el("div", "notice review");
      box.append(el("strong", null, "Check before importing"));
      const list = el("ul");
      for (const item of result.review) {
        const li = el("li");
        li.append(el("b", null, label(item.field)), document.createTextNode(` · ${item.reason}`));
        if (item.field !== "contact" && item.field !== "name") clickable(li, item.field);
        list.append(li);
      }
      box.append(list);
      notices.append(box);
    }
    if (result.rejected.length) {
      const box = el("div", "notice rejected");
      box.append(el("strong", null, "Discarded because they weren't in the CV"));
      const list = el("ul");
      for (const item of result.rejected) list.append(el("li", null, `${item.field}: “${item.value}”`));
      box.append(list);
      notices.append(box);
    }
    for (const warning of result.warnings) notices.append(el("div", "notice warning", warning));

    $("fields").replaceChildren();
    const contact = group("Candidate");
    for (const [name, key, show] of [["Name", "name"], ["Email", "email"], ["Phone", "phone"],
                                     ["Location", "location"], ["Headline", "headline"]]) {
      evidence.set(key, row(contact, name, key, c[key], show));
    }
    if (c.links.length) {
      const node = el("div", "row found");
      node.append(el("span", "label", "Links"));
      const chips = el("span", "chips");
      c.links.forEach((link, i) => {
        chips.append(clickable(el("span", "chip", link.value.replace(/^https?:\/\//, "")), `links.${i}`));
        evidence.set(`links.${i}`, [link.evidence]);
        statusOf.set(`links.${i}`, "found");
      });
      node.append(chips);
      contact.append(node);
    }

    const work = group("Experience", c.experience.length);
    evidence.set("years_experience", row(work, "Years", "years_experience", c.years_experience,
                                         (v) => `${v} years`));
    c.experience.forEach((role, i) => {
      const key = `experience[${i}]`;
      const card = clickable(el("article", `card ${role.status}`), key);
      const top = el("div", "card-top");
      top.append(el("span", "card-title", role.title || "Job title not found"), pill(role.status));
      card.append(top, el("div", "card-sub", `${role.company || "Employer not found"} · ${dates(role)}`));
      if (role.note) card.append(el("div", "note", role.note));
      if (role.highlights.length) {
        const list = el("ul", "highlights");
        for (const h of role.highlights) list.append(el("li", null, h));
        card.append(list);
      }
      work.append(card);
      evidence.set(key, role.evidence);
      statusOf.set(key, role.status);
    });

    const study = group("Education", c.education.length);
    c.education.forEach((entry, i) => {
      const key = `education[${i}]`;
      const card = clickable(el("article", `card ${entry.status}`), key);
      const top = el("div", "card-top");
      top.append(el("span", "card-title", entry.qualification || "Qualification not found"), pill(entry.status));
      card.append(top);
      const sub = [entry.institution, entry.year].filter((x) => x !== null && x !== undefined).join(" · ");
      if (sub) card.append(el("div", "card-sub", sub));
      if (entry.note) card.append(el("div", "note", entry.note));
      study.append(card);
      evidence.set(key, entry.evidence);
      statusOf.set(key, entry.status);
    });

    const skills = group("Skills", c.skills.length);
    const chips = el("div", "chips");
    c.skills.forEach((skill, i) => {
      chips.append(clickable(el("span", "chip", skill.value), `skills.${i}`));
      evidence.set(`skills.${i}`, [skill.evidence]);
      statusOf.set(`skills.${i}`, "found");
    });
    if (!c.skills.length) chips.append(el("span", "empty", "No skills section found"));
    skills.append(chips);

    renderText(result.text, evidence);
    const s = result.source;
    const pages = s.pages ? `${s.pages} page${s.pages === 1 ? "" : "s"} · ` : "";
    $("source-meta").textContent = `${s.filename} · ${pages}${s.characters.toLocaleString()} characters`;
    $("result").hidden = false;
  }

  function renderText(text, evidence) {
    const cv = $("cv");
    cv.replaceChildren();
    cv.scrollTop = 0;
    const spans = [];
    const cuts = new Set([0, text.length]);
    for (const [key, list] of evidence) {
      for (const span of list) {
        spans.push({ key, start: span.start, end: span.end });
        cuts.add(span.start);
        cuts.add(span.end);
      }
    }
    const points = [...cuts].sort((a, b) => a - b);
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i];
      const b = points[i + 1];
      const piece = text.slice(a, b);
      const keys = spans.filter((s) => s.start <= a && s.end >= b).map((s) => s.key);
      if (!keys.length) { cv.append(document.createTextNode(piece)); continue; }
      const mark = el("mark", keys.some((k) => statusOf.get(k) === "needs_review") ? "needs_review" : "", piece);
      mark.dataset.keys = keys.join(" ");
      mark.addEventListener("click", () => activate(keys[keys.length - 1]));
      cv.append(mark);
    }
  }

  function activate(key) {
    for (const node of document.querySelectorAll(".on")) node.classList.remove("on");
    for (const node of document.querySelectorAll(`[data-key="${CSS.escape(key)}"]`)) node.classList.add("on");
    const marks = [...document.querySelectorAll("#cv mark")].filter((m) => m.dataset.keys.split(" ").includes(key));
    for (const mark of marks) mark.classList.add("on");
    if (marks.length) {
      const cv = $("cv");
      cv.scrollTo({ top: Math.max(0, marks[0].offsetTop - cv.clientHeight / 3), behavior: "smooth" });
    }
  }

  // --- export ------------------------------------------------------------------------------

  function record() {
    const { text, ...rest } = current; // the record without the full CV text
    return JSON.stringify(rest, null, 2);
  }

  async function copyJson() {
    if (!current) return;
    const button = $("copy");
    try {
      await navigator.clipboard.writeText(record());
      button.textContent = "Copied";
    } catch {
      button.textContent = "Copy failed";
    }
    setTimeout(() => { button.textContent = "Copy JSON"; }, 1500);
  }

  function downloadJson() {
    if (!current) return;
    const url = URL.createObjectURL(new Blob([record()], { type: "application/json" }));
    const link = el("a");
    link.href = url;
    link.download = `${current.source.filename.replace(/\.[^.]+$/, "")}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  init();
})();
