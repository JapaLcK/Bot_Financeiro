# PigBank — App iOS

Casca nativa (Capacitor) sobre o dashboard web. O app abre
`https://pigbankai.com/login` num WebView; cookies de sessão persistem entre
aberturas, e o `login.html` pula direto pra `/home` quando a sessão está viva.

## Arquitetura

- **Modo remoto** (`server.url` no `capacitor.config.json`): o WebView carrega o
  site de produção. Deploy do site = update do app, sem passar pela App Store
  (só mudanças nativas exigem novo build).
- **User agent** ganha o sufixo `PigBankApp/1.0`. O frontend usa isso
  (`window.PB_IN_APP` em `auth-refresh.js`) pra esconder CTAs de upgrade e
  trocar o redirect de paywall por tela neutra — exigência da diretriz 3.1.1
  da App Store (sem link de compra externa dentro do app).
- **Regra de push** (Anexo 1 do contrato Apple): notificação NÃO pode conter
  valor, saldo ou dado financeiro — texto genérico, detalhe só dentro do app.
- Plugins instalados: `@capacitor/app`, `@capacitor/push-notifications`
  (push ainda sem backend — fase 2).
- Dependências nativas via **Swift Package Manager** (sem CocoaPods).

## Comandos

```bash
npm install          # 1x por máquina
npx cap sync ios     # após mudar config/plugins
npx cap open ios     # abre no Xcode
```

## Build & assinatura

1. Xcode → target **App** → Signing & Capabilities → Team = conta Apple
   Developer do Lucas. Bundle id: `com.pigbankai.app`.
2. Rodar no simulador: esquema **App**, qualquer iPhone.
3. TestFlight: Product → Archive → Distribute → App Store Connect.

## Assets

- Ícone: `ios/App/App/Assets.xcassets/AppIcon.appiconset/` (1024², gerado do
  avatar `identidade/PIGBANK.WHATSAPP.png` via `sips`).
- Splash: `Splash.imageset` (2732², avatar 800px centrado em `#111111`).

## Pendências (fase 2)

- [ ] Push notifications: APNs key no Apple Developer → backend envia via
      `aioapns`/`httpx` + registro do token no login (plugin já no bundle).
- [ ] Face ID lock (plugin biométrico + `NSFaceIDUsageDescription` já no plist).
- [ ] Conta de review da Apple: user demo com plano Pro ativo (grant manual).
- [ ] Ficha da App Store: screenshots, descrição, App Privacy (dados: e-mail,
      telefone, dados financeiros — vinculados à identidade; sem tracking).
