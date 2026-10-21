"use strict";
/*
 * Calma service worker.
 *
 * - Keeps a cache of GET /status from the daemon (127.0.0.1:7390), refreshed by a
 *   long-poll on GET /events and persisted in chrome.storage.session so a restarted
 *   worker fails closed with the last known rules.
 * - Pings POST /ping on startup and every 30 s (alarm).
 * - Renders declarativeNetRequest dynamic rules: main_frame redirects to blocked.html for
 *   every domain of a blocked rule, and "home" redirects for open preset rules.
 * - webNavigation.onCommitted as belt and braces (also catches the policy error page).
 * - When a rule flips to blocked, open tabs on its domains are sent to the blocked page.
 * - Answers messages from the blocked page and content scripts.
 * - Badge: mm:ss of the soonest-ending session.
 */

const DAEMON = "http://127.0.0.1:7390";
const EXT_VERSION = chrome.runtime.getManifest().version;
const PING_ALARM = "calma-ping";
const DOWN_GRACE_MS = 15_000; // daemon unreachable this long → treat every rule as blocked
const DNR_BLOCK_BASE = 1000; // ids 1000+ : blocked-domain redirects
const DNR_HOME_BASE = 100; // ids 100+  : home redirects for open preset rules

const BLOCKED_PAGE = chrome.runtime.getURL("blocked.html");

// ---------------------------------------------------------------------------
// In-memory state (mirrored to chrome.storage.session)
// ---------------------------------------------------------------------------

let status = null; // last /status payload (or null if never seen)
let theme = null; // last /theme payload
let lastOk = 0; // Date.now() of the last successful daemon contact
let lastFail = 0; // Date.now() of the first failure since lastOk (0 = none)
let lastRendered = ""; // signature of the DNR rules last rendered
let polling = false;
let badgeTimer = null;
let stateLoaded = null; // promise

async function loadState() {
  if (stateLoaded) return stateLoaded;
  stateLoaded = (async () => {
    try {
      const saved = await chrome.storage.session.get(["status", "theme", "lastOk", "lastFail"]);
      status = saved.status || null;
      theme = saved.theme || null;
      lastOk = saved.lastOk || 0;
      lastFail = saved.lastFail || 0;
    } catch (e) {
      console.warn("calma: session storage unavailable", e);
    }
  })();
  return stateLoaded;
}

async function saveState() {
  try {
    await chrome.storage.session.set({ status, theme, lastOk, lastFail });
  } catch (e) {
    /* ignore: storage is best effort */
  }
}

// ---------------------------------------------------------------------------
// Daemon access
// ---------------------------------------------------------------------------

async function daemonFetch(path, { method = "GET", body, timeoutMs = 5000 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
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
    const data = await res.json();
    markUp();
    return { httpStatus: res.status, data };
  } finally {
    clearTimeout(t);
  }
}

function markUp() {
  lastOk = Date.now();
  lastFail = 0;
}

function markDown() {
  if (!lastFail) lastFail = Date.now();
}

/** True when the daemon has been unreachable past the grace period. */
function daemonDown() {
  return lastFail !== 0 && Date.now() - lastFail > DOWN_GRACE_MS;
}

async function refreshStatus() {
  try {
    const { data } = await daemonFetch("/status");
    if (data && data.rules) await applyStatus(data);
    return true;
  } catch (e) {
    markDown();
    await onDaemonMaybeDown();
    return false;
  }
}

async function refreshTheme() {
  try {
    const { data } = await daemonFetch("/theme");
    if (data && data.colors) {
      theme = data;
      await saveState();
    }
  } catch (e) {
    /* keep the old theme; the page has an inline fallback */
  }
}

async function ping() {
  try {
    await daemonFetch("/ping", { method: "POST", body: { extension: EXT_VERSION } });
  } catch (e) {
    markDown();
    await onDaemonMaybeDown();
  }
}

// ---------------------------------------------------------------------------
// Status handling
// ---------------------------------------------------------------------------

