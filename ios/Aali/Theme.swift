import SwiftUI

/// Deep-ink night + warm amber-gold, matching the web client.
enum Theme {
    static let ink = Color(red: 0.043, green: 0.055, blue: 0.078)
    static let ink2 = Color(red: 0.063, green: 0.078, blue: 0.114)
    static let ink3 = Color(red: 0.086, green: 0.106, blue: 0.153)
    static let line = Color(red: 0.137, green: 0.165, blue: 0.227)
    static let text = Color(red: 0.910, green: 0.902, blue: 0.890)
    static let muted = Color(red: 0.545, green: 0.576, blue: 0.655)
    static let gold = Color(red: 0.910, green: 0.702, blue: 0.294)
    static let goldSoft = Color(red: 0.961, green: 0.812, blue: 0.541)
    static let userBubble = Color(red: 0.110, green: 0.145, blue: 0.212)
}

/// Amber eight-point star mark, drawn to match the web/app icons.
struct StarMark: View {
    var size: CGFloat = 38

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.3, style: .continuous)
                .fill(Theme.gold.opacity(0.14))
                .overlay(
                    RoundedRectangle(cornerRadius: size * 0.3, style: .continuous)
                        .stroke(Theme.gold.opacity(0.35), lineWidth: 1)
                )
            StarShape()
                .fill(Theme.gold)
                .padding(size * 0.22)
                .shadow(color: Theme.gold.opacity(0.35), radius: 6)
        }
        .frame(width: size, height: size)
    }
}

struct StarShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let center = CGPoint(x: rect.midX, y: rect.midY)
        let rOut = min(rect.width, rect.height) / 2
        let rIn = rOut * 0.22
        for i in 0..<8 {
            let r = i % 2 == 0 ? rOut : rIn
            let angle = Double(i) * .pi / 4 - .pi / 2
            let point = CGPoint(
                x: center.x + r * cos(angle),
                y: center.y + r * sin(angle)
            )
            if i == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        path.closeSubpath()
        return path
    }
}

/// Orbiting-star "thinking" indicator (mirrors the web client).
struct OrbitIndicator: View {
    @State private var spinning = false

    var body: some View {
        ZStack {
            Circle().fill(Theme.gold)
                .frame(width: 10, height: 10)
                .shadow(color: Theme.gold.opacity(0.6), radius: 5)
            Circle()
                .strokeBorder(Theme.gold.opacity(0.4), style: StrokeStyle(lineWidth: 1, dash: [3]))
                .frame(width: 32, height: 32)
            Image(systemName: "sparkle")
                .font(.system(size: 9))
                .foregroundColor(Theme.goldSoft)
                .offset(y: -16)
                .rotationEffect(.degrees(spinning ? 360 : 0))
        }
        .frame(width: 34, height: 34)
        .animation(.linear(duration: 1.6).repeatForever(autoreverses: false), value: spinning)
        .onAppear { spinning = true }
    }
}
