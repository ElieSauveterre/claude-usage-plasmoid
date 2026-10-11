import QtQuick
import QtQuick.Window
import "../package/contents/ui"

Window {
    id: window
    width: 24
    height: 24
    visible: true

    CompactRepresentation {
        id: compact
        remainingPercent: 67
        loading: false
        errorMessage: ""
        horizontalPanel: true
    }

    Timer {
        interval: 500
        running: true
        repeat: false
        onTriggered: Qt.exit(compact.width === 24 && compact.height === 24 ? 0 : 1)
    }
}