/** Effective state of a rule: "blocked" also when the daemon is down (fail closed). */
function effectiveState(rule) {
  if (daemonDown()) return "blocked";
  return rule.state === "open" ? "open" : "blocked";
}

function hostMatches(hostname, domain) {
  hostname = (hostname || "").toLowerCase();
  domain = (domain || "").toLowerCase();
  return hostname === domain || hostname.endsWith("." + domain);
}

/** Name of the rule owning `url`'s hostname, or null. */
function ruleForUrl(url) {
  if (!status || !status.rules) return null;
  let host;
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    host = u.hostname;
  } catch (e) {
    return null;
  }
  for (const [name, rule] of Object.entries(status.rules)) {
    for (const d of rule.domains || []) {
      if (hostMatches(host, d)) return name;
    }
  }
  return null;
}

function blockedPageUrl(ruleName, originalUrl) {
  // `url` is the last parameter and is appended raw-ish; blocked.js reads everything
  // after "url=" so unencoded ampersands in the original URL survive.
  return `${BLOCKED_PAGE}?rule=${encodeURIComponent(ruleName)}&url=${encodeURIComponent(originalUrl || "")}`;
}

async function applyStatus(next) {
  const prev = status;
  if (!next._receivedAt) next._receivedAt = Date.now() / 1000;
  status = next;
  await saveState();
  await renderRules();
  await closeNewlyBlockedTabs(prev, next);
  updateBadge();
}

async function onDaemonMaybeDown() {
  // Called on every failure: once the grace period is over, re-render so every rule is
  // treated as blocked. Rules already rendered as blocked persist regardless.
  await saveState();
  if (daemonDown()) {
    await renderRules();
    await closeNewlyBlockedTabs(status, status, /* forceAll */ true);
    updateBadge();
  }
}

// ---------------------------------------------------------------------------
// declarativeNetRequest dynamic rules
// ---------------------------------------------------------------------------

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function domainRegex(domain) {
  // scheme :// optional subdomains . domain (end | / | ? | #)
  return `^https?://([^/?#]*\\.)?${escapeRe(domain)}([/?#].*)?$`;
}

function buildRules() {
  const rules = [];
  if (!status || !status.rules) return rules;
  const down = daemonDown();
  let blockId = DNR_BLOCK_BASE;
  let homeId = DNR_HOME_BASE;
  const seenDomains = new Set();

  for (const [name, rule] of Object.entries(status.rules)) {
    const state = down ? "blocked" : rule.state === "open" ? "open" : "blocked";
    if (state === "blocked") {
      for (const domain of rule.domains || []) {
        const key = domain.toLowerCase();
        if (seenDomains.has(key)) continue;
        seenDomains.add(key);
        rules.push({
          id: blockId++,
          priority: 10,
          action: {
            type: "redirect",
            redirect: {
              regexSubstitution: `${BLOCKED_PAGE}?rule=${encodeURIComponent(name)}&url=\\0`,
            },
          },
          condition: {
            regexFilter: domainRegex(domain),
            resourceTypes: ["main_frame"],
          },
        });
      }
    } else if (rule.preset === "youtube" || rule.preset === "x") {
      const home = rule.home || (rule.preset === "x" ? "https://x.com/home" : "https://www.youtube.com/feed/subscriptions");
      const hosts = rule.preset === "x" ? ["x\\.com", "twitter\\.com"] : ["youtube\\.com"];
      for (const h of hosts) {
        rules.push({
          id: homeId++,
          priority: 5,
          action: { type: "redirect", redirect: { url: home } },
          condition: {
            regexFilter: `^https?://(www\\.|m\\.|mobile\\.)?${h}/?(\\?[^#]*)?(#.*)?$`,
            resourceTypes: ["main_frame"],
          },
        });
        if (rule.preset === "youtube") {
          rules.push({
            id: homeId++,
            priority: 5,
            action: { type: "redirect", redirect: { url: home } },
            condition: {
              regexFilter: `^https?://(www\\.|m\\.)?${h}/shorts([/?#].*)?$`,
              resourceTypes: ["main_frame"],
            },
          });
        }
      }
    }
  }
  return rules;
}

