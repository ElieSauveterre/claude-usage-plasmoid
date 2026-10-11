import QtQuick
import QtQuick.Layouts

Item {
    id: row

    property string label: ""
    property string value: ""
    property bool accented: false
    property color accentColor: "#d97757"

    implicitHeight: 24

    Rectangle {
        visible: row.accented
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: 4
        height: 20
        radius: 1
        color: row.accentColor
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: row.accented ? 9 : 4
        spacing: 10

        Text {
            Layout.fillWidth: true
            color: "#f2f3f4"
            elide: Text.ElideRight
            font.pixelSize: 13
            text: row.label
        }

        Text {
            color: "#f2f3f4"
            font.pixelSize: 13
            font.weight: Font.Medium
            horizontalAlignment: Text.AlignRight
            text: row.value
        }
    }
}
