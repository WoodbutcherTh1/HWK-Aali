import SwiftUI

struct ChatMessage: Identifiable, Equatable {
    let id = UUID()
    let role: Role
    let text: String
    var isThinking = false

    enum Role { case user, assistant }
}

struct ChatView: View {
    @State private var messages: [ChatMessage] = []
    @State private var draft = ""
    @State private var waiting = false
    @State private var connected: Bool?
    @State private var showSettings = false
    @State private var showResetPrompt = false
    private let api = APIClient.shared

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                connectionBar
                messageList
                composer
            }
            .background(Theme.ink)
            .navigationTitle("آلي")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showResetPrompt = true } label: {
                        Image(systemName: "plus.bubble")
                    }
                    .accessibilityLabel("محادثة جديدة")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape")
                    }
                    .accessibilityLabel("الإعدادات")
                }
            }
            .sheet(isPresented: $showSettings) { SettingsView(connected: $connected) }
            .confirmationDialog("ابدأ محادثة جديدة؟", isPresented: $showResetPrompt, titleVisibility: .visible) {
                Button("محادثة جديدة", role: .destructive) {
                    api.resetConversation()
                    messages.removeAll()
                }
                Button("إلغاء", role: .cancel) {}
            }
            .task { await ping() }
        }
    }

    private var connectionBar: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(statusColor)
                .frame(width: 8, height: 8)
            Text(statusText)
                .font(.caption)
                .foregroundColor(Theme.muted)
            Spacer()
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 6)
        .background(Theme.ink2)
    }

    private var statusColor: Color {
        switch connected {
        case .some(true): return .green
        case .some(false): return .red
        case .none: return .gray
        }
    }

    private var statusText: String {
        switch connected {
        case .some(true): return "متصل بالخادم"
        case .some(false): return "غير متصل — افتح الإعدادات ⚙︎"
        case .none: return "جارٍ التحقق…"
        }
    }

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if messages.isEmpty {
                    welcome
                } else {
                    LazyVStack(spacing: 12) {
                        ForEach(messages) { msg in
                            MessageBubble(message: msg)
                                .id(msg.id)
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 16)
                }
            }
            .onChange(of: messages) { _, new in
                if let last = new.last {
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
    }

    private var welcome: some View {
        VStack(spacing: 14) {
            StarMark(size: 76)
                .padding(.top, 60)
            Text("مرحباً بك في عقل آلي")
                .font(.title2.bold())
            Text("اسألني أي شيء، أو اطلب بناء تطبيق أو تعديل ملفات على حاسوبك.")
                .font(.subheadline)
                .foregroundColor(Theme.muted)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, 80)
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("اكتب رسالتك…", text: $draft, axis: .vertical)
                .lineLimit(1...5)
                .padding(12)
                .background(Theme.ink2)
                .overlay(
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(Theme.line, lineWidth: 1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                .onSubmit(send)

            Button(action: send) {
                Group {
                    if waiting {
                        OrbitIndicator()
                    } else {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 18, weight: .bold))
                    }
                }
                .frame(width: 42, height: 42)
                .background(waiting ? Theme.ink3 : Theme.gold)
                .foregroundColor(waiting ? Theme.gold : .black)
                .clipShape(Circle())
            }
            .disabled(waiting || draft.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
        .padding(.bottom, 6)
        .background(Theme.ink)
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !waiting else { return }
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        messages.append(ChatMessage(role: .user, text: text))
        draft = ""
        waiting = true
        messages.append(ChatMessage(role: .assistant, text: "", isThinking: true))

        Task {
            do {
                let reply = try await api.ask(text)
                replaceThinking(with: reply)
                connected = true
            } catch {
                replaceThinking(with: error.localizedDescription)
                connected = false
            }
            waiting = false
        }
    }

    private func replaceThinking(with text: String) {
        guard let index = messages.lastIndex(where: { $0.isThinking }) else {
            messages.append(ChatMessage(role: .assistant, text: text))
            return
        }
        messages[index] = ChatMessage(role: .assistant, text: text)
    }

    private func ping() async {
        guard api.isConfigured else {
            connected = false
            showSettings = APIClient.shared.serverURL().isEmpty
            return
        }
        connected = (try? await api.health()) ?? false
    }
}

struct MessageBubble: View {
    let message: ChatMessage

    /// Per-message direction: Arabic/Hebrew reads RTL, code and English read LTR.
    private var contentDirection: LayoutDirection {
        message.text.containsArabic ? .rightToLeft : .leftToRight
    }

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 40) }
            VStack(alignment: .leading, spacing: 4) {
                Text(message.role == .user ? "أنت" : "آلي")
                    .font(.caption2)
                    .foregroundColor(Theme.goldSoft)
                if message.isThinking {
                    OrbitIndicator()
                } else {
                    Text(message.text)
                        .textSelection(.enabled)
                }
            }
            .padding(14)
            .frame(maxWidth: 320, alignment: .leading)
            .background(message.role == .user ? Theme.userBubble : Theme.ink3)
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(message.role == .user ? Theme.line : Theme.gold.opacity(0.25), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .environment(\.layoutDirection, contentDirection)
            if message.role == .assistant { Spacer(minLength: 40) }
        }
    }
}

extension String {
    var containsArabic: Bool {
        unicodeScalars.contains { (0x0600...0x06FF).contains($0.value) }
    }
}
