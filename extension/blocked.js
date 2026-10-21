"use strict";
/*
 * Calma blocked page.
 *
 * URL: blocked.html?rule=<name>&url=<original url>
 * `url` is always the last parameter and may arrive unencoded (the DNR redirect appends
 * the matched URL raw), so everything after "url=" is taken as the original URL.
 *
 * Status and theme come from the service worker when the page runs inside the extension;
 * when opened as a plain file (development) it talks to the daemon directly.
 */

const DAEMON = "http://127.0.0.1:7390";
const FALLBACK_PALETTE = {
  mode: "dark",
  colors: {
    accent: "#7aa2f7",
    background: "#1a1b26",
    lighter_background: "#24283b",
    dark_background: "#13141c",
    foreground: "#a9b1d6",
    bright_foreground: "#c0caf5",
    dark_foreground: "#565f89",
    muted: "#414868",
    selection: "#292e42",
    red: "#f7768e",
  },
  font: null,
};

const $ = (id) => document.getElementById(id);
const els = {
  glyph: $("glyph"),
  label: $("label"),
  message: $("message"),
  budget: $("budget"),
  action: $("action"),
  minutesField: $("minutes-field"),
  minutes: $("minutes"),
  open: $("open"),
  note: $("note"),
  error: $("error"),
};

// ---------------------------------------------------------------------------
// Query parsing
// ---------------------------------------------------------------------------

function parseQuery() {
  const search = location.search.startsWith("?") ? location.search.slice(1) : location.search;
  const i = search.indexOf("url=");
  let url = "";
  let rest = search;
  if (i >= 0) {
    // A raw original URL with a #fragment puts the fragment on our own location.
    url = search.slice(i + 4) + (location.hash || "");
    rest = search.slice(0, i);
    try {
      url = decodeURIComponent(url);
    } catch (e) {
      /* keep raw */
    }
  }
  const params = new URLSearchParams(rest);
  if (url && !/^https?:\/\//i.test(url)) url = "";
  return { rule: params.get("rule") || "", url };
}

const query = parseQuery();
const inExtension = typeof chrome !== "undefined" && !!(chrome.runtime && chrome.runtime.id);

// ---------------------------------------------------------------------------
// Daemon access (through the worker, or direct when running as a file)
// ---------------------------------------------------------------------------

async function viaWorker(msg) {
  if (!inExtension) throw new Error("not in extension");
  const reply = await chrome.runtime.sendMessage(msg);
  if (!reply) throw new Error("no reply");
  return reply;
}

async function direct(path, { method = "GET", body } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 4000);
  try {
    const headers = { Accept: "application/json" };
    if (method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-Calma"] = "1";
    }
    const res = await fetch(DAEMON + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
      cache: "no-store",
    });
    return { httpStatus: res.status, data: await res.json() };
  } finally {
    clearTimeout(t);
  }
}

/** → {status, theme, daemonDown} */
async function getStatus() {
  if (inExtension) {
    try {
      const reply = await viaWorker({ type: "status" });
      return { status: reply.status || null, theme: reply.theme || null, daemonDown: !!reply.daemonDown || !reply.status };
    } catch (e) {
      /* fall through to direct */
    }
  }
  try {
    const { data } = await direct("/status");
    return { status: data, theme: null, daemonDown: false };
  } catch (e) {
    return { status: null, theme: null, daemonDown: true };
  }
}

async function getTheme() {
  if (inExtension) {
    try {
      const reply = await viaWorker({ type: "theme" });
      if (reply && reply.theme) return reply.theme;
    } catch (e) {
      /* fall through */
    }
  }
  try {
    const { data } = await direct("/theme");
    if (data && data.colors) return data;
  } catch (e) {
    /* fallback */
  }
  return null;
}

async function openSession(rule, minutes) {
  const body = { rule };
  if (minutes) body.minutes = minutes;
  if (inExtension) {
    try {
      return await viaWorker({ type: "open", rule, minutes });
    } catch (e) {
      /* fall through */
    }
  }
  const { httpStatus, data } = await direct("/open", { method: "POST", body });
  return { ...data, httpStatus };
}

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------

