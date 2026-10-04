# ADR 0005 — Como o app nativo abre o Pluggy Connect

Status: **proposto**, aguarda a decisão do dono. Spike da Fase 4 ("Open Finance e onboarding"), feito em 2026-10-02. Nada aqui muda código de produção: as mudanças de backend são propostas, cada uma vira PR próprio. **Atualizado depois do teste no iPhone (build 14, Nubank real): a recomendação mudou** — ver "Medido no iPhone" e "Decisão recomendada".

Convenção de prova: **[medido]** = rodei no simulador (iPhone 18 Pro, iOS 27, Expo Go 57.0.9) e vi o resultado; **[lido]** = li na documentação oficial da Pluggy ou no código da lib; **[hipótese]** = não provado; **[só no iPhone]** = o simulador não prova. **[medido no iPhone]** = o dono rodou a build 14 (TestFlight, produção, conta real, Nubank real) e o log do app mostrou o resultado.

## Contexto

No site, o Open Finance é um widget JavaScript no navegador: `openWidget` pede `POST /open-finance/{uid}/connect-token`, abre `new PluggyConnect({...})` e, no `onSuccess`, faz `POST /open-finance/{uid}/pluggy-item` (`frontend/open-finance-connect.js:562`, `:575`, `:557`). Só esse POST cria a conexão do lado do PigBank. O backend emite o token com `clientUserId`, `avoidDuplicates`, `webhookUrl` e `products`, **sem `itemId` e sem `oauthRedirectUri`** (`core/services/pluggy.py:302`); a rota é `frontend/routes/open_finance.py:1751`. O webhook `POST /open-finance/pluggy/webhook` (`:2199`) adota o item quando o navegador não volta.

O app novo não tem como carregar esse script. O plano da Fase 1 mandava provar o Pluggy Connect em React Native antes de qualquer tela; isso nunca tinha sido feito. As telas 5 (Conectar seu banco), 6 (Autorizando) e 7 (Organizando seus dados) dependem desta decisão.

Em produção o `onSuccess` do widget já se mostrou pouco confiável: um usuário só conectou por adoção via webhook, e três repetiram o fluxo com itens que morreram em `USER_INPUT_TIMEOUT` ~20 min depois. Parte era conector direto (Caixa, pede QR), parte era consentimento Open Finance não concluído no navegador.

## As três opções

**A. Biblioteca oficial `react-native-pluggy-connect`.** Uma WebView de `connect.pluggy.ai` mais o tratamento do OAuth.
**B. `react-native-webview` própria**, carregando o widget (script do CDN) com o token do nosso `connect-token`.
**C. Navegador do sistema** (`expo-web-browser`, `ASWebAuthenticationSession`, como o Google em `app/src/features/auth/google.ts:71`), abrindo uma página nossa que embute o widget e devolve o resultado por `pigbank://`.

### O que medi em cada uma

| | A (lib, OAuth no navegador) | A (lib, OAuth in-app) | B (WebView + CDN) | C (sessão do sistema) |
|---|---|---|---|---|
| Abre o widget no Expo Go 57 | sim [medido] | sim [medido] | sim [medido] | sim [medido] |
| Chega ao banco de teste (MockBank) | sim, no Safari do sistema [medido] | sim, na WebView do app [medido] | sim, dentro da WebView [medido] | sim, no mesmo sheet [medido] |
| Volta ao app depois do consentimento | pelo `oauthRedirectUri`: Safari pergunta "Abrir no Expo Go?" e o app recebe `…/pluggy-volta?itemId=…` [medido] | sem sair do app, o widget retoma sozinho [medido] | o redirect final da Pluggy disparou o deep link `…?itemId=…` com o app em primeiro plano (num consentimento que cancelei sem querer, então só vale para a volta) [medido] | o sheet fecha sozinho e `openAuthSessionAsync` devolve `type: success` com `…?itemId=…` [medido] |
| `onSuccess` entrega `itemData` | sim: `{id, status, executionStatus, connector}` [medido] | sim [medido] | não completei até o fim (a execução foi cancelada sem querer) | não usado: o resultado veio do redirect, antes da coleta terminar |
| Dependência nativa nova | `react-native-webview` | `react-native-webview` | `react-native-webview` | nenhuma (`expo-web-browser` já existe) |
| Backend novo | nenhum para abrir; `oauthRedirectUri` no token para a volta | nenhum | nenhum | **uma página nova** hospedando o widget + `oauthRedirectUri` |

