import UIKit
import Capacitor
import LocalAuthentication
import WebKit

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

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        return true
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        backgroundedAt = Date()
        showLockCover()
    }

    func applicationDidBecomeActive(_ application: UIApplication) {
        hideScrollIndicators()
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

        let logo = UILabel()
        logo.text = "🐷"
        logo.font = .systemFont(ofSize: 64)
        logo.translatesAutoresizingMaskIntoConstraints = false

        let name = UILabel()
        name.text = "PigBank"
        name.textColor = .white
        name.font = .systemFont(ofSize: 24, weight: .heavy)
        name.translatesAutoresizingMaskIntoConstraints = false

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
        vc.view.addSubview(name)
        vc.view.addSubview(btn)
        NSLayoutConstraint.activate([
            logo.centerXAnchor.constraint(equalTo: vc.view.centerXAnchor),
            logo.centerYAnchor.constraint(equalTo: vc.view.centerYAnchor, constant: -40),
            name.centerXAnchor.constraint(equalTo: vc.view.centerXAnchor),
            name.topAnchor.constraint(equalTo: logo.bottomAnchor, constant: 8),
            btn.centerXAnchor.constraint(equalTo: vc.view.centerXAnchor),
            btn.topAnchor.constraint(equalTo: name.bottomAnchor, constant: 32),
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

    func application(_ app: UIApplication, open url: URL, options: [UIApplication.OpenURLOptionsKey: Any] = [:]) -> Bool {
        return ApplicationDelegateProxy.shared.application(app, open: url, options: options)
    }

    func application(_ application: UIApplication, continue userActivity: NSUserActivity, restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void) -> Bool {
        return ApplicationDelegateProxy.shared.application(application, continue: userActivity, restorationHandler: restorationHandler)
    }

}