function applyTheme(theme) {
  const t = theme && theme.colors ? theme : FALLBACK_PALETTE;
  const c = t.colors;
  const root = document.documentElement;
  const set = (name, value) => value && root.style.setProperty(name, value);
  const light = t.mode === "light";
  root.dataset.mode = light ? "light" : "dark";

  set("--bg", c.background);
  set("--bg-raised", c.lighter_background || c.background);
  set("--bg-deep", c.dark_background || c.background);
  set("--fg", c.foreground);
  set("--fg-bright", c.bright_foreground || c.light_foreground || c.foreground);
  set("--fg-dim", c.dark_foreground || c.muted || c.foreground);
  set("--muted", c.muted);
  set("--selection", c.selection);
  set("--accent", c.accent || c.blue);
  set("--red", c.red);
  // Text on the accent button: whichever of background / bright foreground contrasts more.
  const accent = c.accent || c.blue;
  if (accent && c.background) {
    const bright = c.bright_foreground || c.foreground || "#ffffff";
    const ink = contrast(accent, c.background) >= contrast(accent, bright) ? c.background : bright;
    set("--accent-ink", ink);
  }
  if (t.font) {
    const fam = `"${String(t.font).replace(/"/g, "")}"`;
    set("--font", `${fam}, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Inter", "Noto Sans", sans-serif`);
    set("--font-mono", `${fam}, ui-monospace, "JetBrains Mono", "Cascadia Mono", monospace`);
  }
}

