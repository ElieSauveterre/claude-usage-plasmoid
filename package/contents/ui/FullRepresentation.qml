import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents

Item {
    id: full

    readonly property color accentColor: "#d97757"
    readonly property bool showWeeklyReset: full.hasWeekly && full.weeklyResetText.length > 0

    required property real remainingPercent
    required property string windowLabel
    required property string resetText
    required property bool hasWeekly
    required property real weeklyRemainingPercent
    required property string weeklyResetText
    required property bool loading
    required property string errorMessage
    required property string lastUpdatedText
    required property string warningMessage
    readonly property bool showWarning: full.warningMessage.length > 0 && full.errorMessage.length === 0
    signal refreshRequested()

    Layout.minimumWidth: 250
    Layout.minimumHeight: implicitHeight
    Layout.preferredWidth: 260
    Layout.preferredHeight: implicitHeight
    implicitWidth: 260
    implicitHeight: 258 + (full.hasWeekly ? 24 : 0) + (full.showWeeklyReset ? 24 : 0) + (full.showWarning ? 18 : 0)

    Rectangle {
        anchors.fill: parent
        color: "#202428"
        radius: 6

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 6

            Item {
                Layout.fillWidth: true
                implicitHeight: 28

                Text {
                    anchors.centerIn: parent
                    color: "#f2f3f4"
                    font.pixelSize: 16
                    font.weight: Font.Medium
                    text: qsTr("Claude Usage")
                }

                PlasmaComponents.ToolButton {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    enabled: !full.loading
                    display: PlasmaComponents.AbstractButton.IconOnly
                    icon.name: "view-refresh"
                    onClicked: full.refreshRequested()

                    PlasmaComponents.ToolTip {
                        text: qsTr("Actualiser")
                    }
                }
            }

            Item {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 146
                Layout.preferredHeight: 146

                ProgressRing {
                    anchors.fill: parent
                    value: full.remainingPercent
                    lineWidth: 8
                    centerFontSize: 15
                    centerText: full.loading ? "…" : full.errorMessage.length > 0 ? "!" : Math.round(value) + "%"
                    subText: full.errorMessage.length > 0
                        ? qsTr("indisponible")
                        : qsTr("restant · %1").arg(full.windowLabel)
                    progressColor: full.errorMessage.length > 0 ? "#e06c75" : full.accentColor
                    textColor: "#f2f3f4"
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                MetricRow {
                    Layout.fillWidth: true
                    accented: true
                    accentColor: full.accentColor
                    label: qsTr("Remise à zéro")
                    value: full.resetText.length > 0 ? full.resetText : "—"
                }

                MetricRow {
                    visible: full.hasWeekly
                    Layout.fillWidth: true
                    label: qsTr("Restant sur 7 jours")
                    value: Math.round(full.weeklyRemainingPercent) + "%"
                }

                MetricRow {
                    visible: full.showWeeklyReset
                    Layout.fillWidth: true
                    label: qsTr("Remise hebdomadaire")
                    value: full.weeklyResetText
                }
            }

            Text {
                Layout.fillWidth: true
                color: full.errorMessage.length > 0 ? "#ef9a9a" : "#7f8991"
                elide: Text.ElideRight
                font.pixelSize: 9
                horizontalAlignment: Text.AlignHCenter
                text: full.errorMessage.length > 0 ? full.errorMessage : full.lastUpdatedText
            }

            Text {
                visible: full.showWarning
                Layout.fillWidth: true
                color: "#ef9a9a"
                elide: Text.ElideRight
                font.pixelSize: 9
                horizontalAlignment: Text.AlignHCenter
                text: full.warningMessage
            }
        }
    }
}
