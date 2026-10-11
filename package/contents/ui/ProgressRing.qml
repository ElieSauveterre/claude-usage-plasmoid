import QtQuick
import org.kde.kirigami as Kirigami

Item {
    id: ring

    property real value: 0
    property real lineWidth: 8
    property color trackColor: "#34393e"
    property color progressColor: "#d97757"
    property color textColor: Kirigami.Theme.textColor
    property string centerText: Math.round(value) + "%"
    property string subText: ""
    property real centerFontSize: 16
    property bool showSubText: true

    implicitWidth: 146
    implicitHeight: 146

    onValueChanged: canvas.requestPaint()
    onLineWidthChanged: canvas.requestPaint()
    onTrackColorChanged: canvas.requestPaint()
    onProgressColorChanged: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent

        onPaint: {
            const context = getContext("2d")
            context.reset()

            const centerX = width / 2
            const centerY = height / 2
            const radius = Math.max(0, Math.min(width, height) / 2 - ring.lineWidth)
            const startAngle = -Math.PI / 2
            const progressAngle = startAngle + (Math.PI * 2 * Math.max(0, Math.min(100, ring.value)) / 100)

            context.lineWidth = ring.lineWidth
            context.lineCap = "round"

            context.beginPath()
            context.strokeStyle = ring.trackColor
            context.arc(centerX, centerY, radius, 0, Math.PI * 2, false)
            context.stroke()

            if (ring.value > 0) {
                context.beginPath()
                context.strokeStyle = ring.progressColor
                context.arc(centerX, centerY, radius, startAngle, progressAngle, false)
                context.stroke()
            }
        }
    }

    Column {
        anchors.centerIn: parent
        spacing: 1

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            color: ring.textColor
            font.pixelSize: ring.centerFontSize
            font.weight: Font.Medium
            text: ring.centerText
        }

        Text {
            visible: ring.showSubText && text.length > 0
            anchors.horizontalCenter: parent.horizontalCenter
            color: Qt.rgba(ring.textColor.r, ring.textColor.g, ring.textColor.b, 0.62)
            font.pixelSize: 10
            text: ring.subText
        }
    }
}
