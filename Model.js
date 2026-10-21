// Pure formatting and parsing for the Calma bar plugin. No QML types here so
// the same file runs under node for tests (see dev/model.test.mjs) and inside
// the shell's QML engine.

var STATE_OPEN = "open"
var STATE_BLOCKED = "blocked"

// nf-md-leaf: the mark the bar shows when no rule has a glyph of its own.
var GLYPH_CALMA = "\u{F032A}"

var defaultPort = 7390

function baseUrl(port) {
  var n = parseInt(port, 10)
  if (!isFinite(n) || n < 1 || n > 65535) n = defaultPort
  return "http://127.0.0.1:" + n
}

function parseJson(text) {
  try {
    var value = JSON.parse(String(text || ""))
    if (value === null || typeof value !== "object") return { ok: false, error: "not an object" }
    return { ok: true, value: value }
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) }
  }
}

// /status → { ok, status } with every rule tagged with its name and the rule
// map turned into a stable list: open sessions first (soonest to end first),
// then rules that can be opened, then the rest, alphabetically within a tier.
function parseStatus(text) {
  var result = parseJson(text)
  if (!result.ok) return { ok: false, error: "Could not read the daemon's status: " + result.error }
  var status = result.value
  if (!status.rules || typeof status.rules !== "object") status.rules = {}
  return { ok: true, status: status }
}

function ruleList(status) {
  if (!status || !status.rules) return []
  var out = []
  for (var name in status.rules) {
    var rule = status.rules[name]
    if (!rule || typeof rule !== "object") continue
    var copy = {}
    for (var key in rule) copy[key] = rule[key]
    copy.name = name
    out.push(copy)
  }
  out.sort(compareRules)
  return out
}

function tier(rule) {
  if (rule.state === STATE_OPEN) return 0
  if (rule.can_open === true) return 1
  return 2
}

function compareRules(a, b) {
  var ta = tier(a), tb = tier(b)
  if (ta !== tb) return ta - tb
  if (ta === 0) {
    var ea = a.session && a.session.ends_at ? a.session.ends_at : 0
    var eb = b.session && b.session.ends_at ? b.session.ends_at : 0
    if (ea !== eb) return ea - eb
  }
  var la = String(a.label || a.name).toLowerCase()
  var lb = String(b.label || b.name).toLowerCase()
  return la < lb ? -1 : la > lb ? 1 : 0
}

// Replace one rule in a status object (after /open or /close) and return a
// new object so QML property bindings see the change.
function withRule(status, name, rule) {
  var next = {}
  for (var key in status) next[key] = status[key]
  next.rules = {}
  for (var r in status.rules) next.rules[r] = status.rules[r]
  if (rule) next.rules[name] = rule
  return next
}

function openRules(rules) {
  var out = []
  for (var i = 0; i < rules.length; i++) if (rules[i].state === STATE_OPEN) out.push(rules[i])
  return out
}

// The session that ends soonest, or null.
function soonestSession(rules) {
  var best = null
  for (var i = 0; i < rules.length; i++) {
    var rule = rules[i]
    if (rule.state !== STATE_OPEN || !rule.session) continue
    if (!best || rule.session.ends_at < best.session.ends_at) best = rule
  }
  return best
}

// Seconds left in a session at wall time nowMs. The daemon reports ends_at in
// unix seconds; the plugin ticks locally between polls rather than trusting
// the seconds_left snapshot, which is stale within a second.
function sessionSecondsLeft(rule, nowMs) {
  if (!rule || !rule.session) return 0
  var ends = Number(rule.session.ends_at)
  if (!isFinite(ends)) return Math.max(0, Math.round(Number(rule.session.seconds_left) || 0))
  return Math.max(0, Math.round(ends - nowMs / 1000))
}

// m:ss, never negative; hours fold into the minutes ("75:00").
function formatCountdown(seconds) {
  var s = Math.max(0, Math.round(Number(seconds) || 0))
  var m = Math.floor(s / 60)
  var r = s % 60
  return m + ":" + (r < 10 ? "0" : "") + r
}

function glyphFor(rule) {
  if (rule && typeof rule.glyph === "string" && rule.glyph !== "") return rule.glyph
  return GLYPH_CALMA
}