function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function luminance(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return 0;
  const n = parseInt(m[1], 16);
  const ch = (v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(n >> 16) + 0.7152 * ch((n >> 8) & 255) + 0.0722 * ch(n & 255);
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function fmtTime(unix) {
  const d = new Date(unix * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  const isTomorrow = d.toDateString() === tomorrow.toDateString();
  const time = `${hh}:${mm}`;
  if (sameDay) return time;
  if (isTomorrow) return `${time} tomorrow`;
  return `${time} on ${d.toLocaleDateString(undefined, { weekday: "long" })}`;
}

function minutesLeft(rule) {
  return Math.max(0, Math.floor((rule.seconds_left ?? 0) / 60));
}

function budgetLine(rule) {
  const parts = [];
  if (typeof rule.sessions_per_day === "number") {
    parts.push(`Sessions today: ${rule.sessions_used ?? 0} of ${rule.sessions_per_day}`);
  }
  if (rule.session_minutes) parts.push(`${rule.session_minutes} min each`);
  // Minutes only matter while a session can still be started.
  if (typeof rule.seconds_left === "number" && (rule.sessions_left ?? 1) > 0) {
    parts.push(`${minutesLeft(rule)} min left`);
  }
  return parts;
}

/** Render the budget parts as non-wrapping spans separated by middle dots. */
function setBudget(parts) {
  els.budget.textContent = "";
  if (typeof parts === "string") parts = parts ? [parts] : [];
  parts.forEach((p, i) => {
    const span = document.createElement("span");
    span.className = "part";
    span.textContent = (i ? "\u00b7\u00a0" : "") + p;
    els.budget.append(span, " ");
  });
}

function show(el, visible) {
  el.hidden = !visible;
}

function setError(text) {
  els.error.textContent = text || "";
  show(els.error, !!text);
}

function hostLabel() {
  try {
    return new URL(query.url).hostname.replace(/^www\./, "");
  } catch (e) {
    return "";
  }
}

function renderDown(status) {
  document.body.dataset.state = "down";
  const rule = status && status.rules && status.rules[query.rule];
  els.glyph.textContent = rule ? glyphFor(rule, rule.label || query.rule || "·") : "·";
  els.label.textContent = "Calma isn't running";
  els.message.textContent = "Everything with a rule stays blocked until the daemon is back. It restarts on its own.";
  els.budget.textContent = "";
  setBudget(rule ? budgetLine(rule) : hostLabel());
  show(els.action, false);
  els.note.innerHTML = "";
  els.note.append("Check with ", code("calma doctor"), ".");
  show(els.note, true);
}

function code(text) {
  const c = document.createElement("code");
  c.textContent = text;
  return c;
}

function renderUnknownRule() {
  document.body.dataset.state = "unknown";
  els.glyph.textContent = "·";
  els.label.textContent = hostLabel() || "Blocked";
  els.message.textContent = "This site is blocked by Calma, but the rule it belongs to isn't in the current status.";
  setBudget([]);
  show(els.action, false);
  els.note.innerHTML = "";
  els.note.append("See ", code("calma status"), " for the rules in force.");
  show(els.note, true);
}

// Nerd Font glyphs for presets whose glyph the daemon leaves empty.
const PRESET_GLYPHS = { youtube: "\uf16a", x: "\u{1d54f}" };

function glyphFor(rule, label) {
  return rule.glyph || PRESET_GLYPHS[rule.preset] || label.charAt(0).toUpperCase();
}

function renderRule(rule) {
  const label = rule.label || query.rule;
  els.glyph.textContent = glyphFor(rule, label);
  els.label.textContent = label;
  setBudget(budgetLine(rule));
  els.note.innerHTML = "";
  setError("");

  if (rule.state === "open") {
    // A path the policy keeps closed inside an open session (X explore, YouTube home…).
    document.body.dataset.state = "open-path";
    els.message.textContent = `Your ${label} session is open. This part of it stays out of the way.`;
    show(els.minutesField, false);
    els.open.textContent = `Go to ${label}`;
    els.open.dataset.mode = "home";
    show(els.action, !!rule.home);
    show(els.note, false);
    return;
  }

  if (rule.can_open) {
    document.body.dataset.state = "blocked";
    const secondsLeft = typeof rule.seconds_left === "number" ? rule.seconds_left : rule.session_minutes * 60;
    const fullLength = rule.session_minutes || 5;
    const available = Math.max(1, Math.min(fullLength, Math.floor(secondsLeft / 60)));
    els.message.textContent = `${label} is resting. Start a session when you're ready.`;
    els.open.dataset.mode = "open";
    if (available < fullLength) {
      els.minutes.min = "1";
      els.minutes.max = String(available);
      els.minutes.value = String(available);
      show(els.minutesField, true);
      els.open.textContent = "Start a session";
    } else {
      show(els.minutesField, false);
      els.open.textContent = `Start a ${fullLength}-minute session`;
    }
    show(els.action, true);
    show(els.note, false);
    return;
  }

  document.body.dataset.state = "blocked";
  show(els.action, false);
  const reason = rule.cannot_open_reason;
  const when = typeof rule.next_opportunity === "number" ? fmtTime(rule.next_opportunity) : null;
  if (reason === "cooling down") {
    els.message.textContent = `${label} is resting for a little longer.`;
    els.note.innerHTML = "";
    els.note.append("Cooling down", when ? ` until ` : "", when ? strong(when) : "");
  } else if (reason === "already open") {
    els.message.textContent = `${label} is open in another window.`;
    els.note.textContent = "";
  } else if (reason === "always blocked") {
    els.message.textContent = `${label} stays closed. That's the rule you set.`;
    els.note.textContent = "";
  } else {
    els.message.textContent = `That's ${label} for today. The rest of the day is yours.`;
    els.note.innerHTML = "";
    if (when) els.note.append("Back at ", strong(when));
    else els.note.textContent = reason ? reason.charAt(0).toUpperCase() + reason.slice(1) : "";
  }
  show(els.note, !!els.note.textContent);
}

function strong(text) {
  const s = document.createElement("strong");
  s.textContent = text;
  return s;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

let currentRule = null;

els.action.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!currentRule) return;
  if (els.open.dataset.mode === "home") {
    location.replace(currentRule.home);
    return;
  }
  const minutes = els.minutesField.hidden ? undefined : Math.max(1, parseInt(els.minutes.value, 10) || 1);
  els.open.disabled = true;
  const original = els.open.textContent;
  els.open.textContent = "Opening";
  setError("");
  try {
    const reply = await openSession(query.rule, minutes);
    if (reply && reply.ok) {
      const target = query.url || (reply.rule && reply.rule.home) || currentRule.home || "/";
      // Give the worker a beat to render the new redirect rules before navigating.
      setTimeout(() => location.replace(target), 250);
      return;
    }
    setError(reply && reply.error ? `Couldn't open: ${reply.error}.` : "Couldn't open the session.");
    if (reply && reply.rule) {
      currentRule = reply.rule;
      renderRule(reply.rule);
    }
  } catch (e) {
    setError("Calma isn't reachable right now.");
  } finally {
    els.open.disabled = false;
    if (els.open.textContent === "Opening") els.open.textContent = original;
  }
});

// ---------------------------------------------------------------------------
// Boot and refresh
// ---------------------------------------------------------------------------

async function refresh() {
  const { status, theme, daemonDown } = await getStatus();
  if (theme) applyTheme(theme);
  if (daemonDown || !status) {
    renderDown(status);
    return;
  }
  const rule = status.rules && status.rules[query.rule];
  if (!rule) {
    currentRule = null;
    renderUnknownRule();
    return;
  }
  currentRule = rule;
  // Someone opened the session elsewhere (CLI, bar): just go.
  if (rule.state === "open" && query.url && document.body.dataset.state === "blocked") {
    location.replace(query.url);
    return;
  }
  renderRule(rule);
}

(async () => {
  applyTheme(null);
  getTheme().then((t) => t && applyTheme(t));
  await refresh();
  setInterval(() => {
    if (document.visibilityState === "visible" && !els.open.disabled) refresh();
  }, 5000);
})();
