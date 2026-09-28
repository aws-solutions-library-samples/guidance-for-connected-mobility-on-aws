//
//  EscalationRecommendationCard.swift
//  MeridianMotorsCompanion
//
//  "Critical fault detected — we recommend speaking with an agent."
//
//  One view, two surfaces (CVX spec 2026-09-23, Task 3.1, per
//  `ios-affordance-design.md` § 2):
//
//    • Assistant — rendered from the live voice session's verdict. Its buttons
//      send `escalation.request`; the server answers with the `escalation`
//      event that opens the chat.
//    • Vehicle   — rendered from the snapshot `AppSession` keeps after the
//      session ends. It cannot connect a chat itself (there is no session to
//      ask), so its one action opens the assistant.
//
//  Whether to render at all is decided upstream by
//  `EscalationRecommendation.evaluate`; this view only draws a snapshot it is
//  handed. That keeps the fail-closed rule in one place, tested once.
//

import SwiftUI

struct EscalationRecommendationCard: View {

    enum Surface: Equatable {
        /// Live session. `chatOpen` stands the buttons down while a chat is
        /// being set up or is live; `requestInFlight` while a tap is pending.
        case assistant(chatOpen: Bool, requestInFlight: EscalationRecommendation.RequestIntent?)
        /// Stored snapshot, shown after the conversation ended.
        case vehicle
    }

    let snapshot: EscalationRecommendation.Snapshot
    let surface: Surface
    /// Lead with roadside instead of an agent. The caller computes it with
    /// `EscalationRecommendation.leadsWithRoadside`; false keeps the default.
    var leadsWithRoadside: Bool = false
    var onTalkToAgent: () -> Void = {}
    var onRequestRoadside: () -> Void = {}
    var onOpenAssistant: () -> Void = {}
    var onDismiss: () -> Void = {}

    private var accent: Color { snapshot.isCritical ? .red : .orange }

    private var title: String {
        leadsWithRoadside ? EscalationRecommendation.roadsideFirstTitle : snapshot.title
    }

    private var subtitle: String {
        leadsWithRoadside ? EscalationRecommendation.roadsideFirstSubtitle : snapshot.subtitle
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.title3)
                    .foregroundStyle(accent)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(title).font(.subheadline).bold()
                        if let label = snapshot.categoryLabel {
                            Text(label)
                                .font(.caption2.weight(.semibold))
                                .padding(.horizontal, 6).padding(.vertical, 2)
                                .background(Capsule().fill(accent.opacity(0.18)))
                                .foregroundStyle(accent)
                        }
                    }
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if surface == .vehicle {
                        Text(EscalationRecommendation.asOfText(snapshot.observedAt) + ". "
                             + EscalationRecommendation.vehicleTabCaption)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
                Button(action: onDismiss) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3).foregroundStyle(.secondary)
                }
                .accessibilityLabel("Dismiss recommendation")
            }
            actions
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(accent.opacity(0.10))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(accent.opacity(0.40), lineWidth: 1)
                )
        )
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder
    private var actions: some View {
        switch surface {
        case .vehicle:
            Button("Open assistant", action: onOpenAssistant)
                .buttonStyle(.borderedProminent)
                .tint(accent)
                .controlSize(.small)

        case .assistant(let chatOpen, _):
            if chatOpen {
                // The recommendation stays visible alongside the chat, but a
                // second request would open a second chat — the agent handles
                // anything further from here.
                if snapshot.offersRoadside {
                    Text(EscalationRecommendation.roadsideViaAgentNotice)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else {
                HStack(spacing: 8) {
                    // A separate tap, so a driver who wants a person does not
                    // implicitly order a truck (spec D3). P0 only. When the
                    // card recommends roadside, that button comes first and is
                    // the prominent one, so the layout matches the words.
                    if leadsWithRoadside && snapshot.offersRoadside {
                        roadsideButton(prominent: true)
                        agentButton(prominent: false)
                    } else {
                        agentButton(prominent: true)
                        if snapshot.offersRoadside {
                            roadsideButton(prominent: false)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func agentButton(prominent: Bool) -> some View {
        let inFlight = requestInFlight
        let button = Button(action: onTalkToAgent) {
            if inFlight == .human {
                ProgressView().controlSize(.small)
            } else {
                Text("Talk to an agent")
            }
        }
        .tint(accent)
        .controlSize(.small)
        .disabled(inFlight != nil)
        if prominent {
            button.buttonStyle(.borderedProminent)
        } else {
            button.buttonStyle(.bordered)
        }
    }

    @ViewBuilder
    private func roadsideButton(prominent: Bool) -> some View {
        let inFlight = requestInFlight
        let button = Button(action: onRequestRoadside) {
            if inFlight == .roadside {
                ProgressView().controlSize(.small)
            } else {
                Text("Request roadside")
            }
        }
        .tint(accent)
        .controlSize(.small)
        .disabled(inFlight != nil)
        if prominent {
            button.buttonStyle(.borderedProminent)
        } else {
            button.buttonStyle(.bordered)
        }
    }

    private var requestInFlight: EscalationRecommendation.RequestIntent? {
        if case .assistant(_, let inFlight) = surface { return inFlight }
        return nil
    }
}
