import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var connected: Bool?
    @State private var serverText = APIClient.shared.serverURL()
    @State private var keyText = APIClient.shared.apiKeyValue()
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
                        .foregroundColor(Theme.text)
                        .environment(\.layoutDirection, .leftToRight)
                } header: {
                    Text("عنوان الخادم (على حاسوبك)")
                } footer: {
                    Text("في البيت: عنوان الحاسوب مثل http://192.168.1.13:5055 · خارج البيت: عنوان Tailscale مثل http://100.94.100.57:5055")
                }

                Section {
                    SecureField("ألصق المفتاح هنا", text: $keyText)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .foregroundColor(Theme.text)
                        .environment(\.layoutDirection, .leftToRight)
                } header: {
                    Text("مفتاح آلي (X-API-Key)")
                } footer: {
                    Text("على الحاسوب افتح الملف: D:\\hwk-data\\aali_master_key.txt وانسخ السطر كله هنا. بدون المفتاح سيقول الخادم لا.")
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
                            .foregroundColor(result.hasPrefix("✓") ? Theme.success : Theme.danger)
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
                        APIClient.shared.setAPIKey(keyText)
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
