# PigBank Mobile 2.0

App nativo (Expo/React Native), reconstruído do zero. Substitui o `mobile/`
(casca Capacitor sobre o site) — **mas só na Fase 12**: até lá os dois
convivem, e o app antigo continua atendendo quem não atualizou.

O plano completo (discovery, arquitetura de informação, 35 telas, 12 fases)
está fora do repositório, com o dono.

## Estado: Fase 1 — fundação

O que existe aqui hoje é a fundação técnica, não produto: cliente de API,
sessão, erro, analytics e uma tela que prova a ponta a ponta. Nenhuma tela de
produto entra antes da Fase 3.

## Rodar

```bash
cd app
npm install
npm run lint && npm run typecheck && npm test
npx expo start          # precisa de um backend acessível (ver .env.example)
```

## O que o app NÃO faz

- **Não usa cookie.** A sessão é por token (`Authorization: Bearer`), guardado
  em `expo-secure-store`. O servidor não manda `Set-Cookie` para quem envia
  `X-PigBank-Client: app` — e isso é de propósito: o `fetch` do React Native
  tem cookie jar ligado por padrão, então um cookie guardado sem querer faria a
  escrita seguinte falhar no CSRF.
- **Não guarda escrita offline.** App alimentado por Open Finance é leitura;
  cache de leitura com carimbo de "atualizado há X" resolve. Fila de escrita
  offline é onde app financeiro perde dinheiro do usuário.
- **Não fala com o WebSocket** do backend. Refetch ao voltar ao foreground, e
  push quando um sync termina, cobrem o caso real sem exigir cookie jar.

## Ambientes

`app.config.ts` lê `.env.<ambiente>`. Ver `.env.example`. Sentry e PostHog são
no-op sem chave — o projeto roda sem nenhuma configuração externa.
