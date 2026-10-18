# Plano: push notification no app (Expo)

Medido na `main` em c6c16fda (2026-10-18), por leitura de código. Nada foi aberto em aparelho.
Remeça antes de reusar: os comandos estão em "Como medi".

Este plano existe para ninguém refazer o que o servidor já tem, e para a tela de preferências do app
(#1121) e o push não colidirem. **O código só começa com as decisões da seção 5 respondidas pelo dono.**

## 1. O que push cobre e o que não cobre

Cobre:

- o app Expo (`app/`) pedir a permissão do sistema, registrar o token APNs do aparelho na conta logada e
  baixá-lo quando a conta sai do aparelho;
- o servidor enviar alerta para os aparelhos de UMA conta, com texto genérico (sem valor, saldo nem dado
  financeiro na tela de bloqueio);
- o toque na notificação abrir a tela certa do app;
- uma preferência por tipo de push no servidor, para o usuário desligar sem ir aos Ajustes do iPhone.

Não cobre (fora de escopo, cada um pede conversa própria):

- Android (FCM) e web push no `/painel`;
- as 7 preferências de e-mail, WhatsApp e resumos do app: são a #1121;
- push silencioso (atualizar dado em segundo plano), selo no ícone, imagem, ações na notificação e push de
  marketing;
- o app Capacitor antigo (`mobile/`): ele já tem o fio dele (seção 3) e não ganha nada novo.

## 2. Relação com a #1121

A #1121 (preferências de notificação do app, par do C2 do `/painel`) é trabalho separado e **não depende
do push**; o push também não depende dela. Os dois podem andar em paralelo, com uma regra:

- **uma tela só** de Notificações nas Configurações do app. Quem chegar primeiro cria a tela; o outro
  acrescenta a seção dele. A #1121 deve prever uma seção "No celular" (o estado da permissão do sistema e
  as chaves de push da decisão D5), mesmo que nasça vazia;
- a preferência de push mora no **mesmo** `GET`/`PATCH /settings/{uid}/notifications`
  (`frontend/routes/settings.py`), como campo novo. O servidor só acrescenta campo: app instalado não
  atualiza com o deploy, e o `/painel` ignora o que não conhece;
- no `/painel` o push é específico da plataforma: o C2 não mostra chave de push (não há web push).

## 3. O que já existe no servidor (não reescrever)

| Peça | Onde | Estado |
|---|---|---|
| Envio APNs direto (HTTP/2, JWT ES256 com a `.p8`, cache do token) | `core/services/push_service.py::send_push_to_user` | Dormente sem `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_AUTH_KEY`: só loga e devolve 0. Apaga o token em 410 e em 400 `BadDeviceToken`/`Unregistered`. Não confere o texto: quem chama garante o genérico |
| Registro do token | `POST /api/push/register` (`frontend/routes/push.py`) | Aceita Bearer (o app novo já autentica assim: `tests/_apoio_auth_app.py` usa esta rota como alvo) e cookie. Sem gate de plano, com a perna da senha (`exigir_credencial`). `platform` `ios`/`android`, `environment` `sandbox`/`production`; valor fora vira o padrão em silêncio |
| Baixa do token | `POST /api/push/unregister` | Exige sessão, mas apaga **pelo token, de qualquer conta** (não filtra por `user_id`) |
| Tabela | `push_tokens` (`db/schema.py`, `db/push.py`) | `token` único: o mesmo aparelho re-registrando muda o dono. `environment` por token. Sai com a conta (cascade) e no "Recomeçar do zero" |
| Quem envia hoje | `core/admin_dashboard.py` (teste do admin) e `scripts/push_test.py` | **Nenhum evento de produto dispara push** |
| Cliente antigo | `mobile/ios/App/App/AppDelegate.swift` + `frontend/app-mode.js::wirePush` | O AppDelegate entrega o token ao WebView, que chama o register com cookie |

Achados que mudam o desenho:

- **Tópico.** `APNS_TOPIC` é um só por servidor, padrão `com.pigbankai.app` (o id do Capacitor). O app Expo
  é `com.pigbankai.mobile` (`.dev`, `.staging` nos outros ambientes, `app/app.config.ts`). Com o padrão,
  todo push para o app novo volta `DeviceTokenNotForTopic` (400, hoje só um warning; o token não é apagado).
  Decisão D3.
- **Baixa sem dono.** O unregister de qualquer conta apaga o token de outra. O token é segredo do aparelho,
  então o risco é baixo, mas a correção é uma cláusula (`and user_id = %s`).
- **Logout não baixa token.** `POST /auth/logout` só revoga sessão e refresh. Se o app não conseguir chamar
  o unregister antes (sem rede, sessão já expirada), o token segue na conta que saiu até outra conta
  registrar o mesmo aparelho. Decisão D4.

