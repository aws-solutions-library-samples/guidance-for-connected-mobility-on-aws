import SwiftUI

struct RootView: View {
    @Environment(AppSession.self) private var session

    var body: some View {
        switch session.authState {
        case .signedOut, .signingIn, .failed:
            SignInView()
        case .signedIn:
            MainTabView()
        }
    }
}
