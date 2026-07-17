import SwiftUI

struct PresenterControls: View {
    let currentScenario: TelemetryScenario
    var onScenarioSelected: (TelemetryScenario) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Tap a scenario to flip telemetry and fire the classifier.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Scenarios") {
                    ForEach(TelemetryScenario.allCases, id: \.rawValue) { scenario in
                        row(scenario)
                    }
                }
            }
            .navigationTitle("Presenter")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private func row(_ scenario: TelemetryScenario) -> some View {
        Button {
            onScenarioSelected(scenario)
            dismiss()
        } label: {
            HStack {
                Text(scenario.displayName)
                Spacer()
                if scenario == currentScenario {
                    Image(systemName: "checkmark")
                }
            }
        }
    }
}