function labelFor(rule) {
  if (!rule) return ""
  return String(rule.label || rule.name || "")
}

// The bar label: "<glyph> m:ss" for the soonest-ending session, else the
// Calma mark alone (shown only when the widget is not hidden when idle).
function barText(rules, nowMs) {
  var rule = soonestSession(rules)
  if (!rule) return GLYPH_CALMA
  return glyphFor(rule) + " " + formatCountdown(sessionSecondsLeft(rule, nowMs))
}

// The three situations the widget tells apart, from whether the daemon
// answers and whether the CLI or /etc/calma exists (null = not probed yet).
var SETUP_RUNNING = "running"
var SETUP_STOPPED = "stopped"
var SETUP_MISSING = "missing"
var SETUP_UNKNOWN = "unknown"

function setupState(reachable, installed) {
  if (reachable) return SETUP_RUNNING
  if (installed === false) return SETUP_MISSING
  if (installed === true) return SETUP_STOPPED
  return SETUP_UNKNOWN
}

function setupText(state) {
  if (state === SETUP_MISSING) return "Calma isn't set up on this machine"
  if (state === SETUP_STOPPED) return "Calma isn't running"
  return ""
}

function setupHint(state) {
  if (state === SETUP_MISSING) return "Install it with the agent, or follow SETUP.md in the plugin directory by hand."
  if (state === SETUP_STOPPED) return "calma doctor in a terminal says which layer is down."
  return ""
}

// What the Set up button hands to `omarchy agent prompt`; pluginDir is the
// checkout the shell loaded this plugin from, where SETUP.md lives.
function setupPrompt(pluginDir) {
  var dir = String(pluginDir || "").replace(/\/$/, "") || "~/.config/omarchy/plugins/io.github.joanreyero.calma"
  return "Set up Calma, the distraction blocker, on this machine. Read " + dir + "/SETUP.md first and follow it step by step with me: "
    + "install it with sudo ./install.sh from that directory (I run the sudo lines in a terminal), "
    + "talk me through the password choice, then turn what I want blocked into rules."
}

