// Unit tests for Model.js (repo root). Run: node --test dev/plugin/
import test from "node:test"
import assert from "node:assert/strict"
import { createRequire } from "node:module"

const require = createRequire(import.meta.url)
const Model = require("../../Model.js")

const NOW = 1760969900.0

function fixture() {
  return {
    version: "0.1.0",
    now: NOW,
    problems: [],
    rules: {
      steam: {
        label: "Steam", glyph: "", sessions_per_day: 1, session_minutes: 60, daily_minutes: 60,
        sessions_used: 1, seconds_used: 3600, sessions_left: 0, seconds_left: 0,
        state: "blocked", session: null, can_open: false,
        cannot_open_reason: "no sessions left today", next_opportunity: 1761015600.0
      },
      youtube: {
        label: "YouTube", glyph: "", sessions_per_day: 1, session_minutes: 20, daily_minutes: 20,
        sessions_used: 0, seconds_used: 0, sessions_left: 1, seconds_left: 1200,
        state: "blocked", session: null, can_open: true, cannot_open_reason: null, next_opportunity: null
      },
      x: {
        label: "X", glyph: "𝕏", sessions_per_day: 2, session_minutes: 5, daily_minutes: 10,
        sessions_used: 1, seconds_used: 142, sessions_left: 1, seconds_left: 458,
        state: "open", session: { ends_at: NOW + 258, seconds_left: 258, minutes: 5 },
        can_open: false, cannot_open_reason: "already open", next_opportunity: null
      }
    }
  }
}

const hhmm = date => `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`

test("formatCountdown renders m:ss and clamps at zero", () => {
  assert.equal(Model.formatCountdown(258), "4:18")
  assert.equal(Model.formatCountdown(0), "0:00")
  assert.equal(Model.formatCountdown(-4), "0:00")
  assert.equal(Model.formatCountdown(4500), "75:00")
  assert.equal(Model.formatCountdown("x"), "0:00")
})

test("parseStatus rejects garbage and tolerates a missing rules map", () => {
  assert.equal(Model.parseStatus("nope").ok, false)
  assert.equal(Model.parseStatus("[]").ok, true)
  assert.deepEqual(Model.parseStatus("{}").status.rules, {})
})

test("ruleList orders open sessions, then openable, then the rest", () => {
  const rules = Model.ruleList(fixture())
  assert.deepEqual(rules.map(r => r.name), ["x", "youtube", "steam"])
  assert.equal(rules[0].label, "X")
})

test("barText shows the soonest session's glyph and countdown, else the mark", () => {
  const rules = Model.ruleList(fixture())
  assert.equal(Model.barText(rules, NOW * 1000), "𝕏 4:18")
  assert.equal(Model.barText(rules, (NOW + 100) * 1000), "𝕏 2:38")
  assert.equal(Model.barText([], NOW * 1000), Model.GLYPH_CALMA)
  const idle = fixture()
  idle.rules.x.state = "blocked"
  idle.rules.x.session = null
  assert.equal(Model.barText(Model.ruleList(idle), NOW * 1000), Model.GLYPH_CALMA)
})

test("barTooltip names the open sessions or says the daemon is down", () => {
  const rules = Model.ruleList(fixture())
  assert.equal(Model.barTooltip(rules, NOW * 1000, true), "Calma · X · 4:18 left")
  assert.equal(Model.barTooltip(rules, NOW * 1000, false), "Calma isn't running")
  assert.equal(Model.barTooltip([], NOW * 1000, true), "Calma · nothing open")
})

test("ruleMeta describes each state", () => {
  const f = fixture()
  assert.equal(Model.ruleMeta({ ...f.rules.x, name: "x" }, NOW * 1000), "4:18 left · 1/2 · 8 min left")
  assert.equal(Model.ruleMeta(f.rules.youtube, NOW * 1000), "Blocked · 0/1 · 20 min left")
  assert.equal(Model.ruleMeta(f.rules.steam, NOW * 1000, hhmm), "No sessions left · back at 03:00")
  assert.equal(Model.ruleMeta({ ...f.rules.steam, cannot_open_reason: "cooling down", next_opportunity: NOW + 600 }, NOW * 1000, hhmm), "Cooling down until 14:28")
  assert.equal(Model.ruleMeta({ ...f.rules.steam, cannot_open_reason: "always blocked", sessions_per_day: 0 }, NOW * 1000), "Always blocked")
  assert.equal(Model.ruleMeta({ ...f.rules.steam, cannot_open_reason: "no time left today" }, NOW * 1000, hhmm), "No time left · back at 03:00")
})

