import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Calma in the bar: the open session's glyph and m:ss countdown (hidden when
// idle unless hideWhenIdle is off), and a dropdown listing every rule with its
// state, sessions and minutes left, and a Start / End button.
Panel {
  id: root
  moduleName: "io.github.joanreyero.calma"
  ipcTarget: "calma"
  manageIpc: false

  property int cursorIndex: 0
  property bool cursorActive: false

  readonly property bool hideWhenIdle: setting("hideWhenIdle", true) === true
  readonly property bool vertical: bar ? bar.vertical : false
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // One service per shell: the shell instantiates Service.qml once for the
  // plugin and every bar widget reads that instance. A shell without service
  // support gets a widget-local instance instead.
  readonly property var sharedService: bar && bar.shell && typeof bar.shell.serviceFor === "function"
    ? bar.shell.serviceFor(moduleName) : null
  readonly property var service: sharedService || localService

  readonly property var rules: root.service ? root.service.rules : []
  readonly property bool hasOpenSession: root.service ? root.service.hasOpenSession : false
  readonly property bool reachable: root.service ? root.service.reachable : false
  readonly property string heroMetaText: service && service.actionStatus !== ""
    ? service.actionStatus
    : Model.heroMeta(root.service ? root.service.status : null, rules, reachable, root.service ? root.service.lastError : "")
  readonly property var problems: Model.problems(root.service ? root.service.status : null)

  // The shell injects settings into widgets, not services: push them across.
  function pushSettings() { if (root.service) root.service.settings = settings }
  onSettingsChanged: pushSettings()
  onServiceChanged: pushSettings()
  Component.onCompleted: pushSettings()

  function formatTime(date) { return Qt.formatTime(date, "HH:mm") }

  function ruleMeta(rule) { return Model.ruleMeta(rule, root.service ? root.service.nowMs : Date.now(), root.formatTime) }

  function rowHasCursor(index) { return cursorActive && cursorIndex === index }

  function moveCursor(dy) {
    cursorActive = true
    if (rules.length === 0) return
    cursorIndex = Math.max(0, Math.min(rules.length - 1, cursorIndex + dy))
  }

  function focusRow(index) {
    cursorActive = true
    cursorIndex = index
  }

  function act(rule) {
    if (!rule || !service || service.actionBusy) return
    if (rule.state === Model.STATE_OPEN) service.close(rule.name)
    else if (rule.can_open === true) service.open(rule.name, Model.startMinutes(rule))
  }

  function activateCursor() {
    if (!cursorActive || rules.length === 0) return
    act(rules[Math.max(0, Math.min(rules.length - 1, cursorIndex))])
  }

  // Ends every open session; what the bar's right click and the IPC `end` do.
  function endAll() {
    if (!service) return
    var open = Model.openRules(rules)
    for (var i = 0; i < open.length; i++) service.close(open[i].name)
  }

  // Stays while its panel is open: a session ending must not pull the anchor
  // out from under the dropdown.
  visible: !hideWhenIdle || hasOpenSession || opened
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // The clock's text label sets the pattern: the open-panel dot under the
  // widget takes the label's width rather than the whole padded slot.
  readonly property real openPanelIndicatorWidth: button.labelWidth

  onOpenedChanged: if (opened) {
    cursorActive = false
    cursorIndex = 0
    if (panelFlick) panelFlick.contentY = 0
    if (root.service) root.service.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Service {
    id: localService
    active: root.sharedService === null
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { if (root.service) root.service.refresh(); return "ok" }
    function start(rule: string): string { if (root.service) root.service.open(rule, 0); return "ok" }
    function end(rule: string): string {
      if (rule === "") root.endAll()
      else if (root.service) root.service.close(rule)
      return "ok"
    }
    function status(): string {
      return JSON.stringify({
        reachable: root.reachable,
        rules: root.rules.length,
        open: Model.openRules(root.rules).length,
        barText: root.service ? root.service.barText : "",
        visible: root.visible,
        hideWhenIdle: root.hideWhenIdle,
        error: root.service ? root.service.lastError : ""
      })
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.vertical ? "" : (root.service ? root.service.barText : Model.GLYPH_CALMA)
    labelVisible: !root.vertical
    hasVisualContent: true
    dimmed: !root.hasOpenSession
    horizontalMargin: 8.75
    verticalPadding: 8.75
    tooltipText: root.service ? root.service.barTooltip : ""

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.endAll()
      else if (buttonCode === Qt.MiddleButton) { if (root.service) root.service.refresh() }
      else root.toggle()
    }

    // Vertical bars take the glyph alone, in the icon slot like every icon widget.
    OpticalGlyph {
      visible: root.vertical
      anchors.centerIn: parent
      width: Style.bar.iconCanvas
      height: Style.bar.iconCanvas
      text: Model.glyphFor(root.service ? root.service.soonestRule : null)
      fontFamily: button.fontFamily
      fontSize: Style.bar.iconFont
      color: button.foreground
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        if (dy !== 0) root.moveCursor(dy)
      }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var key = String(t).toLowerCase()
        if (key === "r") { if (root.service) root.service.refresh() }
        else if (key === "e") root.endAll()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            id: hero
            width: parent.width
            title: "Calma"
            meta: root.heroMetaText
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconOpacity: root.reachable ? 1.0 : 0.5
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: Model.GLYPH_CALMA
                color: root.reachable ? root.foreground : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
            trailingControl: Component {
              PanelActionButton {
                iconText: root.service && root.service.refreshing ? "󰑓" : "󰑐"
                tooltipText: "Refresh"
                foreground: root.foreground
                fontFamily: root.fontFamily
                enabled: !(root.service && root.service.refreshing)
                onClicked: if (root.service) root.service.refresh()
              }
            }
          }

          PanelSeparator {
            visible: root.rules.length > 0
            foreground: root.foreground
          }

          Column {
            visible: root.rules.length > 0
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              text: "RULES"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Column {
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: root.rules

                RuleRow {
                  required property var modelData
                  required property int index
                  width: parent.width
                  rule: modelData
                  rowIndex: index
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: root.rules.length === 0
            width: parent.width
            text: root.reachable
              ? "No rules yet. Add one in a terminal: sudo calma add x --preset x --sessions 2 --minutes 5"
              : "Start the Calma daemon to see your rules: sudo systemctl start calma"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
          }

          PanelSeparator {
            visible: root.problems.length > 0
            foreground: root.foreground
          }

          Column {
            visible: root.problems.length > 0
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "PROBLEMS"
              foreground: root.urgent
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.problems

              Text {
                required property string modelData
                textFormat: Text.PlainText
                width: parent.width
                text: modelData
                color: root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            width: parent.width
            text: "calma status in a terminal · j/k move · enter acts · e ends all"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignHCenter
          }
        }
      }
    }
  }

  component RuleRow: CursorSurface {
    id: ruleRow
    property var rule: ({})
    property int rowIndex: 0

    readonly property bool isOpen: rule.state === Model.STATE_OPEN
    readonly property string action: Model.actionLabel(rule)

    hasCursor: root.rowHasCursor(ruleRow.rowIndex)
    foreground: root.foreground
    implicitHeight: rowContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: ruleRow.action !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor
      onEntered: root.focusRow(ruleRow.rowIndex)
      onClicked: root.act(ruleRow.rule)
    }

    RowLayout {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(10)

      Text {
        textFormat: Text.PlainText
        Layout.alignment: Qt.AlignVCenter
        Layout.preferredWidth: Style.space(20)
        horizontalAlignment: Text.AlignHCenter
        text: Model.glyphFor(ruleRow.rule)
        color: ruleRow.isOpen ? root.foreground : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: Model.labelFor(ruleRow.rule)
          color: root.foreground
          opacity: ruleRow.isOpen || ruleRow.rule.can_open === true ? 1.0 : 0.75
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.weight: ruleRow.isOpen ? Font.DemiBold : Font.Normal
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: root.ruleMeta(ruleRow.rule)
          color: ruleRow.isOpen ? Color.accent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      Button {
        visible: ruleRow.action !== ""
        Layout.alignment: Qt.AlignVCenter
        text: ruleRow.action
        bordered: true
        selected: ruleRow.isOpen
        hasCursor: ruleRow.hasCursor
        foreground: root.foreground
        background: Color.popups.background
        accent: Color.accent
        fontFamily: root.fontFamily
        fontSize: Style.font.bodySmall
        horizontalPadding: Style.spacing.controlPaddingX
        verticalPadding: Style.space(3)
        enabled: !(root.service && root.service.actionBusy)
        onClicked: root.act(ruleRow.rule)
      }
    }
  }
}
