import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var connected: Bool?
    @State private var serverText = APIClient.shared.serverURL()
    @State private var testing = false
    @State private var testResult: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.1.10:5055", text: $serverText)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .environment(\.layoutDirection, .leftToRight)
                } header: {
                    Text("عنوان الخادم (على حاسوبك)")
                } footer: {
                    Text("من حاسوبك شغّل: scripts\\start_all.bat ثم اعرف عنوان الحاسوب (مثال: 192.168.1.10). الجوال والحاسوب يجب أن يكونا على نفس شبكة Wi-Fi.")
                }

                Section {
                    Button {
                        test()
                    } label: {
                        HStack {
                            if testing { ProgressView() }
                            Text(testing ? "جارٍ الاختبار…" : "اختبار الاتصال")
                        }
                    }
                    .disabled(serverText.isEmpty || testing)

                    if let result = testResult {
                        Text(result)
                            .font(.footnote)
                            .foregroundColor(result.hasPrefix("✓") ? .green : .red)
                    }
                } header: {
                    Text("الاتصال")
                }
            }
            .navigationTitle("الإعدادات")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("حفظ") {
                        APIClient.shared.setServerURL(serverText)
                        dismiss()
                    }
                    .disabled(serverText.isEmpty)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("إغلاق") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func test() {
        testing = true
        testResult = nil
        let candidate = serverText
        let client = APIClient.shared
        client.setServerURL(candidate)
        Task {
            defer { testing = false }
            do {
                let ok = try await client.health()
                await MainActor.run {
                    testResult = ok ? "✓ تم الاتصال بنجاح" : "✗ الخادم لا يستجيب"
                    connected = ok
                }
            } catch {
                await MainActor.run {
                    testResult = "✗ \(error.localizedDescription)"
                    connected = false
                }
            }
        }
    }
}
