import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import Qt5Compat.GraphicalEffects
import org.kde.kirigami as Kirigami
import org.kde.quickcharts as Charts
import org.kde.quickcharts.controls as ChartControls

QQC2.Control {
    id: compact

    required property real remainingPercent
    required property bool loading
    required property string errorMessage
    required property bool horizontalPanel
    readonly property color progressColor: compact.errorMessage.length > 0 ? "#e06c75" : "#d97757"
    signal activated()

    // Use the same wrapper contract as org.kde.plasma.systemmonitor.
    Layout.fillWidth: face.Layout.fillWidth
    Layout.fillHeight: face.Layout.fillHeight
    Layout.minimumWidth: face.Layout.minimumWidth
    Layout.minimumHeight: face.Layout.minimumHeight
    Layout.preferredWidth: face.Layout.preferredWidth
    Layout.preferredHeight: face.Layout.preferredHeight
    Layout.maximumWidth: face.Layout.maximumWidth
    Layout.maximumHeight: face.Layout.maximumHeight

    anchors.fill: parent
    leftPadding: 0
    rightPadding: 0
    topPadding: 0
    bottomPadding: 0

    contentItem: Item {
        id: face

        Layout.minimumWidth: Kirigami.Units.gridUnit
        Layout.minimumHeight: Kirigami.Units.gridUnit
        Layout.preferredWidth: compact.horizontalPanel
            ? Math.min(Math.max(height, Layout.minimumWidth), Layout.maximumWidth)
            : -1
        Layout.preferredHeight: compact.horizontalPanel
            ? -1
            : Math.min(Math.max(width, Layout.minimumHeight), Layout.maximumHeight)
        Layout.maximumWidth: compact.horizontalPanel
            ? Math.max(Kirigami.Units.iconSizes.enormous, Layout.minimumWidth)
            : -1
        Layout.maximumHeight: compact.horizontalPanel
            ? -1
            : Math.max(Kirigami.Units.iconSizes.enormous, Layout.minimumHeight)

        ColumnLayout {
            anchors.fill: parent

            ChartControls.PieChartControl {
                id: chart

                Layout.minimumHeight: compact.horizontalPanel ? Kirigami.Units.gridUnit : width
                Layout.maximumHeight: Math.min(Math.max(face.width, Layout.minimumHeight), face.height)
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.alignment: Qt.AlignCenter

                leftPadding: 0
                rightPadding: 0
                topPadding: 0
                bottomPadding: 0

                color: compact.progressColor

                chart.smoothEnds: true
                chart.fromAngle: -180
                chart.toAngle: 180
                chart.thickness: Kirigami.Units.smallSpacing
                chart.backgroundColor: Kirigami.ColorUtils.linearInterpolation(
                    Kirigami.Theme.backgroundColor,
                    Kirigami.Theme.textColor,
                    0.1
                )

                range {
                    from: 0
                    to: 100
                    automatic: false
                }

                valueSources: Charts.ArraySource {
                    array: [Math.max(0, Math.min(100, compact.remainingPercent))]
                }

                QQC2.Label {
                    anchors.centerIn: parent
                    width: Math.min(parent.width, parent.height)
                    height: width
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    fontSizeMode: Text.HorizontalFit
                    minimumPointSize: Kirigami.Theme.smallFont.pointSize * 0.8
                    maximumLineCount: 1
                    layer.enabled: chart.width < Kirigami.Units.gridUnit * 2
                    layer.effect: Glow {
                        radius: 4
                        spread: 0.75
                        color: Kirigami.Theme.backgroundColor
                    }
                    text: compact.loading
                        ? "…"
                        : compact.errorMessage.length > 0
                            ? "!"
                            : Math.round(compact.remainingPercent) + "%"
                }
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: compact.activated()
    }
}
