import QtQuick
import Quickshell.Io

// Is Calma installed on this machine? True when the CLI or the configuration
// directory exists, whether or not the daemon answers. Also reports whether
// Omarchy's terminal launcher is there, for the "Open a terminal" action.
// Kept in its own file because Quickshell.Io only loads inside Quickshell:
// Service.qml creates it at runtime and runs without it (never knowing) under
// plain Qt, such as the QtTest harness in dev/plugin.
Item {
  id: probe

  property string binPath: "/usr/bin/calma"
  property string etcPath: "/etc/calma"
  property string terminalPath: "/usr/share/omarchy/bin/omarchy-launch-terminal"
  readonly property bool running: process.running

  signal finished(bool installed, bool hasTerminal)

  function check() {
    if (!process.running) process.running = true
  }

  Process {
    id: process
    running: false
    command: ["sh", "-c",
      'i=0; t=0; { test -e "$0" || test -e "$1"; } && i=1; test -x "$2" && t=1; echo "$i $t"',
      probe.binPath, probe.etcPath, probe.terminalPath]
    stdout: StdioCollector {
      id: out
      waitForEnd: true
    }
    onExited: function(exitCode) {
      var parts = String(out.text || "").trim().split(" ")
      probe.finished(parts[0] === "1", parts[1] === "1")
    }
  }
}
