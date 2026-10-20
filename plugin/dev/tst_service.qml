import QtQuick
import QtTest
import ".."
import "../Model.js" as Model
import "TestPort.js" as TestPort

// Drives Service.qml against dev/mock-daemon.py (started by dev/run on the
// port in TestPort.js) with the real XMLHttpRequest, so the poll, the long-poll
// and the POSTs with the X-Calma header are exercised end to end, headless.
TestCase {
  name: "CalmaService"
  when: windowShown

  readonly property int port: TestPort.port

  property var service: null

  Component {
    id: serviceComponent
    Service {}
  }

  function init() {
    service = serviceComponent.createObject(this, { settings: { port: port } })
    verify(service !== null)
  }

  function cleanup() {
    service.active = false
    service.destroy()
    service = null
  }

  function waitUntil(predicate, timeoutMs) {
    var deadline = Date.now() + (timeoutMs || 3000)
    while (!predicate() && Date.now() < deadline) wait(20)
    verify(predicate(), "condition met before timeout")
  }

  // Runs first (QtTest orders test functions by name) while the fixture is
  // pristine: x mid-session, youtube openable, steam spent.
  function test_0_polls_status() {
    compare(service.baseUrl, "http://127.0.0.1:" + port)
    waitUntil(function() { return service.status !== null })
    verify(service.reachable)
    compare(service.lastError, "")
    verify(service.rules.length >= 3)
    compare(service.rules[0].name, "x")
    verify(service.hasOpenSession)
    verify(service.barText.indexOf("𝕏 ") === 0)
    verify(/^\d+:\d\d$/.test(service.barText.split(" ")[1]))
  }

  function test_close_and_open_round_trip() {
    waitUntil(function() { return service.status !== null })
    service.close("x")
    waitUntil(function() { return service.rule("x") && service.rule("x").state === "blocked" })
    waitUntil(function() { return service.actionStatus !== "" })
    compare(service.actionStatus, "X · session over")
    verify(!service.hasOpenSession || Model.openRules(service.rules).length === 0)
    compare(service.barText, Model.GLYPH_CALMA)

    service.open("x", 2)
    waitUntil(function() { return service.rule("x") && service.rule("x").state === "open" })
    compare(service.rule("x").session.minutes, 2)
    waitUntil(function() { return service.actionStatus.indexOf("2 min session started") !== -1 })
    verify(service.secondsLeft(service.rule("x")) > 100)
  }

  function test_open_refused_reports_the_daemon_reason() {
    waitUntil(function() { return service.status !== null })
    service.open("steam", 0)
    waitUntil(function() { return service.actionStatus !== "" })
    compare(service.actionStatus, "no sessions left today")
    compare(service.rule("steam").state, "blocked")
  }

  function test_unknown_rule() {
    waitUntil(function() { return service.status !== null })
    service.open("nope", 0)
    waitUntil(function() { return service.actionStatus !== "" })
    compare(service.actionStatus, "unknown rule")
  }

  function test_events_long_poll_sees_other_clients_changes() {
    waitUntil(function() { return service.status !== null })
    // A second client ends x; this one must learn it through /events well
    // before its own 5 s poll would.
    var other = serviceComponent.createObject(this, { settings: { port: port } })
    waitUntil(function() { return other.status !== null })
    var openBefore = service.rule("x").state === "open"
    if (openBefore) other.close("x")
    else other.open("x", 1)
    var target = openBefore ? "blocked" : "open"
    waitUntil(function() { return service.rule("x").state === target }, 1500)
    other.active = false
    other.destroy()
  }

  // The shell destroys the service on plugin hot-reload; destroying it with
  // a /status and a long-poll in flight must abort them quietly.
  function test_destroy_mid_request_is_silent() {
    failOnWarning(/.*/)
    var doomed = serviceComponent.createObject(this, { settings: { port: port } })
    waitUntil(function() { return doomed.status !== null })
    doomed.refresh()
    verify(doomed.refreshing)
    doomed.destroy()
    wait(800)
  }

  function test_unreachable_daemon_sets_error_and_keeps_polling() {
    service.settings = { port: 1 }
    waitUntil(function() { return service.lastError !== "" }, 6000)
    verify(!service.reachable)
    compare(service.barText, Model.GLYPH_CALMA)
    service.settings = { port: port }
    waitUntil(function() { return service.lastError === "" && service.status !== null }, 6000)
    verify(service.reachable)
  }
}