> **Atenção:** a coluna "A (lib, OAuth in-app)" só vale para o banco de teste. No iPhone com o Nubank real o modo in-app **não funciona** (ver "Medido no iPhone").

Outras medições, todas no simulador:

- **A instala limpa no SDK 57.** `npx expo install react-native-webview` resolve 13.16.1, a versão que o Expo Go 57 traz (`bundledNativeModules.json`). `react-native-pluggy-connect@1.6.0` (publicada em 2026-07-29, MIT, mantida pela própria Pluggy) acrescentou 16 pacotes; `tsc --noEmit` do app passou e o bundle subiu com 1144 módulos em 5,4 s. Sem build nativo para rodar no Expo Go.
- **Latência do `onSuccess`.** Do `LOGIN_STEP_COMPLETED` até o `onSuccess`: 57,9 s (A, navegador) e 50,2 s (A, in-app). O redirect com o `itemId` chega antes: no consentimento concluído. Quem fecha o app nesse intervalo perde o `onSuccess`, que é o caso de produção. O `itemId` do redirect é a âncora mais cedo.
- **A API da Pluggy aceita `oauthRedirectUri`** `pigbank://pluggy-volta`, `pigbank:///pluggy`, `pigbank://pluggy-volta?a=1`, `exp://127.0.0.1:8081/--/pluggy-volta` e `https://pigbankai.com/app/pluggy-volta`; recusa com HTTP 400 `http://…`, `http://localhost…`, `myapp:/x` e `javascript:…` ("must be a valid HTTPS URL (not localhost) or a valid deep link").
- **O 400 do MockBank é intermitente e sem causa provada.** 2 das 6 execuções chegaram à página "BAD REQUEST" do banco de teste depois do login (A navegador sem `oauthRedirectUri`, e a 1ª em A in-app); 3 concluíram (A navegador, A in-app na 2ª tentativa, C) e 1 foi cancelada por engano (B). As que concluíram levaram até ~3 min entre abrir o login e o callback; as que falharam, pelo menos ~2,5 min e ~33 min. **[hipótese]** a interação do MockBank expira ou é de uso único; não distingue A de C.
- **O modo "atualizar item" existe na API.** `POST /connect_token` com `itemId` de item que não existe devolve 404 `item not found`; com um item real, é o caminho da doc "Updating an Item" (`updateItem` no widget).

### Leitura da documentação (lido)

- Guia de OAuth da Pluggy (`docs.pluggy.ai/en/docs/connect-widget/oauth-support`, lido em 2026-10-02): `oauthRedirectUri` "must be HTTPS or a deep link", não pode ser `localhost`; em React Native/Flutter "use your app's deep link scheme"; "the one thing that reliably goes wrong is the return trip" — navegadores móveis não fecham a janela sozinhos e, sem URI, o usuário fica numa página final sem caminho de volta.
- Changelog da Pluggy, v2026.07 (publicado em 2026-08-13): `react-native-pluggy-connect@1.5.0` adiciona `forceOauthInBrowser`; com `false`, o OAuth abre numa WebView dentro do app, "solving connections to regulated connectors that got stuck in `WAITING_USER_INPUT` until expiring on iOS". O padrão segue `true`. É o sintoma de `USER_INPUT_TIMEOUT` que vimos. **No iPhone, porém, o modo navegador concluiu com o Nubank sem esse fix** [medido no iPhone]; **[hipótese]** o que vimos em produção pode ser o usuário sem caminho de volta (sem `oauthRedirectUri`), não um travamento do modo navegador.
- O código da lib (`src/components/OauthWebView/OauthBrowser.tsx`, `OauthInAppWebView.tsx`) confirma: no modo navegador ela chama `Linking.openURL` e **não acompanha o resultado** — o Connect continua consultando o item. No modo in-app injeta um `window.opener.postMessage` e devolve o resultado ao Connect.
- Sandbox: o conector "Sandbox" tem o fluxo "Fluxo Open Finance" (CPF de teste `761.092.776-73`, login do banco de teste publicado na doc). `includeSandbox` é opção do widget; `POST /connect_token` não tem esse campo.

