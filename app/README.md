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

`APP_ENV` escolhe o id e o nome do binário, e é o que permite dev, staging e
produção conviverem no mesmo aparelho.

**Variável de ambiente só vem de arquivo em desenvolvimento.** O Expo escolhe o
arquivo de `.env` pelo `NODE_ENV`, não pelo `APP_ENV`, então um `.env.staging`
não seria lido e o app rotulado como staging apontaria para o backend de
desenvolvimento sem avisar. Staging e produção declaram as variáveis no `env` do
perfil do `eas.json` ou no ambiente do build.

`EXPO_PUBLIC_API_URL` é obrigatória fora de `development`: sem ela a config
falha na geração, antes de existir binário. Sentry e PostHog são no-op sem
chave — o projeto roda sem nenhuma configuração externa.