test("actionLabel offers Start with the budget-limited minutes, End now when open", () => {
  const f = fixture()
  assert.equal(Model.actionLabel(f.rules.x), "End now")
  assert.equal(Model.actionLabel(f.rules.youtube), "Start 20 min")
  assert.equal(Model.actionLabel({ ...f.rules.youtube, seconds_left: 170 }), "Start 3 min")
  assert.equal(Model.actionLabel(f.rules.steam), "")
  assert.equal(Model.startMinutes({ ...f.rules.youtube, seconds_left: 170 }), 3)
})

test("heroMeta summarises the day", () => {
  const f = fixture()
  const rules = Model.ruleList(f)
  assert.equal(Model.heroMeta(f, rules, true, ""), "3 rules · 1 open")
  assert.equal(Model.heroMeta({ ...f, problems: ["policy file missing"] }, rules, true, ""), "3 rules · 1 open · 1 problem")
  assert.equal(Model.heroMeta(f, [], true, ""), "No rules yet · calma add")
  assert.equal(Model.heroMeta(null, [], false, ""), "Calma isn't running")
  assert.equal(Model.heroMeta(null, [], false, "boom"), "boom")
  assert.equal(Model.heroMeta(null, [], false, "boom", Model.SETUP_MISSING), "Calma isn't set up on this machine")
  assert.equal(Model.heroMeta(null, [], false, "boom", Model.SETUP_STOPPED), "Calma isn't running")
})

test("setupState tells running, stopped, missing and unknown apart", () => {
  assert.equal(Model.setupState(true, null), Model.SETUP_RUNNING)
  assert.equal(Model.setupState(true, false), Model.SETUP_RUNNING)
  assert.equal(Model.setupState(false, true), Model.SETUP_STOPPED)
  assert.equal(Model.setupState(false, false), Model.SETUP_MISSING)
  assert.equal(Model.setupState(false, null), Model.SETUP_UNKNOWN)
  assert.equal(Model.setupText(Model.SETUP_UNKNOWN), "")
  assert.equal(Model.barTooltip([], NOW * 1000, false, Model.SETUP_MISSING), "Calma isn't set up on this machine")
})

test("setupCommand hands SETUP.md in the plugin dir to the agent, shell-quoted", () => {
  assert.equal(Model.pathFromUrl("file:///home/j/.config/omarchy/plugins/io.github.joanreyero.calma/"), "/home/j/.config/omarchy/plugins/io.github.joanreyero.calma")
  assert.equal(Model.pathFromUrl("file:///home/j/My%20Dir/"), "/home/j/My Dir")
  const cmd = Model.setupCommand("/home/j/it's here")
  assert.ok(cmd.startsWith("omarchy agent prompt '"))
  assert.ok(cmd.includes("Read /home/j/it'\\''s here/SETUP.md first"))
  assert.ok(cmd.includes("run the install yourself with pkexec"))
  assert.ok(cmd.endsWith("rules.'"))
  assert.equal(Model.shellQuote("a'b"), "'a'\\''b'")
})

test("withRule replaces one rule without mutating the original", () => {
  const f = fixture()
  const next = Model.withRule(f, "x", { ...f.rules.x, state: "blocked", session: null })
  assert.equal(f.rules.x.state, "open")
  assert.equal(next.rules.x.state, "blocked")
  assert.equal(next.rules.youtube, f.rules.youtube)
})

test("responseError prefers the daemon's message", () => {
  assert.equal(Model.responseError(409, '{"ok":false,"error":"no sessions left today"}', "x"), "no sessions left today")
  assert.equal(Model.responseError(0, "", "Could not start"), "Could not start")
  assert.equal(Model.responseError(500, "oops", "Could not start"), "Could not start (500)")
})

test("baseUrl and eventsUrl", () => {
  assert.equal(Model.baseUrl(undefined), "http://127.0.0.1:7390")
  assert.equal(Model.baseUrl("7391"), "http://127.0.0.1:7391")
  assert.equal(Model.baseUrl(99999), "http://127.0.0.1:7390")
  assert.equal(Model.eventsUrl("http://h", 12.5), "http://h/events?since=12.5")
  assert.equal(Model.eventsUrl("http://h", null), "http://h/events?since=0")
})
