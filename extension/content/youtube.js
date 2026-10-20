"use strict";
/*
 * Calma: the minimal YouTube.
 *
 * youtube.css hides feeds, Shorts, related videos, end screens and comments. This script:
 *  - redirects / and /shorts/* to /feed/subscriptions (also on SPA navigation);
 *  - switches the autoplay toggle off whenever it is on;
 *  - hides guide sections by title (Explore, More from YouTube) and chip-bar entries;
 *  - hides search-result shelves by title ("People also watched", "For you", …);
 *  - marks the watch page when a playlist panel is present so it stays visible.
 *
 * Idempotent, MutationObserver-driven, one pass per animation frame, independent of the
 * daemon (cosmetics stay on when Calma is unreachable).
 */

(() => {
  if (window.top !== window) return;

  const HIDE = "data-calma-hide";
  const HOME_PATH = "/feed/subscriptions";
  const GUIDE_SECTIONS = [/^explore$/i, /^more from youtube$/i, /^trending$/i];
  const SEARCH_SHELVES = [
    /^people also watched$/i,
    /^for you$/i,
    /^shorts$/i,
    /^latest from/i,
    /^previously watched$/i,
    /^from related searches$/i,
    /^channels new to you$/i,
    /^popular today$/i,
    /^explore more$/i,
  ];

  let scheduled = false;
  let lastAutonavClick = 0;
  let lastPath = null;

  const text = (el) => (el && el.textContent ? el.textContent.trim() : "");
  const hide = (el) => {
    if (el && !el.hasAttribute(HIDE)) el.setAttribute(HIDE, "");
  };

  // --- redirects --------------------------------------------------------------------

  function shouldRedirect(pathname) {
    // Only the main site: youtu.be/<id> links and music.youtube.com have their own roots.
    const host = location.hostname;
    if (!(host === "youtube.com" || host.endsWith(".youtube.com"))) return false;
    if (host === "music.youtube.com" || host === "studio.youtube.com") return false;
    if (pathname === "/" || pathname === "") return true;
    if (pathname === "/shorts" || pathname.startsWith("/shorts/")) return true;
    if (pathname === "/feed/trending" || pathname.startsWith("/feed/trending/")) return true;
    if (pathname === "/feed/explore") return true;
    if (pathname === "/gaming" || pathname.startsWith("/gaming/")) return true;
    return false;
  }

  function redirectIfNeeded() {
    if (shouldRedirect(location.pathname)) {
      location.replace(`${location.origin}${HOME_PATH}`);
      return true;
    }
    return false;
  }

  // --- autoplay off -----------------------------------------------------------------

  function autoplayOff() {
    const toggle = document.querySelector('.ytp-autonav-toggle-button[aria-checked="true"]');
    if (toggle && Date.now() - lastAutonavClick > 1000) {
      lastAutonavClick = Date.now();
      toggle.click();
    }
    // Mobile web uses a different control.
    const mobile = document.querySelector('ytm-autonav-toggle-button-renderer button[aria-pressed="true"]');
    if (mobile && Date.now() - lastAutonavClick > 1000) {
      lastAutonavClick = Date.now();
      mobile.click();
    }
  }

  // --- guide ------------------------------------------------------------------------

  function trimGuide() {
    for (const section of document.querySelectorAll("ytd-guide-section-renderer")) {
      const title = section.querySelector("#guide-section-title, h3");
      if (title && GUIDE_SECTIONS.some((re) => re.test(text(title)))) hide(section);
    }
    for (const entry of document.querySelectorAll("ytd-guide-entry-renderer, ytd-mini-guide-entry-renderer")) {
      const t = text(entry.querySelector(".title, yt-formatted-string.title, span.title")) || entry.getAttribute("aria-label") || "";
      if (/^(shorts|explore|trending|gaming|news|sport|sports|learning|fashion & beauty|podcasts|live|music|movies & tv|youtube premium|youtube music|youtube kids|youtube studio)$/i.test(t)) hide(entry);
    }
    for (const chip of document.querySelectorAll("yt-chip-cloud-chip-renderer")) {
      const t = text(chip);
      if (/^(shorts|trending|explore)$/i.test(t)) hide(chip);
    }
  }

  // --- search shelves ---------------------------------------------------------------

  function trimSearch() {
    if (!document.querySelector("ytd-search")) return;
    for (const shelf of document.querySelectorAll("ytd-search ytd-shelf-renderer, ytd-search ytd-reel-shelf-renderer, ytd-search grid-shelf-view-model")) {
      const title = shelf.querySelector("#title, h2, .yt-shelf-header-layout h2, span#title");
      if (title && SEARCH_SHELVES.some((re) => re.test(text(title)))) hide(shelf);
    }
  }

  // --- watch page -------------------------------------------------------------------

  function markPlaylist() {
    const flexy = document.querySelector("ytd-watch-flexy");
    if (!flexy) return;
    const hasList = new URLSearchParams(location.search).has("list") && !!flexy.querySelector("#secondary ytd-playlist-panel-renderer#playlist");
    if (hasList) flexy.setAttribute("data-calma-playlist", "");
    else flexy.removeAttribute("data-calma-playlist");
  }

  // --- pass -------------------------------------------------------------------------

  function pass() {
    scheduled = false;
    try {
      if (location.pathname !== lastPath) {
        lastPath = location.pathname;
        if (redirectIfNeeded()) return;
      }
      autoplayOff();
      trimGuide();
      trimSearch();
      markPlaylist();
    } catch (e) {
      /* never break the page */
    }
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(pass);
  }

  function start() {
    if (redirectIfNeeded()) return;
    schedule();
    const mo = new MutationObserver(schedule);
    mo.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-checked", "aria-pressed", "page-subtype"] });
    // YouTube's SPA router dispatches these on document (DOM events cross the isolated world).
    for (const ev of ["yt-navigate-start", "yt-navigate-finish", "yt-page-data-updated"]) {
      document.addEventListener(ev, () => {
        lastPath = null;
        schedule();
      });
    }
    window.addEventListener("popstate", () => {
      lastPath = null;
      schedule();
    });
    setInterval(schedule, 2000); // safety net (autoplay toggle re-renders, missed routes)
  }

  if (redirectIfNeeded()) return; // document_start: leave before the page loads
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
