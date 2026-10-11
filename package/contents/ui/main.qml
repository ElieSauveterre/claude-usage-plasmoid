pragma ComponentBehavior: Bound

import QtQuick
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.plasma.plasmoid

PlasmoidItem {
    id: root

    property real remainingPercent: 0
    property string windowLabel: ""
    property string resetText: ""
    property bool hasWeekly: false
    property real weeklyRemainingPercent: 0
    property string weeklyResetText: ""
    property bool loading: false
    property string errorMessage: ""
    property string lastUpdatedText: ""
    property string activeCommand: ""

    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Plasmoid.icon: "chronometer"
    Plasmoid.title: qsTr("Claude Usage")

    toolTipMainText: qsTr("Claude Usage")
    toolTipSubText: {
        if (errorMessage.length > 0) {
            return errorMessage
        }
        if (windowLabel.length === 0) {
            return ""
        }
        const summary = qsTr("%1 % restant (%2)").arg(Math.round(remainingPercent)).arg(windowLabel)
        return resetText.length > 0 ? summary + " · " + resetText : summary
    }

    compactRepresentation: CompactRepresentation {
        remainingPercent: root.remainingPercent
        loading: root.loading
        errorMessage: root.errorMessage
        horizontalPanel: Plasmoid.formFactor === PlasmaCore.Types.Horizontal
        onActivated: {
            root.expanded = !root.expanded
            if (root.expanded) {
                root.refresh()
            }
        }
    }

    fullRepresentation: FullRepresentation {
        remainingPercent: root.remainingPercent
        windowLabel: root.windowLabel
        resetText: root.resetText
        hasWeekly: root.hasWeekly
        weeklyRemainingPercent: root.weeklyRemainingPercent
        weeklyResetText: root.weeklyResetText
        loading: root.loading
        errorMessage: root.errorMessage
        lastUpdatedText: root.lastUpdatedText
        onRefreshRequested: root.refresh()
    }

    Plasma5Support.DataSource {
        id: executable
        engine: "executable"

        onNewData: (sourceName, data) => {
            if (sourceName !== root.activeCommand) {
                return
            }

            disconnectSource(sourceName)
            root.loading = false

            const output = data.stdout || ""
            try {
                const payload = JSON.parse(output.trim())
                if (!payload.ok) {
                    root.errorMessage = payload.error || qsTr("Données Claude indisponibles")
                    return
                }
                root.applyPayload(payload)
            } catch (error) {
                root.errorMessage = qsTr("Réponse Claude illisible")
            }
        }
    }

    // Reading the local snapshot is cheap; Claude Code refreshes it while sessions run.
    Timer {
        interval: 60 * 1000
        repeat: true
        running: true
        onTriggered: root.refresh()
    }

    Component.onCompleted: refresh()

    function shellQuote(value) {
        return "'" + value.replace(/'/g, "'\\''") + "'"
    }

    function helperPath() {
        const url = Qt.resolvedUrl("../code/claude_usage.py").toString()
        return decodeURIComponent(url.replace(/^file:\/\//, ""))
    }

    function refresh() {
        if (loading) {
            return
        }

        if (activeCommand.length > 0) {
            executable.disconnectSource(activeCommand)
        }

        loading = true
        errorMessage = ""
        activeCommand = "python3 " + shellQuote(helperPath()) + " --request-id " + Date.now()
        executable.connectSource(activeCommand)
    }

    function resetLabel(timestamp) {
        if (!timestamp) {
            return ""
        }
        const date = new Date(timestamp * 1000)
        return date.toLocaleString(Qt.locale(), "ddd HH:mm")
    }

    function capturedLabel(timestamp) {
        if (!timestamp) {
            return ""
        }
        const date = new Date(timestamp * 1000)
        const sameDay = date.toDateString() === new Date().toDateString()
        return qsTr("Données du %1").arg(date.toLocaleString(Qt.locale(), sameDay ? "HH:mm" : "d MMM HH:mm"))
    }

    function applyPayload(payload) {
        const primary = payload.fiveHour || payload.sevenDay
        const weekly = payload.fiveHour ? payload.sevenDay : null

        remainingPercent = primary.remainingPercent
        windowLabel = payload.fiveHour ? qsTr("5 h") : qsTr("7 j")
        resetText = resetLabel(primary.resetsAt)
        hasWeekly = weekly !== null
        weeklyRemainingPercent = weekly ? weekly.remainingPercent : 0
        weeklyResetText = weekly ? resetLabel(weekly.resetsAt) : ""
        errorMessage = ""
        lastUpdatedText = capturedLabel(payload.updatedAt)
    }
}