## Decisão recomendada

**Opção A: `react-native-pluggy-connect`, com o OAuth no navegador do sistema** (`forceOauthInBrowser={true}`, o padrão da lib) **mais `oauthRedirectUri` com scheme por ambiente** (proposta 1 do backend, agora necessária) e a proteção do `onSuccess` abaixo.

Por quê: é a única que funcionou de ponta a ponta com o Nubank real no iPhone [medido no iPhone]: o sistema abre o app do banco, o usuário autoriza, e o widget conclui. Falta só a volta ao app, que o `oauthRedirectUri` resolve (a Pluggy documenta que sem ele o usuário fica numa página final sem caminho de volta). É mantida pela dona do widget e não exige página nova no backend. A B é a A sem o tratamento do OAuth. A C funcionou no simulador e entregou o `itemId`, mas custa uma página nossa e o sistema mostra "PigBank quer usar `pigbankai.com` para entrar" antes do widget; fica como plano B.

**O modo in-app (`forceOauthInBrowser={false}`) está descartado para banco com app.** O Nubank manda o usuário ao app por link universal e uma WebView não faz esse salto: o iPhone voltou à tela anterior em loop. Fica só como alternativa para conectores com login pelo site, e ainda sem prova em banco real. Isso **inverte** a recomendação anterior deste ADR (in-app como ponto de partida), que se apoiava só no simulador e no changelog da Pluggy.

## O app nunca confia só no callback

1. Ao voltar do widget por qualquer caminho (`onSuccess`, `onError`, `onClose`, deep link com `itemId`), o app consulta o servidor: `GET /open-finance/{uid}` (`frontend/routes/open_finance.py:1630`, devolve as conexões com `provider_item_id`, `status` e o estado exibível `ui`).
2. Se tiver o `id` do item, faz `POST /open-finance/{uid}/pluggy-item` (`:1841`). A rota só aproveita o `id` e confere o dono na Pluggy (`clientUserId` bate com a sessão), então um id vindo do app não dá poder a ninguém.
3. Sem `id` (usuário saiu do app, `onSuccess` perdido), a conexão aparece quando o webhook adota o item **[medido no iPhone, com o app em segundo plano]**; só o evento `item/created` adota (`frontend/routes/open_finance.py:1146`), então, se ele se perder, nenhum evento depois recupera o item e o usuário fica com 0 bancos sem erro visível — por isso o passo 2 existe. O app consulta o `GET` em intervalo curto por uma janela limitada e mostra "Estamos conferindo com o banco" em vez de erro. **[hipótese]** 3 s por até 5 min (medido no iPhone: `updating` até ≥ ~89 s); não medi o tempo do webhook, e o valor entra no PR das telas com medição em staging.
4. Antes de abrir o widget, `GET /open-finance/{uid}/limite` (`:1812`) diz se cabe um banco novo.

## O que o backend precisa (propostas, cada uma um PR na faixa Completo)

