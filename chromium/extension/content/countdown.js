"use strict";
/*
 * Calma countdown pill. Injected on every page; does nothing unless the hostname belongs
 * to a rule. Asks the service worker for status every 2 s (which also keeps the worker
 * alive while a session runs), shows "Calma · m:ss" bottom-right with an "End now" link,
 * and sends the tab to the blocked page the moment the rule is blocked.
 */

(() => {
  if (window.top !== window) return; // top frame only
  if (!/^https?:$/.test(location.protocol)) return;
  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) return;

  const POLL_MS = 2000;
  const BLOCKED_PAGE = chrome.runtime.getURL("blocked.html");
  let ruleName = null;
  let pill = null;
  let timeEl = null;
  let endsAt = null; // ms on our clock
  let tick = null;
  let stopped = false;

  function hostMatches(host, domain) {
    host = host.toLowerCase();
    domain = domain.toLowerCase();
    return host === domain || host.endsWith("." + domain);
  }

  function ruleFor(status) {
    if (!status || !status.rules) return null;
    for (const [name, rule] of Object.entries(status.rules)) {
      for (const d of rule.domains || []) if (hostMatches(location.hostname, d)) return name;
    }
    return null;
  }

  function goBlocked(name) {
    stopped = true;
    try {
      location.replace(`${BLOCKED_PAGE}?rule=${encodeURIComponent(name)}&url=${encodeURIComponent(location.href)}`);
    } catch (e) {
      /* ignore */
    }
  }

  function mmss(sec) {
    sec = Math.max(0, Math.round(sec));
    return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  }

  function ensurePill() {
    if (pill && pill.isConnected) return pill;
    if (!document.documentElement) return null;
    pill = document.createElement("div");
    pill.id = "calma-countdown";
    pill.setAttribute("role", "status");
    pill.setAttribute("aria-live", "off");
    const brand = document.createElement("span");
    brand.className = "calma-brand";
    brand.textContent = "Calma";
    const dot = document.createElement("span");
    dot.className = "calma-dot";
    dot.textContent = "·";
    timeEl = document.createElement("span");
    timeEl.className = "calma-time";
    timeEl.textContent = "–:––";
    const end = document.createElement("button");
    end.type = "button";
    end.className = "calma-end";
    end.textContent = "End now";
    end.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!ruleName) return;
      end.disabled = true;
      end.textContent = "Ending";
      try {
        await chrome.runtime.sendMessage({ type: "close", rule: ruleName });
      } catch (e) {
        /* the next poll sorts it out */
      }
      poll();
    });
    pill.append(brand, dot, timeEl, end);
    (document.body || document.documentElement).appendChild(pill);
    return pill;
  }

  function removePill() {
    if (pill) pill.remove();
    pill = null;
    timeEl = null;
    if (tick) clearInterval(tick);
    tick = null;
  }

  function paint() {
    if (!timeEl || endsAt === null) return;
    const left = (endsAt - Date.now()) / 1000;
    timeEl.textContent = mmss(left);
    pill.classList.toggle("calma-urgent", left < 60);
    if (left <= 0 && ruleName) {
      // The daemon ends it on its own tick; don't wait for the poll.
      goBlocked(ruleName);
    }
  }

  function applyTheme(theme) {
    if (!pill || !theme || !theme.colors) return;
    const c = theme.colors;
    if (c.accent) pill.style.setProperty("--calma-accent", c.accent);
    if (c.background) pill.style.setProperty("--calma-bg", c.background);
    if (c.bright_foreground || c.foreground) pill.style.setProperty("--calma-fg", c.bright_foreground || c.foreground);
    if (c.red) pill.style.setProperty("--calma-red", c.red);
  }

  async function poll() {
    if (stopped) return;
    let reply;
    try {
      reply = await chrome.runtime.sendMessage({ type: "status" });
    } catch (e) {
      // Extension reloaded or worker unavailable: nothing we can do from here. The DNR
      // rules stay in force; stop polling to avoid console noise.
      if (String(e && e.message).includes("context invalidated")) stopped = true;
      return;
    }
    if (!reply) return;
    const status = reply.status;
    ruleName = ruleFor(status);
    if (!ruleName) {
      removePill();
      return;
    }
    const rule = status.rules[ruleName];
    if (reply.daemonDown || rule.state !== "open") {
      goBlocked(ruleName);
      return;
    }
    if (rule.session && typeof rule.session.ends_at === "number") {
      // Same machine, same clock: ends_at is a unix timestamp we can use directly.
      endsAt = rule.session.ends_at * 1000;
    } else if (rule.session && typeof rule.session.seconds_left === "number") {
      const receivedMs = status._receivedAt ? status._receivedAt * 1000 : Date.now();
      endsAt = receivedMs + rule.session.seconds_left * 1000;
    }
    if (ensurePill()) {
      applyTheme(reply.theme);
      paint();
      if (!tick) tick = setInterval(paint, 1000);
    }
  }

  function start() {
    poll();
    setInterval(poll, POLL_MS);
    // SPA sites re-render <body>; make sure the pill comes back.
    const mo = new MutationObserver(() => {
      if (ruleName && pill && !pill.isConnected) ensurePill();
    });
    mo.observe(document.documentElement, { childList: true, subtree: false });
    if (document.body) mo.observe(document.body, { childList: true, subtree: false });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
