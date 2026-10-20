"use strict";
/*
 * Calma: the minimal X (x.com / twitter.com).
 *
 * CSS (x.css) hides what selectors can reach. This script handles the rest:
 *  - on /home, keep the "Following" timeline selected and hide the "For you" tab;
 *  - hide everything in the right sidebar except the search box;
 *  - hide "Discover more" in threads (the heading cell and everything after it);
 *  - hide inline "Who to follow" / "You might like" modules by their headings;
 *  - mark people pages (followers, following, connect_people) so user cells stay visible.
 *
 * Idempotent, MutationObserver-driven, throttled to one pass per animation frame, and
 * independent of the daemon: these cosmetics stay on even if Calma is unreachable.
 */

(() => {
  if (window.top !== window) return;

  const HIDE = "data-calma-hide";
  const SUGGESTION_HEADINGS = [
    /^who to follow$/i,
    /^you might like$/i,
    /^creators to follow$/i,
    /^suggested for you$/i,
    /^people to follow$/i,
    /^more .* to follow$/i,
    /^expand your timeline/i,
    /^join a community/i,
    /^trending/i,
    /^today'?s news$/i,
    /^what'?s happening$/i,
  ];
  const DISCOVER_RE = /^discover more$/i;
  const PEOPLE_PATHS = /^\/(i\/connect_people|[^/]+\/(followers|following|verified_followers|followers_you_follow|subscriptions))(\/|$)/;

  let scheduled = false;
  let lastForYouClick = 0;

  const text = (el) => (el && el.textContent ? el.textContent.trim() : "");
  const hide = (el) => {
    if (el && !el.hasAttribute(HIDE)) el.setAttribute(HIDE, "");
  };
  const cellOf = (el) => (el ? el.closest('[data-testid="cellInnerDiv"]') : null);

  // --- home: Following, not For you ------------------------------------------------

  function forceFollowing() {
    if (location.pathname !== "/home") return;
    const tablist = document.querySelector('[data-testid="primaryColumn"] [role="tablist"]');
    if (!tablist) return;
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    if (tabs.length < 2) return;
    const forYou = tabs.find((t) => /^for you$/i.test(text(t))) || tabs[0];
    const following = tabs.find((t) => /^following$/i.test(text(t))) || tabs[1];
    if (!forYou.hasAttribute("data-calma-foryou")) forYou.setAttribute("data-calma-foryou", "");
    const selected = forYou.getAttribute("aria-selected") === "true";
    if (selected && following && Date.now() - lastForYouClick > 1500) {
      lastForYouClick = Date.now();
      following.click();
    }
  }

  // --- right sidebar: keep only search ---------------------------------------------

  function trimSidebar() {
    const sidebar = document.querySelector('[data-testid="sidebarColumn"]');
    if (!sidebar) return;
    const search = sidebar.querySelector('form[role="search"], [data-testid="SearchBox_Search_Input"]');
    if (!search) {
      // No search box here (e.g. on /search itself the box is in the header): hide it all.
      for (const child of sidebar.children) hide(child);
      return;
    }
    // Everything that isn't on the path from the sidebar to the search box is hidden.
    let node = search;
    while (node && node !== sidebar) {
      const parent = node.parentElement;
      if (!parent) break;
      for (const sibling of parent.children) {
        if (sibling !== node) hide(sibling);
      }
      node = parent;
    }
  }

  // --- threads: Discover more; timelines: inline suggestions -----------------------

  function translateY(cell) {
    const m = /translateY\((-?[\d.]+)px\)/.exec(cell && cell.style ? cell.style.transform : "");
    return m ? parseFloat(m[1]) : null;
  }

  function hideDiscoverMore(column) {
    const headings = column.querySelectorAll("h2");
    for (const h of headings) {
      if (!DISCOVER_RE.test(text(h))) continue;
      const cell = cellOf(h);
      if (!cell) {
        hide(h.closest("section, div"));
        continue;
      }
      hide(cell);
      const y = translateY(cell);
      const siblings = cell.parentElement ? [...cell.parentElement.children] : [];
      if (y !== null) {
        // Virtualised list: DOM order is not visual order, use the transform.
        for (const s of siblings) {
          const sy = translateY(s);
          if (sy !== null && sy > y) hide(s);
        }
      } else {
        let after = false;
        for (const s of siblings) {
          if (s === cell) after = true;
          else if (after) hide(s);
        }
      }
    }
  }

  function hideSuggestionModules(column) {
    const headings = column.querySelectorAll('h2, [role="heading"]');
    for (const h of headings) {
      const t = text(h);
      if (!t || !SUGGESTION_HEADINGS.some((re) => re.test(t))) continue;
      const cell = cellOf(h);
      if (!cell) continue;
      hide(cell);
      // The module's items follow its heading as separate cells until a "Show more" link.
      let next = cell.nextElementSibling;
      let hops = 0;
      while (next && hops < 8) {
        const isUser = next.querySelector('[data-testid="UserCell"], a[href^="/i/connect_people"]');
        const isTweet = next.querySelector('article[data-testid="tweet"]');
        if (isTweet && !isUser) break;
        if (isUser) hide(next);
        else break;
        next = next.nextElementSibling;
        hops += 1;
      }
    }
  }

  function markPeoplePage() {
    const isPeople = PEOPLE_PATHS.test(location.pathname);
    if (isPeople) document.body && document.body.setAttribute("data-calma-people", "");
    else document.body && document.body.removeAttribute("data-calma-people");
  }

  // --- pass -------------------------------------------------------------------------

  function pass() {
    scheduled = false;
    try {
      markPeoplePage();
      forceFollowing();
      trimSidebar();
      const column = document.querySelector('[data-testid="primaryColumn"]');
      if (column) {
        hideDiscoverMore(column);
        hideSuggestionModules(column);
      }
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
    schedule();
    const mo = new MutationObserver(schedule);
    mo.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-selected", "style"] });
    // SPA navigation happens via history.pushState in the page's world, which a content
    // script can't hook; the observer and the interval cover route changes.
    window.addEventListener("popstate", schedule);
    setInterval(schedule, 1500); // safety net for renders the observer misses
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
