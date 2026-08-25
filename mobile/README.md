# PigBank — App iOS

Casca nativa (Capacitor) sobre o dashboard web. O app abre
`https://pigbankai.com/login` num WebView; cookies de sessão persistem entre
aberturas, e o `login.html` pula direto pra `/home` quando a sessão está viva.

## Arquitetura

- **Modo remoto** (`server.url` no `capacitor.config.json`): o WebView carrega o
  site de produção. Deploy do site = update do app, sem passar pela App Store
  (só mudanças nativas exigem novo build).
- **User agent** ganha o sufixo `PigBankApp/1.0` (`capacitor.config.json:18`).
  **Este sufixo sustenta o LAYOUT INTEIRO do app**, não só as regras da App
  Store: depois que o preview `?pbapp=1` foi removido, ele e o ícone instalado
  (`display-mode: standalone`) são os ÚNICOS gatilhos de `html.pb-app`
  (`frontend/app-mode.js`) e de `html.pb-safe` (`frontend/safe-area.js`) — sem
  ele o app abre com layout de site: nav, footer, sem tab bar, desenhando sob o
  notch. Quem for mexer aqui: os dois arquivos e o servidor
  (`frontend/routes/shared.py:548`) casam por SUBSTRING `PigBankApp`, então
  trocar o número da versão é seguro; trocar o NOME quebra tudo.
  Além do layout, o mesmo UA vira `window.PB_IN_APP` (`auth-refresh.js`), que
  esconde CTAs de upgrade e troca o redirect de paywall por tela neutra —
  exigência da diretriz 3.1.1 da App Store (sem link de compra externa dentro
  do app).
- **Regra de push** (Anexo 1 do contrato Apple): notificação NÃO pode conter
  valor, saldo ou dado financeiro — texto genérico, detalhe só dentro do app.
- Plugins instalados: `@capacitor/app`, `@capacitor/push-notifications`
  (registro do token é nativo no `AppDelegate`, não pelo plugin JS — o site
  remoto não carrega o runtime do Capacitor). Ver "Push notifications" abaixo.
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

- [x] Face ID lock — implementado direto no `AppDelegate.swift` via
      `LocalAuthentication` (sem plugin): pede Face ID/Touch ID/código no cold
      launch e ao voltar do background após 60s; cobre o conteúdo no app
      switcher. **Só chega ao aparelho com um build nativo novo**
      (`npx cap sync ios` + Archive no Xcode → TestFlight) — deploy do site
      não atualiza código Swift.
- [x] Login Google nativo (ASWebAuthenticationSession + scheme
      `pigbankai://`) — Face ID/autofill do Safari no botão "Entrar com
      Google". Também exige build nativo novo; num build antigo o botão cai
      no fluxo web dentro do WebView, sem Face ID.
- [x] Push notifications — **infra pronta, dormente até a APNs key existir**:
      - Entitlement `App/App.entitlements` (`aps-environment`), plugado no
        `project.pbxproj` (Debug+Release). **No Xcode ainda falta**: target App →
        Signing & Capabilities → **+ Capability → Push Notifications** (com
        assinatura automática ele provisiona o App ID sozinho).
      - `AppDelegate.swift`: handler `pbPush("register")` pede autorização e
        registra no APNs; o device token volta pro WebView via
        `window.PBPush.onToken(token, env)`.
      - Frontend `app-mode.js` (`wirePush`): só em app iOS logado, faz
        `POST /api/push/register` com o token (reusa cookies de sessão).
      - Backend: tabela `push_tokens`, `db/push.py`, rotas em
        `frontend/routes/push.py`, sender em `core/services/push_service.py`
        (APNs token-based, HTTP/2 via httpx+h2).
      - **Pra ligar o envio**, setar envs no Railway (gerar a APNs Auth Key .p8
        no portal Apple → Keys → +): `APNS_KEY_ID`, `APNS_TEAM_ID`
        (= `S849YDA49P`), `APNS_AUTH_KEY` (conteúdo do .p8), `APNS_TOPIC`
        (default `com.pigbankai.app`). Sem elas o sender é no-op.
      - **Regra Apple**: o texto do push NÃO pode conter valor/saldo (Anexo 1
        §4) — mandar texto genérico, detalhe só dentro do app.
- [ ] Conta de review da Apple: user demo com plano Pro ativo (grant manual).
- [ ] Ficha da App Store: screenshots, descrição, App Privacy (dados: e-mail,
      telefone, dados financeiros — vinculados à identidade; sem tracking).