function shellQuote(value) {
  return "'" + String(value || "").replace(/'/g, "'\\''") + "'"
}

function setupCommand(pluginDir) {
  return "omarchy agent prompt " + shellQuote(setupPrompt(pluginDir))
}

// A local path from the file URL Qt.resolvedUrl gives, without the trailing slash.
function pathFromUrl(url) {
  var s = String(url || "")
  if (s.indexOf("file://") === 0) s = s.substring(7)
  try { s = decodeURIComponent(s) } catch (e) {}
  return s.replace(/\/$/, "")
}

function barTooltip(rules, nowMs, reachable, state) {
  if (!reachable) return setupText(state) || "Calma isn't running"
  var open = openRules(rules)
  if (open.length === 0) return "Calma · nothing open"
  var parts = []
  for (var i = 0; i < open.length; i++) {
    parts.push(labelFor(open[i]) + " · " + formatCountdown(sessionSecondsLeft(open[i], nowMs)) + " left")
  }
  return "Calma · " + parts.join(" · ")
}

function minutesLeft(rule) {
  var seconds = Number(rule && rule.seconds_left)
  if (!isFinite(seconds)) return 0
  return Math.max(0, Math.ceil(seconds / 60))
}

function plural(n, word) {
  return n + " " + word + (n === 1 ? "" : "s")
}

// "1/2 · 7 min left" — sessions used over allowed, then the daily minutes.
function budgetText(rule) {
  if (!rule) return ""
  var used = Number(rule.sessions_used) || 0
  var perDay = Number(rule.sessions_per_day) || 0
  var text = used + "/" + perDay
  if (perDay === 0) return "always blocked"
  return text + " · " + minutesLeft(rule) + " min left"
}

// HH:mm of a unix timestamp in local time, via an injected formatter so the
// QML side can use Qt.formatTime and node tests can use a fixed zone.
function clockText(unixSeconds, formatTime) {
  var n = Number(unixSeconds)
  if (!isFinite(n) || n <= 0) return ""
  var date = new Date(n * 1000)
  if (typeof formatTime === "function") return formatTime(date)
  var h = date.getHours(), m = date.getMinutes()
  return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m
}

// The second line of a rule row.
function ruleMeta(rule, nowMs, formatTime) {
  if (!rule) return ""
  if (rule.state === STATE_OPEN) {
    // The accent colour and the End button already say "open"; the row's
    // width goes to the numbers.
    return formatCountdown(sessionSecondsLeft(rule, nowMs)) + " left · " + budgetText(rule)
  }
  if (rule.can_open === true) return "Blocked · " + budgetText(rule)
  var reason = String(rule.cannot_open_reason || "")
  var when = clockText(rule.next_opportunity, formatTime)
  if (reason === "always blocked") return "Always blocked"
  if (reason === "cooling down") return when ? "Cooling down until " + when : "Cooling down"
  if (reason === "no sessions left today" || reason === "no time left today") {
    return (reason === "no sessions left today" ? "No sessions left" : "No time left") + (when ? " · back at " + when : "")
  }
  if (reason === "already open") return "Open"
  return reason ? "Blocked · " + reason : "Blocked · " + budgetText(rule)
}

// Minutes a Start button would ask for: the rule's session length, or what
// is left of the day's budget when that is shorter.
function startMinutes(rule) {
  if (!rule) return 0
  var session = Number(rule.session_minutes) || 0
  var left = minutesLeft(rule)
  if (left <= 0) return session
  return Math.max(1, Math.min(session, left))
}

function actionLabel(rule) {
  if (!rule) return ""
  if (rule.state === STATE_OPEN) return "End now"
  if (rule.can_open !== true) return ""
  return "Start " + startMinutes(rule) + " min"
}

function heroMeta(status, rules, reachable, error, state) {
  if (!reachable) return setupText(state) || error || "Calma isn't running"
  if (rules.length === 0) return "No rules yet · calma add"
  var open = openRules(rules).length
  var text = plural(rules.length, "rule")
  if (open > 0) text += " · " + open + " open"
  else text += " · all blocked"
  if (status && Array.isArray(status.problems) && status.problems.length > 0) {
    text += " · " + plural(status.problems.length, "problem")
  }
  return text
}

function problems(status) {
  if (!status || !Array.isArray(status.problems)) return []
  var out = []
  for (var i = 0; i < status.problems.length; i++) out.push(String(status.problems[i]))
  return out
}

// A short error line from an HTTP response, for the panel.
function responseError(statusCode, text, fallback) {
  var parsed = parseJson(text)
  if (parsed.ok && typeof parsed.value.error === "string" && parsed.value.error !== "") return parsed.value.error
  if (statusCode === 0) return fallback || "Calma isn't running"
  return (fallback || "Request failed") + " (" + statusCode + ")"
}

function eventsUrl(base, since) {
  var n = Number(since)
  return base + "/events?since=" + (isFinite(n) && n > 0 ? String(n) : "0")
}

if (typeof module !== "undefined") {
  module.exports = {
    STATE_OPEN: STATE_OPEN,
    STATE_BLOCKED: STATE_BLOCKED,
    SETUP_RUNNING: SETUP_RUNNING,
    SETUP_STOPPED: SETUP_STOPPED,
    SETUP_MISSING: SETUP_MISSING,
    SETUP_UNKNOWN: SETUP_UNKNOWN,
    setupState: setupState,
    setupText: setupText,
    setupHint: setupHint,
    setupPrompt: setupPrompt,
    setupCommand: setupCommand,
    shellQuote: shellQuote,
    pathFromUrl: pathFromUrl,
    GLYPH_CALMA: GLYPH_CALMA,
    defaultPort: defaultPort,
    baseUrl: baseUrl,
    parseJson: parseJson,
    parseStatus: parseStatus,
    ruleList: ruleList,
    withRule: withRule,
    openRules: openRules,
    soonestSession: soonestSession,
    sessionSecondsLeft: sessionSecondsLeft,
    formatCountdown: formatCountdown,
    glyphFor: glyphFor,
    labelFor: labelFor,
    barText: barText,
    barTooltip: barTooltip,
    minutesLeft: minutesLeft,
    budgetText: budgetText,
    clockText: clockText,
    ruleMeta: ruleMeta,
    startMinutes: startMinutes,
    actionLabel: actionLabel,
    heroMeta: heroMeta,
    problems: problems,
    responseError: responseError,
    eventsUrl: eventsUrl
  }
}