## 4. O que falta no app (escopo provável)

- dependência `expo-notifications` (e `expo-application`, se a D3 precisar ler o ambiente APNs), com o
  plugin no `app.config.ts`: mexe no nativo (capability de push, `aps-environment`), só chega num build novo;
- pedir permissão no momento da D2, nunca na primeira abertura; recusa não é perguntada de novo pelo app
  (o iOS não deixa), só pela chave da tela de Notificações, que manda aos Ajustes;
- registrar com `platform: "ios"` e o `environment` real do binário (build de desenvolvimento = `sandbox`;
  TestFlight e loja = `production`), depois do login e a cada abertura (o token muda);
- baixar no Sair e na troca de conta, ANTES de revogar a sessão;
- tratar o toque (D6): abrir a rota do payload, esperando a trava biométrica (`features/bloqueio`) liberar;
- o envio do lado do servidor para o(s) evento(s) da D1, com a preferência da D5.

Cuidados duros (valem em todo PR deste plano):

1. **Isolamento por usuário.** O token é do aparelho e o aparelho troca de conta. Push da conta A nunca
   pode chegar com a conta B logada (D4).
2. **Consentimento.** Nada é registrado nem enviado sem o usuário aceitar a permissão do sistema.
3. **Texto genérico** na tela de bloqueio: sem valor, saldo, nome de banco ou de estabelecimento.
4. **Prova.** O simulador não recebe APNs real; registro, entrega e toque só se provam em build no
   aparelho. Todo relato separa o verificado aqui do que só vale no aparelho.

Faixa: **Completo** (sessão, token por usuário, consentimento).
Par no outro lado: específico da plataforma (APNs exige app nativo; web push fora de escopo).

## 5. Decisões abertas (do dono)

| # | Pergunta | Recomendação |
|---|---|---|
| D1 | Que evento dispara o primeiro push? | **Um só no começo:** banco que precisa reconectar (o job de saúde do Open Finance já grava o estado; é acionável e não tem valor), um push por mudança de estado e não a cada volta do job. Candidatos depois: receita detectada a confirmar, alerta do Xerife, lembrete de pagamento |
| D2 | Quando pedir a permissão? | Uma oferta própria do app (tela ou cartão com "Ativar" e "Agora não") depois de conectar o primeiro banco, mais a chave na tela de Notificações. O pedido do sistema só sai do "Ativar" |
| D3 | Como acertar o tópico do app novo? | Trocar `APNS_TOPIC` por servidor (`com.pigbankai.mobile` em produção, `.staging` no staging) e tratar `DeviceTokenNotForTopic` como token inválido (apagar). Custo: o Capacitor deixa de receber push, e hoje nada de produto envia push para ele. A outra opção é guardar o tópico por token (coluna nova) |
| D4 | Como garantir que o token sai com a conta? | Amarrar o token à sessão (`jti`) e enviar só para token de sessão ativa. Fecha de uma vez o logout sem rede, o "encerrar outros dispositivos", a troca de senha e a exclusão. É migração (coluna nova). A opção mínima é só o unregister no Sair, com o risco do logout sem rede |
| D5 | Qual preferência controla o push? | Um campo por tipo de push no `/settings/{uid}/notifications`, ligado por padrão (o consentimento é a permissão do sistema). Começa com um campo, o do D1 |
| D6 | Para onde o toque leva? | Para a tela do evento (D1: Bancos conectados). O payload leva só o nome da rota, nunca dado |
| D7 | Texto do primeiro push | Proposta: título "PigBank", corpo "Um banco precisa de atenção. Toque para ver." (sem nome do banco). O dono aprova o texto final |

## 6. Ordem dos PRs (depois das decisões)

Separados por verificabilidade (raiz §4):

1. **Servidor** (provável aqui): D3 (tratar `DeviceTokenNotForTopic`), a baixa com dono, D4, o campo da D5
   e o envio do evento da D1. Prova por pytest e pela rota.
2. **App**: `expo-notifications`, permissão (D2), registro e baixa, toque (D6). Prova no build do
   aparelho; o servidor do PR 1 já tem de estar no ar (app instalado não espera deploy).
3. Ligar as envs do APNs no Railway e conferir no aparelho: ato operacional do dono.

## Como medi

```bash
git fetch origin && git log -1 --format=%h origin/main
grep -rnE "send_push_to_user|list_push_tokens|api/push" --include='*.py' --include='*.js' --include='*.swift' . | grep -v node_modules
grep -n "ID_BASE\|sufixoId" app/app.config.ts
grep -n "expo-notifications" app/package.json
grep -n '"/auth/logout"' -A40 frontend/finance_bot_websocket_custom.py | grep -n push
```

Não verificado: se as envs do APNs existem no Railway hoje; se algum aparelho tem token gravado em produção.
