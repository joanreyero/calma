/* calma.sh: the blocked-page demo in the hero, and copy buttons. No dependencies. */
(function () {
  "use strict";

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- hero demo: blocked → open → spent ---------- */
  var $ = function (id) { return document.getElementById(id); };
  var el = {
    frame: $("frame"),
    url: $("frame-url"),
    blocked: $("blocked"),
    open: $("open"),
    message: $("message"),
    budget: $("budget"),
    action: $("action"),
    note: $("note"),
    start: $("start"),
    end: $("end"),
    time: $("pill-time"),
    steps: $("demo-steps")
  };

  if (!el.frame) return;

  var DWELL = { blocked: 5200, open: 7000, spent: 6000 };
  var timer = null;
  var ticker = null;
  var current = "blocked";

  function later(fn, ms) { clearTimeout(timer); timer = setTimeout(fn, ms); }

  function setBudget(parts) {
    el.budget.textContent = "";
    parts.forEach(function (p, i) {
      var s = document.createElement("span");
      s.textContent = (i ? "\u00b7\u00a0" : "") + p;
      el.budget.appendChild(s);
      el.budget.appendChild(document.createTextNode(" "));
    });
  }

  function setStep(i) {
    var bars = el.steps.children;
    for (var k = 0; k < bars.length; k++) bars[k].className = k === i ? "on" : "";
  }

  function restartRise(node) {
    // Replay the card's entrance so each state reads as a fresh page.
    if (reduced) return;
    node.style.animation = "none";
    void node.offsetWidth;
    node.style.animation = "";
  }

  function stopTicker() { clearInterval(ticker); ticker = null; }

  function showBlocked() {
    current = "blocked";
    stopTicker();
    el.url.textContent = "x.com/home";
    el.open.hidden = true;
    el.blocked.hidden = false;
    el.message.textContent = "X is resting. Start a session when you're ready.";
    setBudget(["Sessions today: 1 of 2", "5 min each", "5 min left"]);
    el.action.hidden = false;
    el.note.hidden = true;
    el.frame.dataset.state = "blocked";
    restartRise(el.blocked.querySelector(".card"));
    setStep(0);
    later(showOpen, DWELL.blocked);
  }

  function showOpen() {
    current = "open";
    el.url.textContent = "x.com/home";
    el.blocked.hidden = true;
    el.open.hidden = false;
    el.frame.dataset.state = "open";
    restartRise(el.open.querySelector(".pill"));
    setStep(1);
    var seconds = 4 * 60 + 32;
    var paint = function () {
      var m = Math.floor(seconds / 60);
      var s = seconds % 60;
      el.time.textContent = m + ":" + (s < 10 ? "0" : "") + s;
    };
    paint();
    stopTicker();
    ticker = setInterval(function () {
      seconds = Math.max(0, seconds - 1);
      paint();
    }, 1000);
    later(showSpent, DWELL.open);
  }

  function showSpent() {
    current = "spent";
    stopTicker();
    el.url.textContent = "x.com/home";
    el.open.hidden = true;
    el.blocked.hidden = false;
    el.message.textContent = "That's X for today. The rest of the day is yours.";
    setBudget(["Sessions today: 2 of 2", "5 min each"]);
    el.action.hidden = true;
    el.note.hidden = false;
    el.frame.dataset.state = "spent";
    restartRise(el.blocked.querySelector(".card"));
    setStep(2);
    later(showBlocked, DWELL.spent);
  }

  // The buttons do what they say, within the demo.
  el.start.addEventListener("click", showOpen);
  el.end.addEventListener("click", showSpent);

  var paused = false;
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      clearTimeout(timer);
      stopTicker();
      paused = true;
    } else if (paused) {
      paused = false;
      showBlocked();
    }
  });

  showBlocked();

  /* ---------- copy buttons ---------- */
  function textOf(node) {
    // Strip "$ " prompts and trailing comments so the clipboard gets runnable lines.
    return node.textContent.split("\n").map(function (l) {
      return l.replace(/^\$\s+/, "").replace(/\s+#.*$/, "").replace(/\s+$/, "");
    }).filter(function (l) { return l.length > 0; }).join("\n");
  }
  document.querySelectorAll("[data-copy-target]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = document.getElementById(btn.getAttribute("data-copy-target"));
      if (!src) return;
      var text = textOf(src);
      var done = function () {
        var old = btn.textContent;
        btn.textContent = "Copied";
        btn.classList.add("ok");
        setTimeout(function () { btn.textContent = old; btn.classList.remove("ok"); }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, done);
      } else {
        var ta = document.createElement("textarea");
        ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); } catch (e) { /* ignore */ }
        document.body.removeChild(ta);
        done();
      }
    });
  });
})();