async function renderRules() {
  const rules = buildRules();
  const signature = JSON.stringify(rules);
  if (signature === lastRendered) return;
  try {
    const existing = await chrome.declarativeNetRequest.getDynamicRules();
    await chrome.declarativeNetRequest.updateDynamicRules({
      removeRuleIds: existing.map((r) => r.id),
      addRules: rules,
    });
    lastRendered = signature;
    console.info(`calma: ${rules.length} redirect rules rendered${daemonDown() ? " (daemon down, fail closed)" : ""}`);
  } catch (e) {
    console.error("calma: updateDynamicRules failed", e);
  }
}

// ---------------------------------------------------------------------------
// Tabs: close (redirect) tabs on domains that just became blocked
// ---------------------------------------------------------------------------

async function closeNewlyBlockedTabs(prev, next, forceAll = false) {
  if (!next || !next.rules) return;
  const newlyBlocked = [];
  for (const [name, rule] of Object.entries(next.rules)) {
    const nowBlocked = effectiveState(rule) === "blocked";
    const wasBlocked = prev && prev.rules && prev.rules[name] ? prev.rules[name].state !== "open" : false;
    if (nowBlocked && (forceAll || !wasBlocked)) newlyBlocked.push(name);
  }
  if (!newlyBlocked.length) return;
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({});
  } catch (e) {
    return;
  }
  for (const tab of tabs) {
    if (!tab.url || !tab.id) continue;
    const owner = ruleForUrl(tab.url);
    if (owner && newlyBlocked.includes(owner)) {
      chrome.tabs.update(tab.id, { url: blockedPageUrl(owner, tab.url) }).catch(() => {});
    }
  }
}

/** Belt and braces: redirect any committed navigation whose host is blocked. */
async function onCommitted(details) {
  if (details.frameId !== 0) return;
  await loadState();
  const owner = ruleForUrl(details.url);
  if (!owner) return;
  const rule = status.rules[owner];
  if (effectiveState(rule) !== "blocked") return;
  chrome.tabs.update(details.tabId, { url: blockedPageUrl(owner, details.url) }).catch(() => {});
}

/**
 * The Chromium policy error page (ERR_BLOCKED_BY_ADMINISTRATOR) also ends up here.
 * ERR_ABORTED is a navigation replaced by another one (very common on SPAs) and must be
 * left alone, otherwise we would hijack tabs inside an open session.
 */