1. **(necessária) `oauthRedirectUri` no `connect-token`**, hoje ausente (`core/services/pluggy.py:302`). Para o app, `pigbank://open-finance-volta`; para o site, nada muda (parâmetro opcional). A API aceita o formato (medido acima). **O scheme tem de ser por ambiente.** Dev, staging e produção convivem no mesmo iPhone (`app/app.config.ts:3-15`) e hoje todos registram o mesmo `pigbank` (`:52`); quando o redirect é resolvido pelo sistema (Safari → app), o iOS não define qual deles abre, e o errado não completa o fluxo. Derivar o scheme do ambiente (ex.: `pigbank-staging`) ou usar link universal por ambiente. Não afeta o modo in-app nem a C: ali o retorno não passa pelo sistema (o `openAuthSessionAsync` casa o scheme da própria sessão, como no Google).
2. **`itemId` opcional no `connect-token`** para reconectar um banco existente, hoje impossível: com o Nubank já conectado, o widget recusa com `onError` "already exists" (o token vai com `avoidDuplicates: true`, `core/services/pluggy.py:306`) [medido no iPhone]; pela doc o widget então recebe `updateItem`. Precisa validar que o item é do usuário.
3. **(só se a C for escolhida)** uma página hospedada que embute o widget e redireciona para `pigbank://…`.
4. **(só se um dia usar link universal em vez de scheme)** `applinks` no `apple-app-site-association` (hoje só `webcredentials`, `frontend/routes/static_pages.py:471`) e `associatedDomains` com `applinks:` em `app/app.config.ts:66`. O scheme `pigbank` já existe (`app/app.config.ts:52`). O link universal evitaria a pergunta "Abrir no app?" que o Safari faz para scheme customizado [medido com `exp://`; com `pigbank://` é **hipótese**, o Expo Go não registra esse scheme].

## Medido no iPhone (build 14, Nubank real)

Build 14 enviada ao TestFlight a partir de um branch descartável (a tela de teste e as dependências **não** estão na main), apontando para produção, na conta do dono. Resultados, na ordem em que aconteceram:

1. **Nubank já conectado → o widget recusa.** `onError` "already exists"; o widget fechou antes de abrir o app do banco; o servidor seguiu com a mesma conexão (`[active] cbef5c6b`). Causa: `avoidDuplicates: true` no token. Isso prova que **hoje não há como reconectar** um banco já conectado (proposta 2).
2. **OAuth dentro do app: não funciona com o Nubank.** Depois de o dono desconectar o Nubank (o desconectar reverte os lançamentos importados e apaga os cartões criados automaticamente, `db/open_finance.py:3249`), o fluxo chegou na "Verificação de Segurança", em "Conectar", na tela do Nubank com "ir ao app"; tocar nela devolveu a tela anterior, em **loop**.
3. **OAuth no navegador do sistema, sem `oauthRedirectUri`: conclui, mas não volta ao app.** O Nubank abriu pelo sistema, o dono autorizou e **caiu numa página do Safari ("Pronto, você pode fechar") sem caminho de volta**; voltou pelo seletor de apps. O log mostra `onSuccess` em +103,9 s com `{id c13cb883…, status UPDATED, executionStatus PARTIAL_SUCCESS, connector {id 612, name Nubank}}`, e o `GET /open-finance/{uid}` lista `Nubank [UPDATED] c13cb883` 0,3 s depois; o polling retornava 0 conexões até +87,7 s.
4. **Adoção pelo webhook.** O dono nunca tocou em "Registrar item" e a tela de teste não chama `POST /pluggy-item` sozinha, então a conexão chegou ao servidor **pelo webhook**, com o app em segundo plano (não finalizado).
5. **Rede cai ao retomar do segundo plano.** Um `GET` do polling falhou com "A conexão de rede foi perdida" em +103,0 s, ao voltar ao app: falha de rede no polling é "tente de novo", nunca erro final.

**Se o usuário sai da tela no meio** (ordem do fluxo; o ponto de corte é a **criação do item**, não a autorização no banco):
- **Antes de o item existir** (ainda escolhendo banco/CPF no widget): nada foi criado; basta conectar de novo.
- **Item criado, banco ainda não autorizado:** o `item/created` já dispara e o webhook adota na hora a conexão ainda `UPDATING`, incompleta (`tests/test_of_webhook_adopt.py:202-220`; a ordem "webhook antes do `onSuccess`" está documentada em `frontend/routes/open_finance.py:2026-2029`; **[lido]**, não medido no iPhone). Ela fica esperando o usuário e expira por tempo (`USER_INPUT_TIMEOUT`, ~20 min). **Tocar em "conectar de novo" bate em `avoidDuplicates` → "already exists"** [medido no iPhone, item 1]. A tela tem de mostrar a conexão incompleta e oferecer **continuar/reautorizar** (o `updateItem` da proposta 2), nunca um "conectar de novo" cego.
- **Depois de autorizar:** a coleta continua na Pluggy e o PigBank já tem a conexão; o app mostra "Estamos organizando seus dados", não erro.

