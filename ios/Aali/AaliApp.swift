// آلي — Aali for iOS
// SwiftUI app talking to the local brain (Flask /api/ask) over Wi-Fi.
// iOS 16+. No third-party dependencies.

import SwiftUI

@main
struct AaliApp: App {
    var body: some Scene {
        WindowGroup {
            ChatView()
                .environment(\.layoutDirection, .rightToLeft)
                .preferredColorScheme(.dark)
        }
    }
}
