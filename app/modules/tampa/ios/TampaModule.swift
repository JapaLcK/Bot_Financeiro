import ExpoModulesCore
import UIKit

// Os mesmos `claro.bg` e `escuro.bg` de `src/ui/tokens.ts`
// (`__tests__/modules/tampa_espelho.test.ts` compara os dois lados).
let fundoClaro = 0xFFFFFF
let fundoEscuro = 0x0E0E10

private func cor(_ hex: Int) -> UIColor {
  UIColor(
    red: CGFloat((hex >> 16) & 0xFF) / 255,
    green: CGFloat((hex >> 8) & 0xFF) / 255,
    blue: CGFloat(hex & 0xFF) / 255,
    alpha: 1
  )
}

/// Tampa de privacidade: cobre o app de forma SÍNCRONA quando ele perde o foco.
///
/// Existe porque a cobertura desenhada pelo JS chega tarde: o iOS começa a
/// animação do bloqueio e captura a foto do app switcher no mesmo instante do
/// `willResignActive`, e o render do React só aparece ~140 ms depois (medido no
/// simulador). Aqui a tampa sobe dentro da própria notificação.
///
/// Quem TIRA a tampa é o JS (`descobrir()`), nunca o `didBecomeActive`: no
/// bloqueio de tela o iOS manda um `active` de passagem entre dois `inactive`, e
/// o JS só descobre depois de a trava decidir o que vai por baixo.
public class TampaModule: Module {
  private var tampa: UIView?
  private var coberta = false
  /// Os NOSSOS prompts de Face ID também tiram o foco do app; neles a tampa não sobe.
  private var pular = false
  private var remocao: DispatchWorkItem?
  private var observadores: [NSObjectProtocol] = []
  private let atrasoDescobrirMs = 0

  public func definition() -> ModuleDefinition {
    Name("PigBankTampa")

    // `queue: nil`: o bloco roda na hora, na thread que postou (a main). `self`
    // forte, como em toda closure da DSL do Expo: os observadores saem no OnDestroy.
    OnCreate {
      let centro = NotificationCenter.default
      observadores = [
        centro.addObserver(forName: UIApplication.willResignActiveNotification, object: nil, queue: nil) { _ in
          self.remocao?.cancel()
          // Com `pular`, não cobre — mas também não descobre o que já estava coberto.
          if !self.pular { self.mostrar() }
        },
        centro.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: nil) { _ in
          self.remocao?.cancel()
          // Sempre, mesmo durante um prompt: é a foto do app switcher.
          self.mostrar()
          self.pular = false
        },
      ]
    }

    OnDestroy {
      observadores.forEach { NotificationCenter.default.removeObserver($0) }
      observadores = []
      DispatchQueue.main.async { [self] in
        esconder()
        tampa = nil
      }
    }

    AsyncFunction("descobrir") {
      guard coberta else { return }
      remocao?.cancel()
      let item = DispatchWorkItem {
        // Se o app já perdeu o foco de novo quando isto roda, a tampa fica.
        guard UIApplication.shared.applicationState == .active else { return }
        self.esconder()
      }
      remocao = item
      DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(atrasoDescobrirMs), execute: item)
    }.runOnQueue(.main)

    AsyncFunction("pular") { (valor: Bool) in
      pular = valor
    }.runOnQueue(.main)
  }

  private func mostrar() {
    guard let janela = janelaDoApp() else { return }
    let v = tampa ?? criarTampa()
    tampa = v
    v.frame = janela.bounds
    // Por cima de TUDO na janela do app, inclusive as sheets já apresentadas.
    janela.addSubview(v)
    // Desenha AGORA, antes de a notificação voltar para o sistema.
    CATransaction.flush()
    coberta = true
  }

  private func esconder() {
    tampa?.removeFromSuperview()
    coberta = false
  }

  /// A janela onde o RN desenha. Uma `UIWindow` própria mostrada no resign NÃO
  /// entra na animação do bloqueio de tela (medido no simulador: o conteúdo
  /// escurecia inteiro por baixo); uma view nova na janela do app entra.
  private func janelaDoApp() -> UIWindow? {
    let janelas = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
    return janelas.first(where: \.isKeyWindow) ?? janelas.first { $0.windowLevel == .normal && !$0.isHidden }
  }

  private func criarTampa() -> UIView {
    let raiz = UIView()
    raiz.autoresizingMask = [.flexibleWidth, .flexibleHeight]
    raiz.backgroundColor = UIColor { $0.userInterfaceStyle == .dark ? cor(fundoEscuro) : cor(fundoClaro) }
    raiz.accessibilityViewIsModal = true

    // Sem o símbolo (asset faltando), só o fundo: cobre do mesmo jeito.
    if let imagem = UIImage(named: "tampa_simbolo", in: Bundle(for: TampaModule.self), with: nil) {
      let simbolo = UIImageView(image: imagem)
      simbolo.contentMode = .scaleAspectFit
      simbolo.translatesAutoresizingMaskIntoConstraints = false
      raiz.addSubview(simbolo)
      let area = raiz.safeAreaLayoutGuide
      NSLayoutConstraint.activate([
        simbolo.widthAnchor.constraint(equalToConstant: 60),
        simbolo.heightAnchor.constraint(equalToConstant: 64),
        simbolo.centerXAnchor.constraint(equalTo: area.centerXAnchor),
        simbolo.centerYAnchor.constraint(equalTo: area.centerYAnchor),
      ])
    }
    return raiz
  }
}