**Só no iPhone ainda:** que `oauthRedirectUri=pigbank://…` devolve o usuário ao app sozinho (precisa da proposta 1 e de uma build nova) e o app fechado de vez (finalizado) logo depois de autorizar, que o webhook deveria cobrir igual mas não foi medido.

## Riscos

- **`onSuccess` perdido** (descrito acima): sem o polling no servidor, quem fecha o app logo depois de autorizar fica sem conexão na tela.
- **Volta ao app depende do `oauthRedirectUri`.** Sem ele o usuário fica no Safari [medido no iPhone]; com o scheme igual em todos os ambientes, o sistema pode abrir o app errado (abaixo).
- **Conexão incompleta que o webhook já adotou** (item criado, banco não autorizado): sem a proposta 2, o usuário que sai no meio do fluxo não consegue retomar, porque o novo token recusa por `avoidDuplicates`.
- **`item/created` perdido:** nenhum evento depois adota o item (`frontend/routes/open_finance.py:1146`); a rede de proteção é o `POST /pluggy-item` com o `id` do item.
- **A lib carrega `connect.pluggy.ai` ao vivo.** O site fixa a v2.7.0 do script com SRI (`frontend/settings.html:14`); no app ganhamos as correções sem republicar, mas perdemos o travamento de versão e o que a Pluggy mudar chega sem passar por nós.
- **Scheme `pigbank` igual em todos os ambientes** (`app/app.config.ts:52`): só pesa no modo navegador com `oauthRedirectUri` (proposta 1), onde o sistema escolhe o app; resolvido com scheme por ambiente.
- **Dependência nativa nova:** `react-native-webview` exige build nativo (não vai por OTA); a lib depende de `pluggy-connect-sdk@2.9.2` (só tipos e protocolo).

## Não verificado

- Android: a lib e a WebView prometem suporte (peer `react-native-webview` ≥ 11.6; tratamento do botão voltar nos dois modos), mas não há build de Android e o primeiro build depende da #604. Nenhuma afirmação aqui vale para Android.
- Outro banco além do Nubank, e o OAuth em iPhone físico com `oauthRedirectUri`.
- App fechado de vez logo depois de autorizar (só medi em segundo plano).
- O caminho B até o `onSuccess` (cancelei o consentimento sem querer na única passada); ele também roda numa WebView, então herda a limitação do modo in-app com bancos que abrem o app.
- O tempo de adoção pelo webhook (medi que acontece, não quando).
- O `pigbank://` real (só `exp://` entrou em execução: o Expo Go não registra `pigbank://`).

Os itens criados no sandbox da Pluggy durante o spike (`clientUserId=spike-descartavel`) não foram apagados; a doc da Pluggy diz que itens de sandbox sem atualização por 30 dias são removidos.

## Decisões que ficam com o dono

1. Aprovar a recomendação revista: lib oficial com OAuth no navegador do sistema e `oauthRedirectUri` (o in-app sai como ponto de partida).
2. Abrir **já** o PR do backend da proposta 1 (faixa Completo; scheme por ambiente) e uma build 15 para provar a volta automática no iPhone, antes das telas 5 a 7.
3. A proposta 2 (`itemId`, continuar/reconectar) **junto com as telas 5 a 7**, não depois: sem ela, quem sai no meio do fluxo não consegue retomar a conexão que o webhook já adotou.
4. Android na Fase 4 ou depois (hoje fica sem prova).
5. Se vale repetir o teste com o app fechado de vez (exige outro banco, ou desconectar o Nubank de novo).
