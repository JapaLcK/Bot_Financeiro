import UIKit
import Capacitor
import LocalAuthentication
import WebKit
import AuthenticationServices
import UserNotifications

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?

    // ── Trava biométrica (Face ID / Touch ID / código do aparelho) ─────────
    // Cobre o conteúdo ao ir pro background (nada de saldo no app switcher) e
    // exige autenticação no cold launch e ao voltar depois do período de graça.
    private var lockWindow: UIWindow?
    private var retryButton: UIButton?
    private var unlocked = false
    private var authInFlight = false
    private var backgroundedAt: Date?
    private let gracePeriod: TimeInterval = 60

    // ── Login Google nativo (Face ID / autofill) ──────────────────────────
    // O WebView do Google perde o autofill do iOS. Aqui o OAuth roda num
    // ASWebAuthenticationSession (navegador nativo, com Face ID/keychain), e o
    // servidor devolve um código de uso único pelo scheme pigbankai:// → o app
    // carrega /d/{code} no WebView pra logar de verdade.
    private let siteBase = "https://pigbankai.com"
    private var authSession: ASWebAuthenticationSession?
    private var authBridgeReady = false
    private var authBridgeRetries = 0
    private weak var appWebView: WKWebView?

    // ── Push notifications (APNs) ──────────────────────────────────────────
    // O site (app-mode.js) pede o registro via postMessage("register") no
    // handler "pbPush". O device token chega em didRegister... e é entregue de
    // volta pro WebView (window.PBPush.onToken), que faz o POST autenticado em
    // /api/push/register reusando os cookies de sessão.
    private var pendingPushToken: String?
    #if DEBUG
    private let apnsEnvironment = "sandbox"
    #else
    private let apnsEnvironment = "production"
    #endif

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        return true
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        backgroundedAt = Date()
        showLockCover()
    }

    func applicationDidBecomeActive(_ application: UIApplication) {
        hideScrollIndicators()
        setupAuthBridge()
        deliverPushTokenToWeb()
        if unlocked, let t = backgroundedAt, Date().timeIntervalSince(t) > gracePeriod {
            unlocked = false
        }
        backgroundedAt = nil
        if unlocked {
            hideLockCover()
        } else {
            showLockCover()
            authenticate()
        }
    }

    // Indicador nativo de rolagem do WKWebView desligado — app não é site.
    // Idempotente; roda a cada ativação porque o WebView nasce depois do launch.
    private func hideScrollIndicators() {
        func walk(_ view: UIView) {
            if let wk = view as? WKWebView {
                wk.scrollView.showsVerticalScrollIndicator = false
                wk.scrollView.showsHorizontalScrollIndicator = false
                return
            }
            view.subviews.forEach(walk)
        }
        if let root = window?.rootViewController?.view { walk(root) }
    }

    // Acha o WKWebView do Capacitor na hierarquia (nasce depois do launch)
    private func findWebView() -> WKWebView? {
        if let wv = appWebView { return wv }
        var found: WKWebView?
        func walk(_ view: UIView) {
            if found != nil { return }
            if let wk = view as? WKWebView { found = wk; return }
            view.subviews.forEach(walk)
        }
        if let root = window?.rootViewController?.view { walk(root) }
        appWebView = found
        return found
    }

    // Registra o handler JS "pbAuth" uma vez, quando o WebView existe.
    // No cold launch o WebView do Capacitor pode nascer DEPOIS do primeiro
    // didBecomeActive — sem retry a ponte só era registrada quando o app
    // voltava do background, e o botão Google caía no fluxo web (sem Face ID).
    private func setupAuthBridge() {
        if authBridgeReady { return }
        guard let wk = findWebView() else {
            if authBridgeRetries < 20 {
                authBridgeRetries += 1
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
                    self?.setupAuthBridge()
                }
            }
            return
        }
        wk.configuration.userContentController.add(self, name: "pbAuth")
        wk.configuration.userContentController.add(self, name: "pbPush")
        UNUserNotificationCenter.current().delegate = self
        authBridgeReady = true
    }

    // ── Push: pedido de autorização + registro no APNs ─────────────────────
    // Chamado quando o site (logado) manda pbPush("register"). Pede permissão
    // de notificação e, se concedida, registra no APNs. O token volta em
    // application(_:didRegisterForRemoteNotificationsWithDeviceToken:).
    private func registerForPush() {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .badge, .sound]) { granted, _ in
            guard granted else { return }
            DispatchQueue.main.async {
                UIApplication.shared.registerForRemoteNotifications()
            }
        }
    }

    // Entrega o device token pro WebView, que faz o POST autenticado. Se o
    // WebView ainda não existe (cold launch), segura em pendingPushToken.
    private func deliverPushTokenToWeb() {
        guard let token = pendingPushToken else { return }
        guard let wk = findWebView() else { return }
        let js = "window.PBPush && window.PBPush.onToken && window.PBPush.onToken('\(token)','\(apnsEnvironment)')"
        DispatchQueue.main.async {
            wk.evaluateJavaScript(js) { _, _ in }
        }
        // Não zera pendingPushToken: a entrega é idempotente (o backend faz
        // upsert por token) e permite re-entregar depois de navegações/reloads.
    }

    // Inicia o OAuth do Google no navegador nativo (Face ID/autofill/SSO).
    private func startGoogleLogin() {
        guard let url = URL(string: "\(siteBase)/auth/google/start?app=1") else { return }
        let session = ASWebAuthenticationSession(
            url: url, callbackURLScheme: "pigbankai"
        ) { [weak self] callbackURL, error in
            guard let self = self, let cb = callbackURL else { return } // cancelou/erro
            self.handleAuthCallback(cb)
        }
        session.presentationContextProvider = self
        // false = compartilha cookies/keychain do Safari → Face ID e "manter
        // conectado" do Google funcionam (ephemeral perderia isso).
        session.prefersEphemeralWebBrowserSession = false
        authSession = session
        session.start()
    }

    // pigbankai://auth?code=X → carrega /d/X no WebView (loga).
    // pigbankai://auth?onboarding=T → carrega /onboarding?token=T.
    private func handleAuthCallback(_ url: URL) {
        guard let comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return }
        let items = comps.queryItems ?? []
        let code = items.first(where: { $0.name == "code" })?.value
        let onboarding = items.first(where: { $0.name == "onboarding" })?.value

        var dest: URL?
        if let code = code, !code.isEmpty {
            dest = URL(string: "\(siteBase)/d/\(code)")
        } else if let t = onboarding, !t.isEmpty,
                  let enc = t.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
            dest = URL(string: "\(siteBase)/onboarding?token=\(enc)")
        }
        guard let target = dest, let wk = findWebView() else { return }
        DispatchQueue.main.async { wk.load(URLRequest(url: target)) }
    }

    private func authenticate() {
        if authInFlight { return }
        authInFlight = true
        retryButton?.isHidden = true

        let ctx = LAContext()
        ctx.localizedFallbackTitle = "Usar código do iPhone"
        var err: NSError?
        guard ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err) else {
            // Aparelho sem código configurado: não tranca o usuário pra fora.
            authInFlight = false
            unlocked = true
            hideLockCover()
            return
        }
        ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Desbloqueie o PigBank") { ok, _ in
            DispatchQueue.main.async {
                self.authInFlight = false
                if ok {
                    self.unlocked = true
                    self.hideLockCover()
                } else {
                    self.retryButton?.isHidden = false
                }
            }
        }
    }

    @objc private func retryTapped() {
        authenticate()
    }

    private func showLockCover() {
        if lockWindow != nil { return }
        let w = UIWindow(frame: UIScreen.main.bounds)
        w.windowLevel = .alert + 1

        let vc = UIViewController()
        vc.view.backgroundColor = UIColor(red: 0x11 / 255.0, green: 0x11 / 255.0, blue: 0x11 / 255.0, alpha: 1)

        // Logo completa (marca + wordmark "PigBank"). Fallback pro texto se o
        // asset faltar, pra nunca ficar com a tela vazia.
        let logo = UIImageView(image: UIImage(named: "BrandLogo"))
        logo.contentMode = .scaleAspectFit
        logo.translatesAutoresizingMaskIntoConstraints = false

        let nameFallback = UILabel()
        if logo.image == nil {
            nameFallback.text = "PigBank"
            nameFallback.textColor = .white
            nameFallback.font = .systemFont(ofSize: 26, weight: .heavy)
        }
        nameFallback.translatesAutoresizingMaskIntoConstraints = false

        let btn = UIButton(type: .system)
        btn.setTitle("Desbloquear", for: .normal)
        btn.setTitleColor(.white, for: .normal)
        btn.titleLabel?.font = .systemFont(ofSize: 17, weight: .bold)
        btn.backgroundColor = UIColor(red: 0xFF / 255.0, green: 0x2D / 255.0, blue: 0x8E / 255.0, alpha: 1)
        btn.layer.cornerRadius = 24
        btn.contentEdgeInsets = UIEdgeInsets(top: 12, left: 32, bottom: 12, right: 32)
        btn.isHidden = true
        btn.addTarget(self, action: #selector(retryTapped), for: .touchUpInside)
        btn.translatesAutoresizingMaskIntoConstraints = false
        retryButton = btn

        vc.view.addSubview(logo)
        vc.view.addSubview(nameFallback)
        vc.view.addSubview(btn)
        NSLayoutConstraint.activate([
            logo.centerXAnchor.constraint(equalTo: vc.view.centerXAnchor),
            logo.centerYAnchor.constraint(equalTo: vc.view.centerYAnchor, constant: -40),
            logo.widthAnchor.constraint(equalToConstant: 216),
            logo.heightAnchor.constraint(equalToConstant: 64),
            nameFallback.centerXAnchor.constraint(equalTo: vc.view.centerXAnchor),
            nameFallback.topAnchor.constraint(equalTo: logo.bottomAnchor, constant: 8),
            btn.centerXAnchor.constraint(equalTo: vc.view.centerXAnchor),
            btn.topAnchor.constraint(equalTo: nameFallback.bottomAnchor, constant: 32),
        ])

        w.rootViewController = vc
        w.makeKeyAndVisible()
        lockWindow = w
    }

    private func hideLockCover() {
        lockWindow?.isHidden = true
        lockWindow = nil
        retryButton = nil
    }

    func applicationWillResignActive(_ application: UIApplication) {
    }

    func applicationWillEnterForeground(_ application: UIApplication) {
    }

    func applicationWillTerminate(_ application: UIApplication) {
    }

    // ── APNs: token recebido / falha ───────────────────────────────────────
    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        pendingPushToken = hex
        deliverPushTokenToWeb()
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        NSLog("[PigBank] Falha ao registrar push: \(error.localizedDescription)")
    }

    func application(_ app: UIApplication, open url: URL, options: [UIApplication.OpenURLOptionsKey: Any] = [:]) -> Bool {
        return ApplicationDelegateProxy.shared.application(app, open: url, options: options)
    }

    func application(_ application: UIApplication, continue userActivity: NSUserActivity, restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void) -> Bool {
        return ApplicationDelegateProxy.shared.application(application, continue: userActivity, restorationHandler: restorationHandler)
    }

}

// Recebe o postMessage("google") do app-mode.js e o pbPush("register")
extension AppDelegate: WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController,
                              didReceive message: WKScriptMessage) {
        if message.name == "pbAuth", (message.body as? String) == "google" {
            startGoogleLogin()
        } else if message.name == "pbPush", (message.body as? String) == "register" {
            registerForPush()
        }
    }
}

// Notificações com o app em primeiro plano + toque na notificação.
extension AppDelegate: UNUserNotificationCenterDelegate {
    // App aberto: ainda mostra o banner (senão o push some sem o usuário ver).
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound, .badge])
    }

    // Toque na notificação: leva pro app (WebView já carrega o /home logado).
    // Deep link por notificação fica pra fase futura (payload traria a rota).
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        completionHandler()
    }
}

// Âncora de apresentação do ASWebAuthenticationSession
extension AppDelegate: ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        return window ?? ASPresentationAnchor()
    }
}