async function onErrorOccurred(details) {
  if (details.frameId !== 0) return;
  const error = String(details.error || "");
  if (error.includes("ERR_ABORTED")) return;
  await loadState();
  const owner = ruleForUrl(details.url);
  if (!owner) return;
  const blocked = effectiveState(status.rules[owner]) === "blocked";
  const byPolicy = error.includes("ERR_BLOCKED_BY_ADMINISTRATOR");
  if (!blocked && !byPolicy) return;
  chrome.tabs.update(details.tabId, { url: blockedPageUrl(owner, details.url) }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Badge
// ---------------------------------------------------------------------------

function mmss(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function soonestSession() {
  if (!status || !status.rules || daemonDown()) return null;
  let best = null;
  const nowSec = Date.now() / 1000;
  // Same machine as the daemon, so ends_at (unix) is directly comparable to our clock.
  for (const rule of Object.values(status.rules)) {
    if (rule.state === "open" && rule.session && typeof rule.session.ends_at === "number") {
      const left = rule.session.ends_at - nowSec;
      if (best === null || left < best) best = left;
    }
  }
  return best;
}

function updateBadge() {
  const left = soonestSession();
  const action = chrome.action;
  if (!action) return;
  if (left === null) {
    action.setBadgeText({ text: "" }).catch(() => {});
    if (badgeTimer) {
      clearInterval(badgeTimer);
      badgeTimer = null;
    }
    return;
  }
  const accent = (theme && theme.colors && theme.colors.accent) || "#7aa2f7";
  action.setBadgeBackgroundColor({ color: left < 60 ? "#f7768e" : accent }).catch(() => {});
  action.setBadgeTextColor?.({ color: "#1a1b26" }).catch(() => {});
  action.setBadgeText({ text: mmss(left) }).catch(() => {});
  if (!badgeTimer) badgeTimer = setInterval(updateBadge, 1000);
}

// ---------------------------------------------------------------------------
// Long-poll loop
// ---------------------------------------------------------------------------

async function pollLoop() {
  if (polling) return;
  polling = true;
  try {
    while (true) {
      const since = status && typeof status.now === "number" ? status.now : 0;
      try {
        const { data } = await daemonFetch(`/events?since=${encodeURIComponent(since)}`, { timeoutMs: 30_000 });
        if (data && data.changed && data.status && data.status.rules) {
          await applyStatus(data.status);
        } else if (!status) {
          await refreshStatus();
        }
      } catch (e) {
        markDown();
        await onDaemonMaybeDown();
        await new Promise((r) => setTimeout(r, 3000));
        await refreshStatus(); // also recovers when it comes back
      }
    }
  } finally {
    polling = false;
  }
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

function statusReply() {
  return {
    ok: true,
    daemonDown: daemonDown(),
    hasStatus: !!status,
    status,
    theme,
    receivedAt: lastOk,
  };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    await loadState();
    try {
      switch (msg && msg.type) {
        case "status": {
          if (!status || Date.now() - lastOk > 5000) await refreshStatus();
          sendResponse(statusReply());
          return;
        }
        case "theme": {
          if (!theme) await refreshTheme();
          sendResponse({ ok: true, theme });
          return;
        }
        case "open": {
          const body = { rule: msg.rule };
          if (msg.minutes) body.minutes = Number(msg.minutes);
          const { httpStatus, data } = await daemonFetch("/open", { method: "POST", body });
          await refreshStatus();
          sendResponse({ ...data, httpStatus });
          return;
        }
        case "close": {
          const { httpStatus, data } = await daemonFetch("/close", { method: "POST", body: { rule: msg.rule } });
          await refreshStatus();
          sendResponse({ ...data, httpStatus });
          return;
        }
        case "ruleFor": {
          const owner = ruleForUrl(msg.url || (sender.tab && sender.tab.url) || "");
          sendResponse({ ok: true, rule: owner, daemonDown: daemonDown() });
          return;
        }
        default:
          sendResponse({ ok: false, error: "unknown message" });
      }
    } catch (e) {
      markDown();
      await onDaemonMaybeDown();
      sendResponse({ ok: false, error: "Calma isn't running", daemonDown: daemonDown(), status, theme });
    }
  })();
  return true; // async sendResponse
});

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

let started = null;

function start() {
  if (started) return started;
  started = (async () => {
    await loadState();
    chrome.alarms.get(PING_ALARM).then((a) => {
      if (!a) chrome.alarms.create(PING_ALARM, { periodInMinutes: 0.5 });
    });
    await ping();
    await refreshStatus();
    // With the daemon unreachable and nothing cached there is nothing to block yet (the
    // Chromium policy layer still applies); otherwise make the DNR rules match the cache.
    await renderRules();
    refreshTheme();
    updateBadge();
    pollLoop();
  })();
  return started;
}

chrome.runtime.onInstalled.addListener(() => start());
chrome.runtime.onStartup.addListener(() => start());
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== PING_ALARM) return;
  await loadState();
  await ping();
  if (!polling) pollLoop();
  if (Date.now() - lastOk > 30_000) await refreshStatus();
  updateBadge();
});
chrome.webNavigation.onCommitted.addListener(onCommitted);
chrome.webNavigation.onErrorOccurred.addListener(onErrorOccurred);

// A worker woken by an event (not onStartup) still needs its state and loops.
start();
