import QtQuick
import "Model.js" as Model

// The plugin's engine, instantiated once per shell as its `service` entry
// point; every bar widget (one per monitor) reads this one instance, so one
// poll and one long-poll run however many bars there are. Panels push their
// settings in, since the shell injects settings into widgets only.
//
// Talks to calmad over loopback with XMLHttpRequest: GET /status every
// pollIntervalMs, GET /events as a long-poll so a session ending shows within
// milliseconds, POST /open and /close with the X-Calma header the daemon
// requires on every POST.
Item {
  id: root

  property var shell: null
  property var settings: ({})
  // A widget-local instance, built only under a shell without service
  // support, must stay inert once a shared one exists.
  property bool active: true

  // The last /status the daemon returned, as parsed, or null before the first.
  property var status: null
  readonly property var rules: Model.ruleList(status)
  readonly property bool reachable: status !== null && lastError === ""
  readonly property bool hasOpenSession: Model.openRules(rules).length > 0
  readonly property var soonestRule: Model.soonestSession(rules)
  property bool refreshing: false
  property string lastError: ""
  property string actionStatus: ""
  readonly property bool actionBusy: statusRequest.actionInFlight
  property double lastUpdatedMs: 0
  // Wall clock, ticking every second while a session is open so countdowns
  // move between polls.
  property double nowMs: Date.now()

  readonly property int pollIntervalMs: 5000
  readonly property int requestTimeoutMs: 4000
  readonly property int longPollTimeoutMs: 30000
  readonly property int actionStatusMs: 3000
  readonly property string baseUrl: Model.baseUrl(setting("port", Model.defaultPort))

  readonly property string barText: Model.barText(rules, nowMs)
  readonly property string barTooltip: Model.barTooltip(rules, nowMs, reachable)

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function secondsLeft(rule) { return Model.sessionSecondsLeft(rule, nowMs) }

  function refresh() {
    if (!active) return
    statusRequest.getStatus()
  }

  function open(ruleName, minutes) {
    if (!active) return
    var body = { rule: String(ruleName) }
    var n = parseInt(minutes, 10)
    if (isFinite(n) && n > 0) body.minutes = n
    statusRequest.post("/open", body, "Could not start the session")
  }

  function close(ruleName) {
    if (!active) return
    statusRequest.post("/close", { rule: String(ruleName) }, "Could not end the session")
  }

  // The rule with that name from the latest status, or null.
  function rule(name) {
    if (!status || !status.rules) return null
    var r = status.rules[String(name)]
    if (!r) return null
    var copy = {}
    for (var key in r) copy[key] = r[key]
    copy.name = String(name)
    return copy
  }

  function applyStatus(next) {
    status = next
    lastError = ""
    lastUpdatedMs = Date.now()
    nowMs = Date.now()
  }

  function fail(message) {
    lastError = String(message || "Calma isn't running")
  }

  function showAction(message) {
    actionStatus = String(message || "")
    actionStatusTimer.restart()
  }

  onActiveChanged: {
    if (!active) statusRequest.abortAll()
    else refresh()
  }

  // A plugin hot-reload destroys the service; a request still in flight would
  // otherwise call back into an object whose context is gone.
  Component.onDestruction: statusRequest.abortAll()

  onBaseUrlChanged: {
    statusRequest.abortAll()
    if (active) Qt.callLater(refresh)
  }

  Timer {
    id: pollTimer
    interval: root.pollIntervalMs
    repeat: true
    running: root.active
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    id: tickTimer
    interval: 1000
    repeat: true
    running: root.active && root.hasOpenSession
    onTriggered: root.nowMs = Date.now()
  }

  Timer {
    id: actionStatusTimer
    interval: root.actionStatusMs
    repeat: false
    onTriggered: root.actionStatus = ""
  }

  // After a failure the long-poll waits for the next successful /status; the
  // poll timer is what retries, so nothing spins while the daemon is down.
  QtObject {
    id: statusRequest

    property var statusXhr: null
    property var eventsXhr: null
    property var actionXhr: null
    property bool actionInFlight: false
    // Monotonic token so an aborted request's late callback is ignored.
    property int generation: 0

    function abortAll() {
      generation++
      statusXhr = abortOne(statusXhr)
      eventsXhr = abortOne(eventsXhr)
      actionXhr = abortOne(actionXhr)
      root.refreshing = false
      actionInFlight = false
    }

    // Drops the callback before aborting: abort itself reports DONE.
    function abortOne(xhr) {
      if (!xhr) return null
      try { xhr.onreadystatechange = null } catch (e) {}
      try { xhr.abort() } catch (e) {}
      return null
    }

    function getStatus() {
      if (statusXhr) return
      var gen = generation
      var xhr = new XMLHttpRequest()
      statusXhr = xhr
      root.refreshing = true
      xhr.timeout = root.requestTimeoutMs
      xhr.onreadystatechange = function() {
        if (xhr.readyState !== XMLHttpRequest.DONE) return
        if (gen !== generation) return
        statusXhr = null
        root.refreshing = false
        if (xhr.status === 200) {
          var parsed = Model.parseStatus(xhr.responseText)
          if (parsed.ok) {
            root.applyStatus(parsed.status)
            startEvents()
          } else {
            root.fail(parsed.error)
          }
        } else {
          root.fail(Model.responseError(xhr.status, xhr.responseText, "Calma isn't running"))
        }
      }
      xhr.open("GET", root.baseUrl + "/status")
      xhr.setRequestHeader("Accept", "application/json")
      xhr.send()
    }

    function startEvents() {
      if (eventsXhr || !root.active || !root.status) return
      var gen = generation
      var since = root.status.now
      var xhr = new XMLHttpRequest()
      eventsXhr = xhr
      xhr.timeout = root.longPollTimeoutMs
      xhr.onreadystatechange = function() {
        if (xhr.readyState !== XMLHttpRequest.DONE) return
        if (gen !== generation) return
        eventsXhr = null
        if (xhr.status !== 200) return
        var parsed = Model.parseJson(xhr.responseText)
        if (!parsed.ok) return
        if (parsed.value.changed === true && parsed.value.status && typeof parsed.value.status === "object") {
          var status = parsed.value.status
          if (!status.rules || typeof status.rules !== "object") status.rules = {}
          root.applyStatus(status)
        }
        // Re-arm straight away: the daemon answers within 25 s either way.
        Qt.callLater(startEvents)
      }
      xhr.open("GET", Model.eventsUrl(root.baseUrl, since))
      xhr.setRequestHeader("Accept", "application/json")
      xhr.send()
    }

    function post(path, body, fallbackError) {
      if (actionXhr) return
      var gen = generation
      var xhr = new XMLHttpRequest()
      actionXhr = xhr
      actionInFlight = true
      xhr.timeout = root.requestTimeoutMs
      xhr.onreadystatechange = function() {
        if (xhr.readyState !== XMLHttpRequest.DONE) return
        if (gen !== generation) return
        actionXhr = null
        actionInFlight = false
        var parsed = Model.parseJson(xhr.responseText)
        if (parsed.ok && parsed.value.rule && typeof parsed.value.rule === "object" && root.status) {
          root.applyStatus(Model.withRule(root.status, body.rule, parsed.value.rule))
        }
        if (xhr.status === 200 && parsed.ok && parsed.value.ok === true) {
          var rule = parsed.value.rule || {}
          var label = Model.labelFor(rule) || body.rule
          if (path === "/open") {
            var minutes = rule.session && rule.session.minutes ? rule.session.minutes : body.minutes
            root.showAction(label + " · " + (minutes ? minutes + " min session started" : "session started"))
          } else {
            root.showAction(label + " · session over")
          }
        } else {
          root.showAction(Model.responseError(xhr.status, xhr.responseText, fallbackError))
        }
        // The daemon's view wins over the optimistic row.
        getStatus()
      }
      xhr.open("POST", root.baseUrl + path)
      xhr.setRequestHeader("Content-Type", "application/json")
      xhr.setRequestHeader("Accept", "application/json")
      xhr.setRequestHeader("X-Calma", "1")
      xhr.send(JSON.stringify(body))
    }
  }
}
