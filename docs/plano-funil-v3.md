# Funil de venda pelo quiz: plano v3 (conta antes de pagar, Stripe embutido)

Faixa **Completo** (criação de conta, sessão, dinheiro). Lido contra `origin/main`
(ef472f8) em 2026-09-27. Os trechos citados são da main.

**Revisão 2 (2026-09-27).** Incorpora as respostas do dono às 6 perguntas (seção 11) e
os 4 apontamentos da revisão cruzada do Manager: o `next=` sem encoding no MFA; o plano
B para o Stripe embutido em WebView (teste real antecipado + queda automática para o
checkout hospedado); o terceiro chamador do bloco de sessão; e a citação correta da
proteção de telefone disputado.

**Estado em 2026-09-29 (leia antes do resto).** Este plano é a base, não a especificação.
O código da `main` vence o texto quando os dois divergem.
- **PR 1 (conta na `/assinar`) = #658, mergeado.** A revisão do Codex mudou quatro coisas
  que o texto abaixo não diz:
  - `boas_vindas_da_conta` só roda depois de a sessão sair;
  - se a sessão falha, `desfazer_conta_sem_codigo` apaga a conta pela PK (só se estiver sem
    senha e sem plano) e a rota responde 503;
  - a limpeza de sessões só roda quando nenhuma outra linha do `user_id` sobrou;
  - `/auth/register` e `/auth/verify-email` chamam o banco via `asyncio.to_thread`.
- **Etapa 0 PARCIAL.** O embutido funciona no Instagram iOS (ver a última seção), mas os
  itens 2, 4 e 5 e a lista de hosts da CSP do item 6 **nunca foram medidos**. Eles são
  o portão do PR 2 (ver lá).
- **O texto do PR 1 e a D-c abaixo descrevem o desenho ANTIGO** (`create/confirm_email_verification`).
  O #658 entrou com `criar_conta_sem_codigo` + `inserir_conta_nova` + `trava_email`, sem
  `email_verification_codes`. O código da `main` e a nota "Ajustes do PR 1" (no fim)
  mandam.
- **Próximo: PR 2** (checkout embutido e hospedado, mais a CSP). Depois vêm os PRs 3 a 6,
  na ordem da seção 5.
- A etapa 0b-2 (túnel antes do merge do PR 5) continua pendente.
- **Decisões do dono de 2026-09-29:**
  - o "Crie sua senha" do PR 4 bloqueia no SERVIDOR (403 nas rotas de dados), e não só
    na tela. Ver o item 2b do PR 4;
  - o e-book do PR 3 sai por uma entrega pendente mais um job de nova tentativa, e não
    pelo 5xx do webhook;
  - (2026-09-30) o job só envia o e-book **depois que o e-mail foi provado**, com a
    senha criada ou Google/Apple.
- **Auditoria de 2026-09-29:** o plano inteiro foi conferido contra a `main` e o SDK do
  Stripe instalado, com 14 achados corrigidos neste arquivo. O que o plano ainda afirma
  sem ter medido está marcado como "medir" (a Etapa 0 parcial e o desconto da linha de
  fatura).

---

## 1. Resumo para o dono (uma tela)

**Como fica para quem compra:**
1. Faz o quiz no XQuiz, deixa nome, e-mail e WhatsApp, vê o resultado e clica num plano.
2. Cai em **pigbankai.com/assinar**, com os dados já preenchidos. Pode corrigir. Marca
   "aceito os termos" e clica em **Continuar**.
   - **E-mail novo:** a conta nasce na hora, sem senha e sem código, e a pessoa fica
     logada naquele navegador.
   - **E-mail que já tem conta:** "Você já tem conta. Entre com sua senha", ali mesmo,
     com "Entrar com Google" e "Não tenho ou esqueci a senha".
   - **Já é assinante** (só aparece depois de entrar): "Você já é assinante" e um botão
     para o painel.
3. Na mesma página aparece o **formulário do Stripe embutido**: cartão, a caixinha do
   e-book e o e-mail travado. Antes de pagar, a página diz se tem **15 dias grátis** ou
   não. Ao lado fica "Prefere Pix? Plano anual", que leva ao Pix que já existe.
4. Pagou, cai no **painel** com o perfil do quiz. Um aviso que não dá para fechar pede
   **"Crie sua senha"**, com um link mandado para o e-mail (decidido pelo dono). Isso
   também prova que o e-mail é dela.
5. **Se o formulário embutido não abrir** (por exemplo, dentro do navegador do
   Instagram), a mesma página manda a pessoa para a página de pagamento do Stripe, com
   a mesma conta, o mesmo teste grátis e o mesmo e-book. Só nesse caso o endereço sai do
   pigbankai.com durante o pagamento.

**O que muda no sistema:** uma página nova, uma rota que cria a conta, o checkout que
já existe ganha o modo embutido, o webhook do Stripe aprende a entregar o e-book, e o
painel ganha o aviso de senha. **O webhook continua o mesmo, sem "visitante", sem compra
órfã.** Todo o código do "PR 1 do v2" que está parado na branch pode ser jogado fora.

**São 6 PRs, mais 1 teste no Stripe antes.** A página só entra no ar no último PR que
liga o funil (PR 5). Até lá, nada muda para ninguém.

**O que você configura:** no Stripe, o produto do e-book (preço avulso), a chave
publicável e a marca. No XQuiz, os links dos botões e o webhook desligado. Passo a passo
na seção 9.

**O que tem risco, em uma linha cada:**
- a página diz se um e-mail já é cliente, e o resto do site esconde isso (aceito pelo dono);
- quem digitar o e-mail errado paga e não recebe o link da senha, e precisa do suporte;
- o formulário do Stripe dentro do navegador do Instagram só se prova no celular, e por
  isso esse teste vem **antes** dos PRs de pagamento (Etapa 0b) e há o plano B acima.

---

## 2. Decisões que este plano tomou (e por quê)

| # | Decisão | Por quê |
|---|---|---|
| D-a | Entrada do XQuiz continua sendo a **`/q`**, que redireciona para a **`/assinar`** quando o link traz `plano` | A `/q` já é dona do fragmento (p/r → cookie `quiz_result`, com a disciplina de PII). Se a `/assinar` também lesse p/r, a lista de perfis ganharia uma 4ª cópia (§0.7; `tests/test_signup_quiz.py` compara três). |
| D-b | Página nova **`/assinar`**, em arquivos próprios (`assinar.html/.js/.css`), e não um "modo" da `precos.html` | A `precos.html` já tem três modos (`/precos`, `/continuar-compra`, Pix). O b9dc2c4 tentou o "modo assinar" e foi descartado. |
| D-c | **(Superada pelo #658.)** A conta nasce em `db/signup_quiz.criar_conta_sem_codigo`, que **não** passa por `email_verification_codes`. Sob a trava do e-mail (`db_support.trava_email`, sem esperar): conta existente → `tem_conta`; código de register vivo → `cadastro_pendente`; senão `db_support.inserir_conta_nova` (`ON CONFLICT DO NOTHING`) | O desenho original (chamar `create_email_verification` + `confirm_email_verification`) deixava o quiz invalidar o código de quem estava no meio do `/auth/register`, e o confirm fundia contas. O telefone disputado é descartado por `db_support.telefone_livre` e `db_support.gravar_descartando_telefone_disputado`, as mesmas funções dos três criadores de conta. |
| D-d | O bloco de "conta acabou de nascer" (sessão, as três atribuições e o CAPI CompleteRegistration) vira um helper no monólito, chamado pelos **três** lugares: `/auth/verify-email`, `_completar_cadastro_social` (Google/Apple, monólito :4700-4763) e a rota nova | Reúso real (§0.1, "extrair"). O bloco já está repetido hoje, linha por linha, entre o `verify-email` e o `_completar_cadastro_social`, que só muda o `event_source_url` (`/cadastro` × `/completar-cadastro`). Extrair para dois e deixar o terceiro seria criar a duplicata órfã que o §0.1 proíbe. O diff continua pequeno: um parâmetro `origem_url`. O `log_auth_login_event` do social fica fora do helper, porque só ele o tem. |
| D-e | O checkout da `/assinar` é o **`/billing/create-checkout` de hoje com dois campos novos**: `origem: "assinar"` (oferece o e-book e ajusta o `cancel_url`) e `embutido: bool` (formulário embutido ou página do Stripe). Não é uma rota nova | O lock, o rastreio (`_ga/_fbp/_fbc`), a elegibilidade do trial, os 409 (`already_subscribed`, `lifetime`, `pix_active`) e o `record_checkout_started` já estão lá. São **dois** campos, e não um, por causa do plano B: o checkout hospedado da `/assinar` também leva o e-book, então o e-book não pode depender do modo embutido. |
| D-f | `return_url` = a **mesma** `success_url` de hoje (`/home?upgrade=success&sid=…&ev=…&td=…&pl=…&ia=…`) | A `/home` já espera o webhook (overlay de ~20 s, fail-open) e dispara StartTrial/Purchase com o `sid`. Não se mexe nisso. |
| D-g | Pix = **`PBPurchaseIntent.begin(plan,"annual","pix")` + `markAwaitingAuth()` + ir para `/continuar-compra`** | É o fluxo de hoje, inteiro. O `pix-checkout.js` depende dos globais da `precos.html` (`currentCycle`, `getCsrfToken`, …), e portá-lo para a `/assinar` duplicaria código. |
| D-h | CSP: os hosts do Stripe entram na CSP **global** (`_SECURITY_HEADERS`) | Existe um lugar só para ela. O `script-src` já tem `'unsafe-inline'`, então o ganho de uma CSP só da `/assinar` seria pequeno e custaria uma segunda cópia. |
| D-i | A `/assinar` tem **Pixel e GA4, mas não tem Clarity** | O Pixel cria o `_fbc` a partir do `fbclid`, e é ele que amarra a compra ao anúncio. O Clarity gravaria nome, e-mail e WhatsApp digitados (a `/suporte` e o `/cadastro` já ficam sem ele pelo mesmo motivo). |
| D-j | Aceite dos termos: caixinha obrigatória, validada no servidor | É o mesmo do `/cadastro` (checkbox) e do Google (`accepted_terms` no servidor, monólito :4713). |
| D-k | WhatsApp obrigatório e válido (400 se inválido) | Igual ao `/auth/register`. Sem número não há teste grátis nem produto. |
| D-l | Limites: **10/h por IP** (balde `quiz`) e **3/h por e-mail** (balde do `register`), **sem teto global** | O 10/h por IP foi decidido pelo dono em 2026-09-27 (b9dc2c4). Um teto global numa rota pública deixaria qualquer um derrubar o funil. O webhook tinha teto global porque todo pedido vinha do IP do XQuiz. |
| D-m | Texto do "sem teste grátis" **neutro, o mesmo para os dois motivos** ("sem telefone" e "telefone já usou") | O telefone digitado não é confirmado. Diferenciar os dois deixaria qualquer um descobrir quais números já usaram o teste. Continua valendo "quem já usou vê *sem teste* antes de pagar". |
| D-n | Sessão Stripe da `/assinar` (embutida **e** hospedada) com **`expires_at` de 1 h** | Encurta a janela em que uma aba esquecida ainda cobra, por exemplo quando a pessoa já pagou no Pix em outra aba. É um valor chutado, pode ser ajustado. |
| D-o | **Cupons ligados** também na `/assinar` (`allow_promotion_codes=True`, igual à `/precos`) | Decisão do dono (pergunta 1). O parâmetro fica igual nos dois modos, sem ramo. |
| D-p | **Plano B do embutido:** a `/assinar` cai no checkout **hospedado** (`embutido:false`, `origem:"assinar"`) em três casos: user agent na lista `HOSPEDADO_UA` (começa só com `PigBankApp`, o app iOS); falha de montagem do Stripe.js (script não carrega, a chamada de montar rejeita, ou nenhum `<iframe>` no contêiner em 10 s); e um link sempre visível, "Problemas com o pagamento? Abrir a página segura do Stripe" | É o pedido da revisão. Um iframe que monta mas fica em branco não é detectável com segurança, e o link manual cobre esse caso. O Instagram e o Facebook só entram na lista se a Etapa 0b mostrar que o embutido falha lá: tirá-los de saída mandaria quase todo o tráfego de anúncio para fora do domínio sem evidência. |

---

## 3. O que já existe: guardar ou jogar fora

**O diff não commitado do "PR 1 do v2" (branch `Japa/funil-visitante`): jogar tudo fora.**
- `db/checkout_visitante.py`, `core/services/checkout_visitante.py`, a tabela
  `checkout_visitante` e os ganchos no webhook: o desenho novo não tem visitante no
  webhook. A tabela nunca foi para produção (o diff nunca foi commitado), então não há
  nada para dropar.
- `telefone_pode_ter_trial` e `_telefone_ja_usou`: servia para "número sem conta". Na
  v3 a conta, com o telefone, já existe antes da sessão, e o
  `is_trial_eligible_for_user(user_id)` de hoje resolve.
- `minutes_valid` no e-mail de senha: o link de "crie sua senha" é pedido na hora pelo
  botão, e os 30 min de hoje bastam.
- `send_trial_nao_aplicado_email`, `send_checkout_orfao_email` e `notify_checkout_orfao`:
  os dois casos (trial encerrado no webhook e compra órfã) deixam de existir.
- Os testes `tests/_checkout_visitante_helpers.py` e `test_checkout_visitante_*.py`:
  testam o que sai.

**Do b9dc2c4 (`Japa/quiz-checkout-stripe`), guardar três linhas e jogar o resto fora:**
- `_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")`, usado com `fullmatch`. É a mesma
  regex do `cadastro.html`.
- `_nome`: recusar `"{{"`, que é o placeholder do XQuiz quando o campo vem vazio.
- `LIMITE_IP_QUIZ = (10, 3600)`, a decisão do dono.
- Descartar a `/assinar` "modo precos", a `/q` com código e o `/auth/quiz/start`.

**Na main: o que se reusa e o que sai.**

| Existe | Destino |
|---|---|
| `/q` + `quiz-resultado.js` (p/r → cookie) | **reusa**, com um ramo novo: com `plano` na query, vai para a `/assinar` (PR 5) |
| `_apply_quiz_attribution` | **reusa** sem mudança (roda dentro do helper D-d) |
| `inserir_conta_nova`, `trava_email`, `telefone_livre`, `gravar_descartando_telefone_disputado` (`db_support.py`) | **reusa** (D-c, já na main pelo #658) |
| `/billing/create-checkout` + `_billing_checkout_for_user` | **estende** (PR 2) |
| `/continuar-compra`, `purchase-intent.js`, `pix-checkout.js` | **reusa** para o Pix (D-g) |
| `/auth/login`, `/auth/forgot-password`, `/auth/google/start?next=/continuar-compra` | **reusa** no "já tem conta" |
| `_completar_cadastro_social` (Google/Apple) | **passa a chamar o helper D-d**, com comportamento idêntico |
| `/settings/{uid}/password-reset` + `/reset-password` | **reusa** como o "Crie sua senha" (PR 4) |
| `/cadastro`, `/auth/register`, `verify-email` | ficam como estão (o `verify-email` só perde o final para o helper) |
| `/xquiz/webhook`, `/auth/quiz/resend`, ramo `e/c` da `/q`, `quiz_signup_pendente`, a isenção de CSRF do webhook | **saem** no PR 6, depois da troca no XQuiz |

---

## 4. Máquina de estados da `/assinar`

Entrada: `/assinar?plano=essencial|plus|pro&ciclo=monthly|annual[&utm…&fbclid…]#n=&e=&w=`.

**Carga (síncrona, no `<head>`, ANTES do ponto em que o `inject_tracking` injeta o Pixel):**
1. O script lê `n/e/w` do fragmento para a memória e faz `history.replaceState` sem o
   fragmento. Assim o Pixel nunca vê PII no `dl`.
2. Valida `plano` e `ciclo`. Se forem inválidos, mostra "link inválido" com um botão
   para a `/precos` (estado **L0**).
3. Faz `GET /auth/me`:
   - **401 ou falha** → **S1** (formulário).
   - **200**, e o e-mail do fragmento está vazio ou é igual ao da sessão → **S3**,
     com "Assinando como a•••@x. Não é você? Sair" (o "Sair" faz o logout e volta ao S1).
   - **200** com um e-mail diferente → **S1** com o e-mail digitado. O servidor decide:
     outra conta ou conta nova, e a sessão troca.

| Estado | Evento | Vai para |
|---|---|---|
| S1 formulário | Continuar → `POST /auth/quiz/conta` 200 `criada` | Pixel CompleteRegistration (`signup_<uid>`) + GA4 `sign_up{method:"quiz"}` → **S3** |
| S1 | 200 `logado` (sessão já é desse e-mail, por recarga ou duplo clique) | **S3** |
| S1 | 200 `tem_conta` | **S2** |
| S1 | 400 (e-mail, telefone, nome ou termos) | S1 com o erro no campo |
| S1 | 429 | S1: "Muitas tentativas, aguarde" |
| S1 | 200 `cadastro_pendente` (há um `/auth/register` com senha em andamento para o e-mail; nada foi criado nem tocado) | S1 com o aviso "Já existe um cadastro em andamento com este e-mail. Termine pelo código que enviamos para ele, ou use outro e-mail", e um link para o `/cadastro`. Sem sessão e sem checkout |
| S1 | 409 `ocupado` (outro pedido do mesmo e-mail está com a trava, por exemplo um duplo clique) | refaz 1 vez depois de ~1 s. Se falhar de novo, S1 com erro genérico |
| S2 | "Entrar com sua senha" | **não faz login dentro da `/assinar`**: `location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search)`, no padrão da `precos.html:587` e da `shared.py:1212`. O `/login` faz a senha e o MFA inteiros. Com MFA, o `/auth/login` devolve só o `mfa_challenge`, sem cookie, e um login feito aqui perderia o desafio: a pessoa digitaria tudo de novo. **Sem o `encodeURIComponent`, o `nextParam` do `login.html:144` (que lê com `URLSearchParams`) corta o `next` no primeiro `&`**, e a pessoa volta sem o `ciclo` e sem a UTM e cai em "link inválido". `/assinar` entra na allowlist do `nextParam`, que compara por prefixo e mantém a query. Na volta, a carga cai no S3 |
| S2 | "Entrar com Google" | `PBPurchaseIntent.begin(plan,cycle,"card")` + `markAwaitingAuth()` → `/auth/google/start?next=/continuar-compra` (checkout hospedado, sem e-book: é o limite aceito) |
| S2 | "Não tenho ou esqueci a senha" | `POST /auth/forgot-password`, com a mensagem de sempre (o link serve para conta sem senha: a copy muda para "definir", monólito :3686) |
| S3 checkout | `POST /billing/create-checkout {plan, interval, embutido:true, origem:"assinar"}` 200 (sem `origem`, o padrão é `"precos"`: a sessão sai sem o e-book e sem o `metadata.origem` da entrega) | mostra o texto do teste grátis (`trial_days`), monta o Stripe (`client_secret` + `publishable_key`), dispara `InitiateCheckout`/`begin_checkout` e mostra o link do Pix se `plans-config.pix_annual_available` → **S4** |
| S3 | 409 `already_subscribed` / `lifetime` | "Você já é assinante" + botão `/home` (**F1**) |
| S3 | 409 `pix_active` | a mensagem do 409 + botão `/home` (**F1**) |
| S3 | 401 depois do auth-refresh (sessão morreu) | S1: "Sua sessão expirou, entre de novo" |
| S3 | 502 / 503 | S3: "Não deu para abrir o pagamento" + tentar de novo |
| S3 | user agent em `HOSPEDADO_UA` | **H** (não tenta montar o embutido) |
| S3 | o Stripe.js não carregou, a montagem rejeitou, ou não há `<iframe>` em 10 s | **H** |
| S4 | clicou em "Problemas com o pagamento? Abrir a página segura do Stripe" | **H** |
| H hospedado | `POST /billing/create-checkout {plan, interval, embutido:false, origem:"assinar"}` 200 | `location.href = checkout_url` (a página do Stripe, com a mesma conta, o mesmo trial e o e-book oferecido). A sessão embutida aberta é expirada no servidor pelo laço que já existe (o `ui_mode` não casa) |
| H | 409 / 502 / 503 / 401 | os mesmos desfechos do S3 |
| H | pagou na página do Stripe | `success_url` = a mesma `/home?upgrade=success…` |
| H | desistiu na página do Stripe | `cancel_url` = `/assinar?plano=…&ciclo=…`, montada no servidor com o plano e o ciclo já validados (não é redirect aberto) → carga → S3 → embutido de novo (ou H, se o user agent estiver na lista) |
| S4 Stripe | cartão recusado / 3D Secure | tudo dentro do iframe do Stripe. Nosso estado não muda |
| S4 | pagamento concluído | o Stripe navega a página inteira para o `return_url` → `/home?upgrade=success…` (fluxo de hoje) |
| S4 | clicou em "Prefere Pix?" | D-g → `/continuar-compra` |
| S4 | recarrega a página | Carga → `/auth/me` 200 → S3 → **a mesma sessão Stripe é reaproveitada** (`_checkout_session_matches` com `ui_mode`) |
| S4 | duas abas | as duas reaproveitam a mesma sessão Stripe. Se a Etapa 0 mostrar que o `list` não devolve `client_secret`, a 2ª aba expira a 1ª, que mostra o erro do Stripe, e recarregar resolve |
| S4 | desistiu (fechou a aba) | a conta fica sem plano. Voltando no mesmo navegador: S3. Em outro navegador: S2, e aí "não tenho senha" → link por e-mail → login → `/home` → gate de plano → `/precos` (checkout hospedado). **Limite aceito**, veja a seção 8 |
| S4 | a sessão Stripe expirou (1 h) | erro do Stripe no iframe. Recarregar cria outra |

---

## 5. PRs, na ordem

A ordem garante que o funil só abre (PR 5) com todas as peças no ar. PR 3 e PR 4 podem
correr em paralelo depois do PR 2, com uma dependência: o job do e-book (PR 3) usa o
`conta_sem_credencial` do PR 4. Quem chegar primeiro cria a função, e o outro reusa.

### Etapa 0: prova no Stripe em modo teste (não é PR; é o portão do PR 2)

Com as chaves de teste do dono e um produto de e-book de teste, rodar um script de
rascunho (fora do repo) com o SDK `stripe==15.6.1` do `requirements.txt`. Provar e anotar
no corpo do PR 2:
1. o **valor exato de `ui_mode`** para o checkout embutido na versão de API que esse
   SDK fixa. A Stripe renomeou os modos em versões recentes, então não se chuta;
2. `mode=subscription` + `customer` + `trial_period_days=15` + `optional_items=[preço
   avulso]` + `locale=pt-BR` + `payment_method_types=["card"]` é aceito junto;
3. com trial, o e-book **é cobrado na hora**, na 1ª fatura (`billing_reason=subscription_create`,
   `amount_paid` = preço do e-book);
4. o e-book **não vira item da assinatura**: `subscription.items.data` só tem o
   recorrente. É o que mantém o `_subscription_price_id` (lê `data[0]`) certo. Se virar,
   o PR 3 passa a escolher o item com `price.recurring` e ganha teste;
5. o `checkout.Session.list(status="open")` devolve o **`client_secret`** das sessões
   embutidas (decide o reaproveitamento);
6. o nome da chamada do Stripe.js (`initEmbeddedCheckout` ou o sucessor) e a **lista de
   hosts da CSP**, medida pelo console sem violação: esperado `script-src
   https://js.stripe.com`; `frame-src https://js.stripe.com https://checkout.stripe.com
   https://hooks.stripe.com`, somados ao que já existe (o `frame-src` tem o Pluggy). O
   `connect-src 'self' https: wss:` e o `img-src 'self' data: blob: https:` de hoje já
   cobrem;
7. o cartão de 3DS de teste completa dentro do iframe;
8. `optional_items` também é aceito na sessão **hospedada** (o plano B leva o e-book).

### Etapa 0b: o embutido no navegador do Instagram e do Facebook, de verdade (antes de começar o PR 2)

O staging não resolve DNS hoje, então o teste real vai por dois caminhos que não
dependem dele. Os dois usam o Stripe em **modo de teste** e não cobram nada.

**0b-1 (dez minutos, sem código nosso, antes do PR 2).** Prova o componente do Stripe
dentro dos WebViews.
1. O dono manda para si mesmo, por mensagem direta no Instagram, o link da demonstração
   pública do Stripe para o checkout embutido (`https://checkout.stripe.dev`, na opção
   do formulário embutido), e toca no link: ele abre no navegador do Instagram. Faz o
   mesmo pelo Messenger, para o navegador do Facebook.
2. Em cada um (iPhone e, se der, Android): paga com o cartão `4242 4242 4242 4242`, e
   depois com o de 3DS `4000 0027 6000 3184` (aprova o desafio).
3. Anota: o formulário apareceu? o 3DS abriu e voltou? a página terminou no "sucesso"?

Se falhar no Instagram ou no Facebook, esse user agent entra na `HOSPEDADO_UA` já no
PR 5, e o plano segue igual (o plano B existe para isso).

**0b-2 (a nossa página, antes do merge do PR 5).** Prova a nossa CSP, os nossos cookies
e a nossa página no WebView.
1. O Coder sobe localmente a branch do PR 5 (com o PR 1 a 4 empilhados) com
   `dashboard_dev.py`, com chaves de teste do Stripe e banco de teste, e expõe por um
   túnel HTTPS temporário (`cloudflared tunnel --url http://localhost:<porta>`, que dá
   um endereço `https://….trycloudflare.com` sem configurar DNS). `DASHBOARD_URL`
   aponta para esse endereço, para o `return_url` voltar ao túnel.
2. O dono manda o link `…/q?plano=plus&ciclo=monthly#p=controlar&r=abcab&n=Teste&e=<e-mail de teste>&w=<número de teste>`
   para si pelo Instagram e pelo Messenger, toca, e faz a compra toda: cartão comum,
   3DS, recusado (`4000 0000 0000 0002`), com e sem e-book, e o "Problemas com o
   pagamento?" (plano B).
3. O Coder derruba o túnel no fim. Nada disso vai para produção.

Isso substitui a "verificação em staging" das versões anteriores deste plano.

### PR 1: conta nasce na `/assinar` (backend, fica parado até o PR 5)

> **MERGEADO como #658 (2026-09-29). O texto desta seção é o plano ORIGINAL e está
> SUPERADO** no mecanismo de criação: `create/confirm_email_verification`, o 409 do
> confirm e o teste do upsert não existem no que entrou. Para o que existe, veja o
> código (`frontend/routes/quiz_signup.py`, `db/signup_quiz.py`), o "Estado" no topo e a
> nota "Ajustes do PR 1" no fim. A seção fica pelo histórico das decisões.

**Muda, nesta ordem:**
1. `frontend/finance_bot_websocket_custom.py`: extrair o bloco repetido
   (`_issue_session_token` → `_entrega_sessao` → `_apply_referral/prospect/quiz_attribution`
   → CAPI CompleteRegistration) para `async def _sessao_de_conta_nova(request, response,
   background_tasks, *, user_id, email, origem_url) -> dict`. Chamadores: o
   `auth_verify_email` (`origem_url=f"{DASHBOARD_URL}/cadastro"`) e o
   `_completar_cadastro_social` (`…/completar-cadastro`), os dois com comportamento
   idêntico ao de hoje. O `log_auth_login_event` do social continua fora do helper.
2. `frontend/routes/quiz_signup.py`: `POST /auth/quiz/conta`, com corpo `QuizContaBody(_SemVeneno)
   {email, nome, whatsapp, aceitou_termos}`. Os helpers do monólito entram por import
   tardio, como o arquivo já faz. Passos:
   - `_EMAIL.fullmatch` → 400; `aceitou_termos` falso → 400; `normalize_phone_e164` → 400;
     `_nome` (com o filtro `{{`);
   - limites D-l (`_check_persistent_rate_limit("quiz", f"ip:{ip}", 10, 3600)` e o
     balde `register` por e-mail);
   - sessão válida com o **mesmo** e-mail → `{"estado":"logado"}` (não cria nada);
   - `find_user_id_by_email` achou → `{"estado":"tem_conta"}`, **sem cookie de sessão e
     sem escrever nada**;
   - senão: `create_email_verification(email, None, phone, display_name=nome)` →
     `confirm_email_verification(email, code, source=signup_source_from_request(request))`
     → `_sessao_de_conta_nova(…, origem_url=f"{DASHBOARD_URL}/assinar")` →
     `{"estado":"criada","user_id":…, **credenciais}`;
   - `AccountAlreadyExistsError` na corrida → `tem_conta`. `ValueError` do confirm
     (código consumido por pedido paralelo) → 409.
   - PII fora de log: nas exceções, só `type(exc).__name__`, como o arquivo já faz.
3. `docs/CLAUDE.md`, "Autenticação": um parágrafo com o caminho novo.

**Não muda:** o webhook do XQuiz, a `/q`, o `/cadastro`, o `/auth/register` e o schema.

**Pode quebrar:** o `verify-email` e o `complete-signup` do Google e da Apple, que
ganham o helper (os testes de hoje deles, inclusive `tests/test_auth_google_app_cadastro.py`,
e os do quiz são a rede); `_apply_quiz_attribution` apaga o cookie na resposta, e o
helper tem de receber o **mesmo** `response`.

**Telefone disputado (apontamento 4 do Manager): nenhuma mudança no PR 1, e nenhuma
issue nova.** O INSERT de `create_email_verification_impl` (`db_support.py:887-909`) é
em `email_verification_codes`, que não tem índice único de telefone (`db/schema.py`
só indexa o e-mail), então duas linhas pendentes com o mesmo número não conflitam. O
conflito real é no INSERT de `auth_accounts`, e ele já é tratado pelo
`gravar_descartando_telefone_disputado` (`db_support.py:993`), que desfaz e grava sem
telefone. O limite que sobra é o que a docstring dele já declara: variantes do mesmo
número com e sem o nono dígito têm hashes diferentes e entram as duas. Isso vale igual
no `/cadastro` de hoje e não é agravado aqui.

**Testes (pytest, estado real no banco de teste):**
- e-mail novo → a conta existe com `password_hash` NULL, telefone gravado, cookies de
  sessão e CSRF emitidos, e o cookie `quiz_result` vira `dashboard_profile`;
- **e-mail existente com senha → `tem_conta`, e o `password_hash`, o `user_id` e o
  telefone da conta existente ficam idênticos (antes = depois), sem `Set-Cookie` de
  sessão.** É o teste que guarda contra tomada de conta;
- mesmo e-mail com a sessão dele → `logado`, e não nasce uma segunda conta;
- telefone de outra conta → a conta nasce **sem** telefone (resposta igual a `criada`);
- `complete-signup` do Google continua emitindo a sessão, gravando o quiz e
  mandando o CompleteRegistration com `…/completar-cadastro` (regressão da extração);
- sem o header de CSRF → 403; corpo com NUL → recusado; 11ª chamada do mesmo IP → 429;
- `aceitou_termos:false` → 400;
- **conversa:** `POST /auth/quiz/conta` → `GET /auth/me` (`has_password:false`,
  `needs_plan_selection:true`) → `POST /billing/create-checkout` com o Stripe falso
  → 200.
- **Controles do §3:** *negativo*: tirar o ramo `find_user_id_by_email` faz o teste do
  "antes = depois" ficar vermelho (o `confirm` faz upsert com `password_hash=excluded`);
  *positivo*: o caso do e-mail novo cria a conta.

### PR 2: checkout da `/assinar` (embutido e hospedado) + CSP (backend)

Portão: a Etapa 0b-1 (feita) e **o que falta da Etapa 0, medido com a chave de teste e
anotado no corpo do PR 2 antes de codar**:
- item 2: `mode=subscription` + `customer` + trial + `optional_items` + `locale=pt-BR` +
  `payment_method_types=["card"]` + `adaptive_pricing` desligado, tudo na mesma sessão;
- item 4: o e-book **não** entra em `subscription.items.data`. O PR 3 depende disso,
  porque o `_subscription_price_id` lê `data[0]`;
- item 5: o `checkout.Session.list(status="open")` devolve o `client_secret` da sessão
  embutida. Se não devolver, o reaproveitamento da seção 4 (S4, "duas abas" e "recarrega")
  segue o caminho alternativo já escrito lá;
- item 6: a lista de hosts da CSP, medida no console sem violação.

**Muda:**
1. `CreateCheckoutBody`: `embutido: bool = False` e `origem: str = "precos"`
   (aceita `"precos"` e `"assinar"`; outro valor dá 400).
2. `_billing_checkout_for_user(…, embutido=False, origem="precos")`:
   - `metadata["origem"] = origem` (vai também para o `subscription_data`, que é cópia);
   - `_checkout_session_matches` compara também o `ui_mode` e o `metadata.origem`, e
     exige `client_secret` (embutido) ou `url` (hospedado). Assim uma sessão nunca é
     reaproveitada por outro modo ou por outra página; ela é expirada pelo laço que já
     existe;
   - `_new_session`, se `origem == "assinar"`: `optional_items=[{"price":
     STRIPE_PRICE_ID_EBOOK, "quantity": 1}]` só se **as duas** envs estiverem preenchidas:
     `STRIPE_PRICE_ID_EBOOK` e `EBOOK_URL`. Com só o preço, a pessoa pagaria por um e-book
     que o webhook não tem como entregar. Um teste cobre a config pela metade (só o preço,
     ou só a URL): a sessão sai sem o e-book. `cancel_url` =
     `f"{DASHBOARD_URL}/assinar?plano={plan}&ciclo={interval}"` (valores já validados;
     só vale no hospedado);
   - `_new_session`, se `embutido`: usa `ui_mode` (o valor da Etapa 0), `return_url` =
     a string da `success_url` de hoje, e não manda `success_url`/`cancel_url`;
   - `_new_session`, se `origem == "assinar"` (**nos dois modos**, embutido e hospedado):
     `expires_at=now+3600` (D-n). Sem o campo, o Stripe usa 24 h, e o plano B hospedado
     manteria aberta por 24 h a janela de cobrança dupla (Pix numa aba, cartão na outra);
   - `_new_session`, se o e-book é oferecido: `metadata["ebook_price"] = STRIPE_PRICE_ID_EBOOK`
     e `metadata["ebook_url"] = EBOOK_URL`, também no `subscription_data.metadata`. O
     PR 3 identifica o e-book e pega a URL por essa **foto**, e não pela env do momento
     do webhook. Assim, trocar ou tirar a env com uma
     sessão aberta não faz a compra perder a entrega nem virar receita do plano;
   - `allow_promotion_codes=True` em **todos** os casos, como hoje (D-o);
   - `_new_session`, se `origem == "assinar"`: `adaptive_pricing={"enabled": False}`, para
     o preço sair sempre em BRL. A etapa 0 viu USD fora do Brasil (Adaptive Pricing). O
     teste confere o parâmetro na sessão da `/assinar`, e a `/precos` continua sem ele;
   - `metadata["td"] = str(trial_days)`;
   - retorno: embutido → `{"client_secret", "trial_days", "plan", "interval",
     "session_id"}`. Hospedado → como hoje, mais `trial_days`. **Hoje a rota descarta
     o `session_id` sempre** (`result.pop("session_id", None)`, no handler de
     `/billing/create-checkout` do monólito). No modo embutido esse `pop` tem de sair,
     senão o contrato acima não se cumpre.
3. A rota: se `embutido`, acrescenta `publishable_key` (env nova
   `STRIPE_PUBLISHABLE_KEY`, e 503 se faltar). O `trial_days` de sessão reaproveitada
   vem de `metadata.td`.
4. `_SECURITY_HEADERS["Content-Security-Policy"]`: os hosts medidos na Etapa 0,
   **somados** aos de hoje, nunca por cima. O `frame-src` já tem o Pluggy
   (`https://cdn.pluggy.ai https://connect.pluggy.ai`), e trocar a diretiva quebraria o
   widget do Open Finance. Um teste confere que os hosts do Pluggy continuam lá.
5. `docs/CLAUDE.md`: "Pagamentos" (o modo embutido e as duas envs) e "Frontend"
   (a allowlist da CSP).

**Não muda:** o caminho da `/precos` (`origem:"precos"`, o default: mesmos kwargs de
hoje, fora os campos novos do metadata), o webhook, a regra do trial
(`is_trial_eligible_for_user`) e o `_billing_user_lock`.

**Pode quebrar:** `tests/test_billing_checkout.py` (o fake do Stripe precisa aceitar os
kwargs novos); os testes de CSP (`test_static_pages_routes.py:76`,
`test_ga4_tracking.py:262`, `test_clarity_tracking.py:158`); uma aba da `/precos`
aberta expira a sessão embutida da mesma pessoa, e vice-versa (é o desejado: uma
tentativa aberta por cliente).

**Testes:**
- `assinar` + embutido: kwargs no fake com o `ui_mode`, o `return_url` com
  `{CHECKOUT_SESSION_ID}` e sem `success_url`/`cancel_url`, os `optional_items` só com
  a env, `allow_promotion_codes=True` e `customer=` (e não `customer_email`);
- `assinar` + hospedado (o plano B): `url` na resposta, `optional_items` presentes,
  `allow_promotion_codes=True`, `cancel_url` = `/assinar?plano=…&ciclo=…`, sem
  `ui_mode` embutido, e o **mesmo** `trial_period_days` que o embutido daria para a
  mesma conta;
- **`precos` idêntico ao de hoje** (sem `ui_mode`/`optional_items`,
  `allow_promotion_codes=True`, `cancel_url=/precos?escolha=1`, `url` na resposta);
- `origem` inválida → 400;
- telefone que já usou → sem `trial_period_days` e `trial_days:0` na resposta;
  elegível → 15;
- reaproveitamento: sessão embutida aberta que casa → o mesmo `client_secret` e
  nenhum `create`; troca embutido → hospedado da mesma conta → a embutida é expirada e
  nasce uma hospedada; sessão hospedada da `/precos` aberta + pedido da `/assinar` →
  expirada (a `origem` não casa);
- os 409 (`already_subscribed`, `lifetime`, `pix_active`) iguais com `origem:"assinar"`;
- a CSP da resposta contém os hosts, e `frame-ancestors 'none'` continua lá.
- **Controles:** *negativo*: forçar `eligible=True` deixa vermelho o caso "telefone
  já usou"; tirar `origem` do `_checkout_session_matches` deixa vermelho "sessão da
  `/precos` não é reaproveitada pela `/assinar`"; *positivo*: o caso elegível recebe
  15, e a `/precos` continua com o checkout de hoje.

### PR 3: webhook entrega o e-book

**Muda (só no `billing_webhook`, mais o e-mail):**
1. `checkout.session.completed`: se a sessão tem `metadata.ebook_price` (a foto do
   preço oferecido, gravada pelo PR 2) **e** veio da `/assinar` (`metadata.origem ==
   "assinar"`, embutida ou hospedada), **grava uma entrega pendente** do e-book: uma
   linha por `user_id` + `session_id`, idempotente, para a reentrega do evento não
   criar outra. Ela guarda também a **URL do e-book oferecida**, copiada de
   `metadata.ebook_url` da sessão (a foto do PR 2, e nunca a env na hora do webhook).
   O envio pode esperar dias pela senha, e a env pode mudar ou sumir nesse tempo. O webhook **não** envia e **não** chama a Stripe para isso. Sem
   `EBOOK_URL` na hora da compra, o PR 2 nem oferece o e-book.
   - **Onde e em que ordem:** logo depois do grant, **antes** dos outros efeitos do
     ramo (funil, e-mails, `notify_new_pro`). Se a gravação falhar, o ramo responde
     **5xx** como já faz quando o grant falha ("o grant vem primeiro"). Nada depois dele
     rodou, e a reentrega executa tudo uma vez só. Com a pendência gravada, o resto
     segue como hoje e responde 2xx. O e-book nunca depende de um 2xx dado sem registro
     durável.
   - **Por que não enviar no webhook nem responder 5xx por falha de envio:** a reentrega
     da Stripe repete o ramo INTEIRO por até 3 dias. A dedupe do `_fire_email` vale 1
     dia por padrão, e o `notify_new_pro` não tem nenhuma.
   - **Quem envia:** um job, no molde das tarefas de fundo que já existem. **Só envia
     depois que o e-mail foi provado** (decisão do dono em 2026-09-30), ou seja, quando
     a conta já tem credencial (`not conta_sem_credencial`, do PR 4): a pessoa criou a
     senha pelo link do e-mail, ou tem Google/Apple. Antes disso a pendência espera.
     - Assim, quem digitou um e-mail errado e o corrigiu na `/settings` recebe no
       endereço certo, e o dono de um e-mail alheio nunca recebe o e-book de outro.
     - Custo aceito: quem paga e nunca cria a senha não recebe o e-book.
     - O PR 4 pode acordar o job na hora em que a senha é criada, sem esperar o próximo
       ciclo.
   - **O que o job faz:** confirma pela `list_line_items` (preço = `ebook_price`) que o
     e-book foi comprado. Se não foi, fecha a pendência sem enviar. Se foi, envia com
     `send_ebook_email`, com a foto da URL e nunca a env do momento. O envio **não**
     passa pela dedupe por usuário do `_fire_email`. Essa dedupe tem a chave
     `fn.__name__` + `uid` e devolve True se a chave existe, então engoliria a segunda
     compra do mesmo dia e fecharia a pendência sem enviar. O job chama o remetente com
     a chave da **pendência** (`user_id` + `session_id`): ou passa a chave ao
     `_fire_email` extraído (um parâmetro opcional de chave), ou chama `send_ebook_email`
     direto e grava o resultado na linha. URL vazia na pendência não conta como enviado: a pendência fica aberta e
     gera alerta.
     - **A marca de "enviado" é a própria linha da pendência** (`user_id` + `session_id`),
       e não a chave de 3650 dias do `_fire_email` por usuário. Com a chave por usuário,
       quem comprasse o e-book de novo, depois de cancelar e assinar outra vez, pagaria e
       não receberia.
     - O job reivindica a linha de forma atômica antes de enviar, com uma expiração para
       não travar se cair no meio, e a fecha só com o envio confirmado. O claim impede
       duas rodadas SIMULTÂNEAS do job de enviarem a mesma pendência.
     - **Entrega "pelo menos uma vez", aceita de propósito:** se o job cair depois de o
       provedor aceitar o e-mail e antes de fechar a linha, a expiração do claim faz a
       rodada seguinte enviar de novo. Um e-mail repetido com o mesmo link de download é
       inofensivo, e fechar essa janela pediria uma chave de idempotência no provedor,
       que o `send_email` de hoje não usa. Se um dia valer a pena, a chave natural é o
       `session_id`.
     - A pendência é fechada quando o envio devolve True. Uma falha (Stripe fora, e-mail recusado) mantém a pendência para o próximo
     ciclo.
   - **Condição do PR 3:** o registro da pendência segue §0.1. Procure antes se já existe
     outbox ou fila de e-mail no repositório; se existir, reuse. **O `_fire_email` hoje é
     uma função ANINHADA dentro do `billing_webhook`**, e um job não a alcança. O PR 3 a
     extrai para o nível do módulo (extrair, §0.1: o webhook e o job passam a chamar a
     mesma), sem mudar a chave nem o comportamento. Para o e-book, a marca de enviado é a
     linha da pendência (por compra), e não outra chave por usuário.
2. O ramo de fatura paga **que já existe** trata os DOIS eventos juntos:
   `elif event["type"] in ("invoice.paid", "invoice.payment_succeeded")` (monólito).
   A mudança é **dentro dele**. Um ramo só para `invoice.paid` deixaria o
   `payment_succeeded` calculando e-mail e comissão sobre o valor cheio, com o e-book.
   `amount_plano = amount_paid − valor líquido das linhas de
   `invoice.lines.data` com `pricing.price_details.price` igual ao `ebook_price` da
   `metadata` da assinatura (a foto do PR 2), e não à env do momento. **O "valor líquido"
   da linha é MEDIDO, não suposto:** numa fatura de teste com cupom que desconta o
   e-book, anote no corpo do PR 3 se o `amount` da linha já sai descontado. Se sair, o
   líquido é o próprio `amount`. Se não, é `amount − soma(discount_amounts)`. Subtrair o
   desconto duas vezes inflaria o `amount_plano`. O e-mail de cobrança e a comissão de afiliado usam o `amount_plano`
   (comissão só sobre o plano, decisão do dono), e com `amount_plano <= 0` os dois são
   pulados. Motivo: **com trial, a 1ª fatura é só o e-book**, e hoje ela mandaria
   "cobrança do seu plano" e daria comissão sobre o e-book.
   **Formato da API `dahlia`** (conferido no SDK 15.6.1): a linha de FATURA
   (`InvoiceLineItem`) não tem `price` no nível de cima. O id fica em
   `pricing.price_details.price`, que pode vir expandido: aceite o id ou `.id`. A linha
   da SESSÃO de checkout (`LineItem`, a do `list_line_items` do item 1) continua com
   `price`. Os payloads dos testes seguem esse formato; comparar por `price.id` nunca
   acharia o e-book.
3. `core/services/email_service.py`: `send_ebook_email(to, url, dashboard_url="")`. Ela
   precisa aceitar o terceiro argumento, porque o `_fire_email` chama
   `fn(email, *args, DASHBOARD_URL)`, como o `send_trial_ending_email`. Com dois
   parâmetros ela dá `TypeError`, e o `_fire_email` engole o erro e devolve False.
   **A copy precisa da aprovação do dono.**
4. Envs novas: `STRIPE_PRICE_ID_EBOOK` e `EBOOK_URL` (link de download do PDF que o
   dono hospeda, decisão do dono).

**Não muda:** o grant (`_subscription_price_id`, salvo o item 4 da Etapa 0), o
StartTrial/Purchase do CAPI, a regra "`subscription_create` já contado no checkout" do
GA4, e o `claim_trial_for_user`.

**Pode quebrar:** a comissão de afiliado de quem compra o plano sem trial e com e-book
(passa a ser só sobre o plano). Os testes de comissão e de e-mail de cobrança de hoje são
a rede.

**Testes (payloads de webhook, com o Stripe falso para `Subscription.retrieve` e `list_line_items`):**
- trial + e-book: StartTrial sai, e há **uma** pendência do e-book mesmo com o evento
  entregue duas vezes, e o `invoice.paid` da 1ª fatura **não** manda e-mail de
  cobrança nem cria comissão. O mesmo vale com `invoice.payment_succeeded` no lugar do
  `invoice.paid`;
- pendência: o `checkout.session.completed` com e-book grava a pendência e responde
  2xx. Se a gravação falhar, responde 5xx sem ter rodado os efeitos seguintes. A
  reentrega não cria uma segunda pendência nem repete o e-mail de boas-vindas ou o
  alerta ao admin;
- job: a conta sem credencial **não** recebe o e-book (a pendência fica). Depois de
  criar a senha, recebe (pelo menos uma vez; a duplicata só acontece se o job cair
  entre o envio e o fechamento, e o teste cobre esse caso como aceito). A conta que trocou o e-mail antes de criar a senha
  recebe no e-mail novo. O envio falhando mantém a pendência, e o próximo ciclo
  envia;
- sem trial + e-book: o e-mail de cobrança e a comissão usam **só** o valor do plano;
- sem trial + e-book + cupom que desconta o e-book: a subtração usa o valor líquido da
  linha;
- sessão hospedada da `/assinar` (o plano B) com e-book: a pendência é gravada igual;
- sem e-book: tudo igual a hoje (renovação manda e-mail de cobrança e comissão);
- sessão da `/precos` (sem `ebook_price`): nenhuma pendência é gravada.
- **Controles:** *negativo*: tirar a subtração deixa vermelho "trial + e-book não
  cobra e-mail"; *positivo*: a renovação comum continua mandando.

### PR 4: "Crie sua senha" obrigatório no painel

**Muda:**
1. `db/google_auth.py`: `conta_sem_credencial(user_id) -> bool` = `password_hash is
   null` **e** nenhuma linha em `auth_identities` (nem Google, nem Apple). Uma query,
   ao lado de `auth_account_has_password`.
2. `/auth/me`: campo `precisa_criar_senha`.
2b. **O bloqueio vale no SERVIDOR, não só na tela** (decisão do dono em 2026-09-29, a
   partir de um P1 do Codex no #676). O motivo: quem pagou com um e-mail digitado
   errado, que é de outra pessoa, usaria as APIs por baixo do overlay. Depois, o dono
   verdadeiro do e-mail pediria "esqueci a senha" e leria os dados financeiros.
   - **Onde:** mais uma perna no gate central `_enforce_subscription_gate`
     (`frontend/routes/shared.py`). Com `exige_direito=True` e
     `conta_sem_credencial(user_id)`, ela responde 403
     `{"error": "password_required"}`.
   - **Quem já herda sem mudança:** as rotas de dados (`authorize_dashboard_access`) e a
     `/api/v2` (`api/v2/sessao.py`).
   - **O que continua liberado:** as rotas da própria conta (`authorize_account_access`,
     `exige_direito=False`: a `/settings`, que corrige o e-mail e manda o
     `password-reset`) e os prefixos isentos (`/auth`, `/billing`, `/conta`). É por elas
     que a pessoa sai do bloqueio.
   - **A perna no gate central NÃO basta sozinha.** Muitas rotas não passam por ele.
     - As rotas de IA (`GET /ai/messages`, `POST /ai/chat`) usam `require_pro_feature`,
       que só confere `_plan_gate_ok`.
     - Várias rotas só autenticam, com `Depends(_get_current_user)`.
     - E a própria rota que corrige o e-mail, `PATCH /settings/{uid}/security/contact`,
       usa `authorize_dashboard_access`. Com a perna nova, ela seria BLOQUEADA, e a saída
       do gate deixaria de existir.
   - **Por isso o PR 4 começa por uma TABELA DE TODAS as rotas autenticadas**, em vez de
     uma lista de exceções. A tabela sai de percorrer o `app.routes` e o sub-app da
     `/api/v2`, e não de memória. Ela inclui o `/ws`, o bot
     (`core/handle_incoming.py`, conta com WhatsApp vinculado) e o HTML servido. Cada
     linha é classificada em **bloqueia** ou **libera**, com o motivo.
   - **Libera, no mínimo:** do `/auth`, **só** o que serve para sair do bloqueio:
     `/auth/me`, o logout, o refresh, o login e a recuperação (`forgot-password`,
     `reset-password`). **O `POST /auth/link-code` BLOQUEIA:** ele vincula o WhatsApp de
     quem chama à conta, e quem pagou com o e-mail de outra pessoa ligaria o próprio
     telefone antes de provar o e-mail. Quando o dono do e-mail recuperasse a conta, o
     WhatsApp do outro continuaria com acesso pelo bot. A tabela classifica cada rota do
     `/auth` uma a uma. Do `/billing`
     **só** `create-checkout` e `plans-config`, as rotas de `authorize_account_access`,
     `POST /settings/{uid}/password-reset` e `PATCH /settings/{uid}/security/contact`.
     - Esta última passa a pular **só** a perna da credencial e continua com a checagem
       de dono e o CSRF.
     - Atenção: o docstring de `authorize_account_access` tem um aviso do dono ("não
       isente o `/contact`"). O aviso é sobre a perna do DIREITO, e ela **continua**
       valendo nessa rota. Não afrouxe as duas ao mexer na chamada.
   - **O resto do `/billing` BLOQUEIA:** `portal` (faturas e cartão no Stripe),
     `subscription`, `change-plan` e `cancel-change`. Hoje o prefixo `/billing` inteiro é
     isento do gate central (`_GATE_EXEMPT_PREFIXES`), então a perna da credencial
     precisa valer nessas rotas de outro jeito. A tabela diz como.
   - **O `GET /conta` também BLOQUEIA:** ele chama o mesmo `_create_billing_portal` e
     redireciona para o portal do Stripe. É um atalho do `/billing/portal`, e o prefixo
     `/conta` também é isento hoje. Sem senha, ele manda para o overlay de "Crie sua
     senha", e não para o portal.
   - **O `/ws` não chama o gate central:** tem uma cópia manual da checagem
     (`needs_plan_selection` ou `not has_app_access`) e fecha com o código 4402. A perna da
     credencial entra nessa cópia também, com um código de fechamento próprio.
   - **Bloqueia:** toda rota que lê ou grava dado financeiro, inclusive a IA
     (`require_pro_feature` também chama a checagem da credencial).
   - **Um teste percorre as rotas** e reprova a rota autenticada que não está na
     tabela, no molde do `tests/test_api_v2_rotas.py`. Sem isso, a próxima rota nova
     nasce sem o bloqueio e ninguém percebe. É a classe inteira, e não as duas
     instâncias que a revisão achou.
   - **Front:** o `criar-senha.js` trata o 403 `password_required` como "mostrar o
     overlay", e não como erro.
3. `frontend/criar-senha.js` + `criar-senha.css` (rotas em `static_pages.py`),
   carregados pela `home.html` e pela `dashboard.html`: um overlay que não fecha,
   "Crie sua senha para proteger sua conta", com o botão "Enviar link para
   <e-mail>" → `POST /settings/{uid}/password-reset`, **que já existe** (manda o
   "Definir senha" para conta sem senha, 3/min) → "Enviamos. Abra no seu e-mail." +
   "Reenviar" + "E-mail errado? Corrigir" (→ `/settings`, que já troca o e-mail) +
   "Sair". Quando a aba volta ao foco, relê o `/auth/me`: o reset revoga todas as
   sessões (monólito :3711), e se vier 401 vai para `/login`.
3b. **Trocar o e-mail invalida os links de reset em aberto.** Hoje o token de reset
   guarda só o `user_id` (`password_reset_tokens`, gravado por
   `create_password_reset_token_impl`), o consumo não confere o e-mail atual, e a troca
   de e-mail (`PATCH /settings/{uid}/security/contact`) não mexe nos tokens.
   - **O risco:** quem clicou em "Enviar link" com o e-mail errado e depois corrigiu
     deixa, por 30 min, um link válido na caixa do dono do e-mail errado. Ele cria a
     senha, derruba as sessões do comprador e fica com a conta paga.
   - **Conserto: amarrar o token ao e-mail**, e não só invalidar na troca. Invalidar na
     troca tem duas corridas: o pedido de reset lê o usuário numa transação e grava o
     token em outra, e o consumo confere o `used_at` separado da troca de senha.
     - O token passa a guardar o `email_hash` da conta no momento em que é emitido. A
       coluna é nova numa tabela que já existe em produção: o `CREATE TABLE IF NOT
       EXISTS` não a cria lá. Precisa de `ALTER TABLE password_reset_tokens ADD COLUMN IF
       NOT EXISTS email_hash text` no `init_db`, com um teste que sobe o schema ANTIGO e
       aplica a migração.
     - O consumo o **reivindica de forma atômica** (`update … set used_at = now() where
       token = %s and used_at is null and expires_at > now() returning user_id,
       email_hash`) e, na **mesma transação** da troca de senha, confere esse hash
       contra o e-mail atual da conta. Se for diferente, recusa.
     - Com isso, o link emitido para o e-mail antigo nunca vale depois da troca, qualquer
       que seja a ordem dos pedidos.
     - A invalidação na troca de e-mail pode continuar, como limpeza, mas deixa de ser o
       que garante a segurança.
   - **Teste:** pedir o link, trocar o e-mail e consumir o link antigo → recusado.
     Também com o token gravado **depois** da troca, mas com o hash antigo (a corrida do
     pedido). Dois consumos simultâneos do mesmo token → só um vence. O link pedido
     depois da troca funciona.
   - **Isto já vale hoje na `main`**, para qualquer conta, e não só a do quiz. Se o
     conserto entrar antes num PR próprio, o PR 4 só confere que ele existe.
4. Ordem dos overlays na `/home` (enumerar antes de codar: overlay de checkout,
   boas-vindas do Pro, onboarding do MFA e este): o gate só sobe **depois** de o overlay
   de checkout fechar e fica **acima** das boas-vindas. Com o gate de pé, o onboarding
   do MFA **não** aparece e **não** é marcado como visto (sem senha não dá para ativar
   MFA; veja `scripts/reset_mfa_onboarding_sem_senha.py`).
5. O Coder usa a skill `pigbank-frontend`.

**Não muda:** contas com senha, contas só-Google e só-Apple (têm identidade, então o
campo sai `false`), a `/settings` (não carrega o gate: é a saída para corrigir o e-mail),
e o backend de reset.

**Custo aceito pelo dono:** quem pagou só usa o app depois de clicar no link do e-mail.
Quem digitou o e-mail errado corrige na `/settings` (o reset vai para o e-mail novo) ou,
se não perceber, depende do suporte.

**Pode quebrar:** nada em contas existentes, pelo que o dono afirma: nenhuma conta foi
criada pelo quiz antigo (#640), e ele pediu para não tratar nem medir (pergunta 5). Se
alguma conta sem senha e sem Google/Apple existir por outro caminho, ela passa a ver o
gate, o que é correto: hoje ela só entra de novo pelo "esqueci a senha".

**Testes:**
- pytest: `precisa_criar_senha` true para uma conta sem senha e sem identidade; false
  com senha; false só-Google; false só-Apple;
- Playwright: com `me.precisa_criar_senha:true` o overlay está visível e **bloqueia o
  clique** (o `elementFromPoint` no botão do painel devolve o overlay), e o botão faz o
  POST certo; com `false` não há overlay; o MFA não abre com o gate de pé;
- **conversa:** `POST /auth/quiz/conta` → webhook `checkout.session.completed` →
  `/auth/me` (`app_access:true`, `precisa_criar_senha:true`) →
  `/settings/{uid}/password-reset` → consumir o token → `/auth/me` depois de logar
  (`precisa_criar_senha:false`).
- **bloqueio no servidor, com chamadas DIRETAS às APIs, sem passar pela tela:** a conta
  sem credencial e com plano pago leva 403 `password_required` em rota de dados, em
  `GET /ai/messages`, `POST /ai/chat`, `POST /auth/link-code`, `/billing/portal`,
  `/billing/subscription` e
  `/billing/change-plan`, e na `/api/v2/me`. O `GET /conta` **não** redireciona para o
  portal do Stripe. No WebSocket, o **código de fechamento**
  próprio (não um 403). A mesma conta
  consegue `/auth/me`, `POST /settings/{uid}/password-reset`,
  `PATCH /settings/{uid}/security/contact` (a troca de e-mail) e o logout. Depois
  de criar a senha, as rotas de dados voltam a responder 200.
- **Controles:** *negativo*: fazer `conta_sem_credencial` ignorar
  `auth_identities` deixa vermelho "só-Google = false"; tirar a perna nova do gate deixa
  vermelho o 403 da rota de dados; *positivo*: a conta sem senha ganha o gate, e a
  conta com senha continua com 200 em tudo.

### PR 5: a página `/assinar` (liga o funil)

**Muda:**
1. `frontend/routes/static_pages.py`: `GET /assinar` → `html_file(…, pixel=True,
   clarity=False)`, mais as rotas dos assets novos. `robots.txt`: `Disallow: /assinar`
   e `/q`.
2. `frontend/assinar.html`, `assinar.js` (≤ 350 linhas, o teto do eslint; se passar,
   separar o Stripe em `assinar-stripe.js`) e `assinar.css`. O script que lê o
   fragmento fica no `<head>`, **antes** do `</head>`, que é onde o `inject_tracking`
   injeta o Pixel. Carrega `auth-refresh.js` (`/static/auth-refresh.js`),
   `purchase-intent.js`, `safe-area.js` e `https://js.stripe.com/dahlia/stripe.js`, com
   `stripe.createEmbeddedCheckoutPage`. O Stripe.js tem de vir do Stripe. O `/v3/` dá
   "Something went wrong" com a API `dahlia` (resultado da etapa 0, no fim deste
   arquivo). Implementa a máquina da seção 4.
3. `frontend/quiz-resultado.js`: com `plano` válido na query (depois de gravar o cookie
   e limpar o fragmento, como já faz), `location.replace("/assinar?" + qs + "#" +
   new URLSearchParams({n, e, w}).toString())`. O fragmento é **montado com
   `URLSearchParams`**, nunca por concatenação, para um `&`, `#` ou `+` no nome ou no
   e-mail não quebrar os campos. Os outros ramos ficam iguais. O `+` do e-mail recebe o
   mesmo tratamento que já existe.
   **Tire `n` e `w` do fragmento junto com `p`, `r`, `e` e `c`.** Hoje o arquivo só
   remove `["p", "r", "e", "c"]`, e toda chave que sobra no fragmento vai para a query
   visível (`query.append`, depois `history.replaceState`). Sem isso, o nome e o WhatsApp
   do link novo do XQuiz (`…#…&n=…&w=…`) apareceriam na URL da `/q`, contra a D-a e a
   seção 7. Um teste em `quiz_resultado.test.mjs` confere a URL **da `/q`** depois da
   limpeza (sem `n`, `w` nem `e` na query), e não só a da `/assinar`.
4. `frontend/login.html`: `/assinar` entra na allowlist do `nextParam`. Quem manda
   para o login usa `encodeURIComponent` no valor do `next` (seção 4, S2).
5. Plano B (D-p), dentro do `assinar.js`: a constante `HOSPEDADO_UA = /PigBankApp/`
   (mais o que a Etapa 0b reprovar); um relógio de 10 s depois de chamar a montagem,
   que exige um `<iframe>` dentro do contêiner; o `onerror` do `<script>` do Stripe.js;
   o `.catch` da montagem; e o link manual no S4. Os quatro levam ao mesmo `irParaHospedado()`,
   que chama `destroy()` no embutido (se houver), faz o POST com `embutido:false,
   origem:"assinar"` e navega para o `checkout_url`. Um só caminho, chamado uma vez
   (uma guarda impede o relógio e o erro de dispararem juntos).
6. `docs/armadilhas.md`: a `assinar` entra na linha do shim da tabela "quem carrega o
   quê" (a soma tem de continuar batendo com `ls frontend/*.html | wc -l`).
7. O Coder usa a skill `pigbank-frontend` (rosa só no CTA principal; mobile primeiro).

**Não muda:** a `precos.html`, a `/continuar-compra`, o Pix e a `/home`.

**Pode quebrar:** `tests/test_frontend_assets_e_rotas.py` (página e asset sem rota
reprovam); `tests/frontend/quiz_resultado.test.mjs` (o ramo novo); o service worker
**não** muda (navegação e cross-origin passam direto, `service-worker.js:66,103`), então
não há bump do `CACHE_NAME`.

**Testes (Playwright, com os endpoints e o `Stripe` falsos):**
- **nenhuma requisição sai com PII na URL:** interceptar tudo, inclusive o
  `facebook.com/tr` e o GA4, e afirmar que nenhum `dl`/URL contém o e-mail, o
  telefone, o nome, `p=` ou `r=`. Controle negativo: mover o script do fragmento para
  depois do Pixel deixa o teste vermelho;
- cada transição da seção 4 (criada, logado, tem_conta com login ok/401/MFA, 409
  assinante, 409 pix_active, 401 → S1, 503, link inválido);
- recarga depois de `criada` → vai direto ao S3 sem o formulário;
- sessão de outro e-mail + fragmento com e-mail diferente → mostra o formulário (não
  cobra a conta errada);
- "Prefere Pix?" grava a intenção `pix/annual` com status `awaiting_auth` e navega
  para `/continuar-compra`;
- `/q?plano=plus&ciclo=monthly#p=…&r=…&e=a%2Bb@x&n=…&w=…` → grava o cookie e chega
  à `/assinar` com `a+b@x` preenchido, e a URL final não tem PII; um nome com `&` e
  `#` chega inteiro;
- **MFA com a query inteira:** na `/assinar?plano=plus&ciclo=annual&utm_source=ig`,
  login com `mfa_required` → a URL do `/login` tem o `next` codificado; depois do MFA
  (mockado), a página final é `/assinar?plano=plus&ciclo=annual&utm_source=ig`, com o
  ciclo e a UTM. Controle negativo: tirar o `encodeURIComponent` faz o teste chegar
  sem `ciclo` e ficar vermelho;
- **plano B:**
  (1) `js.stripe.com` bloqueado (`route.abort`) → o POST `embutido:false,
  origem:"assinar"` sai e a página navega para o `checkout_url` falso;
  (2) a montagem rejeita → o mesmo;
  (3) a montagem resolve mas não aparece `<iframe>` → o mesmo, depois de 10 s (relógio
  do Playwright adiantado, não espera real);
  (4) user agent com `PigBankApp` → nunca tenta montar, vai direto ao hospedado;
  (5) o link manual no S4 → hospedado;
  (6) o relógio e o erro juntos → **um** POST só.
  Controle negativo: tirar o relógio deixa o caso (3) vermelho. Controle positivo: com
  o user agent comum e a montagem ok, **nenhum** POST `embutido:false` sai.
- Desktop (1280×800) e mobile (390×844), com medida e não "pareceu ok".

**Verificação real antes do merge: a Etapa 0b-2** (túnel HTTPS, Stripe em modo teste,
no navegador do Instagram e do Facebook e no Safari e Chrome comuns): a compra de ponta
a ponta com o cartão comum, o de 3DS e o recusado; com e sem trial; com e sem e-book;
o plano B pelo link manual; a volta à `/home` com o overlay e o gate de senha; o e-mail
do e-book.

### PR 6: limpeza do #640 (depois da troca no XQuiz + 24 h)

- Remover: `/xquiz/webhook`, `/auth/quiz/resend`, o ramo `e/c` da `quiz-resultado.js`
  (e o formulário de código da `quiz-resultado.html`), `quiz_signup_pendente`, a
  entrada `/xquiz/webhook` de `CSRF_EXEMPT_PATHS`, `QuizLeadBody`/`_tokens`/
  `TETO_GLOBAL_WEBHOOK`, os testes deles e a menção no `docs/CLAUDE.md`.
- O ramo "código vivo" de `create_email_verification_impl` (`password is None`) pode
  sair: a `/assinar` funciona com código novo a cada chamada.
- **Dado:** um script de uso único, que o dono roda, apaga os leads que o webhook gravou
  e que nunca viraram conta (`email_verification_codes` com `password_hash is null` e
  `used_at is null`). É a D2 cumprida para trás.
- Testes: a rota removida dá 404/405; o `/cadastro` e o `verify-email` continuam verdes.

---

## 6. Segurança da conta criada sem senha e sem código

| Tema | Como fica |
|---|---|
| CSRF | A rota não entra em `CSRF_EXEMPT_PATHS`. O navegador pega o `csrf_token` no GET da `/q` ou da `/assinar`. Um POST cross-site não manda o header, e o JSON cross-origin precisa de preflight. |
| Limites | 10/h por IP e 3/h por e-mail (D-l). Sem teto global, porque ele viraria DoS do funil. A exposição a spam de e-mail de boas-vindas é a mesma do `/auth/register` de hoje. |
| Enumeração | **O único lugar do site que diz "este e-mail tem conta".** O `/cadastro`, o `/login` e o `forgot-password` escondem isso (monólito :3265, :3378, :3683). O próprio desenho exige dizer, e o dono aceitou (pergunta 6). A atenuação: os limites, e o "já é assinante" só aparece **depois** de entrar (a assinatura não vaza). |
| E-mail de outra pessoa ou com erro de digitação | A conta nasce com um e-mail não provado. **A prova vem no "Crie sua senha"** (link no e-mail, decisão do dono). Quem digitou errado paga, não recebe o link e vê "E-mail errado? Corrigir" (a `/settings` já troca o e-mail). Sem essa prova, o verdadeiro dono do e-mail poderia usar o "esqueci a senha" e ler as finanças de quem pagou. |
| Link forjado (`#e=atacante@x`) | O e-mail aparece e pode ser editado antes do Continuar. Para ganhar alguma coisa, o atacante precisaria que a vítima pagasse, e o gate de senha manda o link para o e-mail do atacante, então a vítima fica travada e percebe. Risco baixo, registrado. |
| "Esqueci a senha" em conta sem senha | Já funciona: `create_password_reset_token` acha pelo `email_hash`, a copy muda para "Definir senha", e o reset revoga as sessões. |
| Sessão sem senha | É a mesma do login: JWT de 15 min + refresh de 14 dias + CSRF. Antes de pagar, ela só abre o que conta sem plano abre hoje (o gate de plano manda para `/precos` e dá 402 nas rotas de dados). Depois de pagar, **continua sem abrir dado** até a senha ser criada: 403 `password_required` (PR 4, item 2b, decisão do dono). Não há token novo. |
| Telefone disputado | Descartado em silêncio pelo código de hoje, em `db_support.telefone_livre` (na busca) e `db_support.gravar_descartando_telefone_disputado` (na corrida do INSERT), as funções dos três criadores de conta: a conta nasce sem telefone e sem teste grátis, com o texto neutro D-m. **Risco antigo, que o v3 não piora:** o telefone não é confirmado no cadastro, então alguém pode registrar o número de outra pessoa (vale igual no `/cadastro` de hoje). Fica fora do escopo e vai para uma issue própria. |
| Tomada de conta pelo upsert | **Fechada pelo #658:** os três criadores passam por `inserir_conta_nova` (trava por e-mail + `ON CONFLICT DO NOTHING`) e recusam o e-mail que ganhou conta no meio. Ver a nota "Ajustes do PR 1". |

---

## 7. Rastreio

- **UTM e `fbclid`:** o XQuiz precisa repassar os dois **na query** do link do botão.
  A `/q` já passa a query adiante, e o Pixel na `/assinar` cria o `_fbc`. O
  `create-checkout` já lê `_ga/_fbp/_fbc` dos cookies para o metadata. Nada novo no
  backend.
- **Eventos:** `PageView` (Pixel, com a URL sem fragmento); `CompleteRegistration`
  (Pixel com `signup_<uid>` + CAPI no helper D-d, com dedupe); `sign_up` no GA4;
  `InitiateCheckout`/`begin_checkout` quando o Stripe monta; `StartTrial`/`Purchase`
  como hoje (a `/home` com o `sid` e o CAPI do webhook).
- **Resultado do quiz:** só vai no cookie `quiz_result` e fica no banco, como hoje. Nunca
  em query, log, Pixel ou GA4 (o teste de PII do PR 5).
- **Lacuna conhecida:** com trial, a receita do e-book não vai para o GA4 nem para a
  Meta. O GA4 só manda `purchase` sem trial, e aí o `amount_total` já inclui o e-book.
  Se o dono quiser, é um follow-up.

---

## 8. Riscos e o que ficou de fora

- **Navegador do Instagram e do Facebook:** o checkout embutido e o 3DS dentro do WebView
  só se provam no celular. Isso é provado **antes** (Etapa 0b-1 antes do PR 2 e 0b-2
  antes do merge do PR 5), e o plano B (D-p) cobre o que falhar depois. O que continua
  só em produção: o link real do anúncio e o cartão real com 3DS de banco brasileiro.
- **O plano B sai do domínio:** no checkout hospedado, a pessoa vê `checkout.stripe.com`
  durante o pagamento. É o preço de não perder a venda.
- **Iframe que monta mas fica em branco** (por exemplo, cookie de terceiros bloqueado
  dentro do iframe) não é detectado pelo relógio. Quem cobre é o link manual, sempre
  visível.
- **App iOS:** o funil não passa pelo app (o AASA só tem `webcredentials`, sem
  `applinks`, então o link abre no navegador). Se alguém abrir a `/assinar` no app, o
  comportamento é o da `/precos` de hoje. Não se cria exceção, e a regra da Apple sobre
  venda dentro do app continua a mesma de hoje.
- **Abandono e volta em outro navegador:** a pessoa cai em "já tem conta" sem ter senha.
  O caminho é o link por e-mail → login → `/precos` (checkout hospedado, sem e-book).
  Um "entrar por código no e-mail" resolveria isso, e fica como follow-up se a métrica
  mostrar que dói.
- **Duas cobranças (Pix + cartão)** em abas diferentes: a janela vai de 24 h para 1 h
  (D-n), nos dois modos da `/assinar`. Não foi fechada de todo.
- **Contas sem plano criadas por bots:** somam na base e podem entrar nos e-mails de
  ciclo de vida de quem não assinou (o mesmo de hoje com o `/cadastro`). O Coder faz o
  inventário no PR 1 (`grep` dos jobs que mandam e-mail para conta sem plano) e cita
  no PR.
- **Troca de telefone na `/settings` para ganhar outro teste:** existe hoje, e o v3 não
  mexe.
- **Cupons ligados + e-book:** um cupom sem restrição de produto pode descontar o
  e-book também. Para limitar, o dono restringe o cupom aos produtos dos planos no
  Stripe (seção 9). O código só garante que a comissão e o e-mail de cobrança usam o
  valor líquido do plano.
- **Fora do escopo:** mudar o plano ou o ciclo dentro da `/assinar`; o e-book pelo Pix;
  o e-book dentro do painel; o login por código.

---

## 9. O que o dono configura

**Stripe (fazer em modo teste primeiro, depois em produção):**
1. Produtos → criar "E-book …" com **preço avulso** (pagamento único, BRL) → copiar o
   `price_…` para a env **`STRIPE_PRICE_ID_EBOOK`** (Railway; a de teste vai no `.env` local da Etapa 0b-2).
2. Desenvolvedores → Chaves de API → copiar a **chave publicável** (`pk_live_…`, e a
   `pk_test_…` no `.env` local da Etapa 0b-2) para **`STRIPE_PUBLISHABLE_KEY`**.
3. Configurações → Marca: logo, ícone e cor `#FF2D8E`. O formulário embutido usa isso.
4. (Opcional, para o Apple Pay) Configurações → Métodos de pagamento → Domínios →
   adicionar `pigbankai.com`.
5. Webhook: nada muda (os eventos `checkout.session.completed` e `invoice.paid` já estão
   ligados).
6. Hospedar o PDF do e-book (por exemplo, no Drive, com "qualquer pessoa com o link") →
   a URL de download na env **`EBOOK_URL`**.
7. Cupons: como eles ficam ligados na `/assinar`, na hora de criar cada cupom, em
   "Aplicar a produtos específicos", escolher só os planos, para o cupom não descontar
   o e-book (a não ser que seja essa a intenção).
8. Antes do PR 2: a Etapa 0b-1 (dez minutos no celular, seção 5).

**XQuiz (só depois da Etapa 0b-2 e do PR 5 no ar e testado em produção):**
1. Em cada botão da página dos planos, um link neste formato (os nomes das variáveis
   seguem a sintaxe do XQuiz):
   `https://pigbankai.com/q?plano=plus&ciclo=monthly#p={perfil}&r={respostas}&n={nome}&e={email}&w={whatsapp}`,
   com `plano` = `essencial|plus|pro` e `ciclo` = `monthly|annual`. Se o XQuiz tiver
   "repassar parâmetros da URL", ligar para levar `utm_*` e `fbclid` na query.
   Os valores das variáveis precisam sair **codificados para URL** (um nome com `&` ou
   `#` quebraria os campos). Conferir no teste do passo 3 com um nome desses.
2. **No mesmo salvamento:** desligar a integração de webhook que aponta para
   `/xquiz/webhook`.
3. Fazer o quiz com os próprios dados e conferir que chega na `/assinar` preenchida.

**Railway:** logo depois do passo 2 do XQuiz, **apagar a env `XQUIZ_WEBHOOK_TOKEN`**. Aí
o webhook responde 503 e nenhum lead entra no banco, mesmo que o XQuiz ainda dispare.
Depois de 24 h, o PR 6.

**Ordem da troca, sem janela quebrada:** Etapa 0b-2 feita → PR 1–5 no ar → teste real
em produção (compra própria + estorno) → XQuiz (links novos + webhook off, juntos) →
apagar a env → 24 h → PR 6 → rodar o script de limpeza dos leads.

---

## 10. Critério de pronto (para o Tester)

- Baseline da suíte (skill `baseline-testes`) antes de cada PR, comparada por **nome**.
- Os controles negativo e positivo de cada PR (seção 5), com o vermelho visto.
- Classes de falha a caçar: `next=` ou fragmento montado sem encoding (qualquer URL
  com query dentro de query); o plano B disparando duas vezes, ou disparando com o
  embutido funcionando; e-book ausente no hospedado da `/assinar`; conta existente alterada pela rota nova; PII em qualquer URL
  ou log; e-mail de cobrança ou comissão sobre o e-book; e-book entregue duas vezes;
  sessão Stripe de um modo reaproveitada pelo outro; o gate de senha em conta só-Google
  ou só-Apple; o gate atrás de outro overlay; conta errada cobrada com a sessão de outra
  pessoa no navegador.
- Relato separando **verificado aqui** (pytest, Playwright, túnel da Etapa 0b-2 com Stripe em teste)
  de **só no celular ou em produção** (navegador do Instagram, 3DS de cartão real,
  entrega real dos e-mails, o XQuiz).

---

## 11. Perguntas ao dono (respondidas; a resposta está no fim da seção e vale por cima)

1. **Cupons no checkout da `/assinar`.** Hoje o checkout aceita código de cupom
   (`allow_promotion_codes=True`). No funil do quiz, um campo "tem cupom?" faz a pessoa
   sair para procurar um. **(a) Desligar só na `/assinar` [recomendado]**; (b) manter
   como na `/precos`.
2. **Como é o "Crie sua senha" obrigatório.** **(a) Um aviso que bloqueia o painel com
   o botão "enviar link para o seu e-mail"; a senha é criada pelo link [recomendado]**:
   prova que o e-mail é da pessoa e reaproveita o "Definir senha" que já existe, sem
   backend novo; o custo é que a pessoa sai para o e-mail e entra de novo. (b) Os campos
   de senha ali mesmo no painel, sem prova do e-mail: é mais rápido, mas quem digitou o
   e-mail errado ou de outra pessoa deixa as próprias finanças abertas para o dono
   daquele e-mail (pelo "esqueci a senha"). (c) Código de 6 dígitos no e-mail + senha
   ali mesmo: prova o e-mail e não sai da página, mas é o maior dos três em código novo.
3. **Entrega do e-book.** **(a) Um e-mail com um link fixo para o PDF que você hospeda
   (env `EBOOK_URL`) [recomendado]**: o link pode ser repassado, o que é aceitável
   num e-book barato. (b) Download protegido no nosso site, só para quem comprou: é
   mais código e precisa guardar quem comprou.
4. **Comissão de afiliado sobre o e-book.** **(a) Não, a comissão é só sobre o plano
   [recomendado]**; (b) sim, sobre a fatura inteira, como seria hoje por acidente.
5. **As contas já criadas pelo quiz antigo (#640), sem senha, também ganham o aviso
   obrigatório de senha?** **(a) Sim [recomendado]**: é o mesmo caso, e hoje elas só
   entram de novo pelo "esqueci a senha". (b) Não, só as contas novas da `/assinar`
   (precisaria de uma marca na conta para separar).
6. **Confirmar a enumeração.** O "Você já tem conta" que você pediu faz da `/assinar` o
   único lugar do site onde dá para testar se um e-mail é cliente PigBank (o cadastro,
   o login e o "esqueci a senha" escondem isso de propósito). **(a) Aceitar, com o limite
   de 10 tentativas por hora por IP e 3 por e-mail [recomendado]**; (b) trocar a
   mensagem por "enviamos um link para o seu e-mail" para quem já tem conta: esconde
   melhor, mas quem já tem conta sai da página.

---

## Respostas do dono às perguntas pendentes (2026-09-27): valem por cima do texto acima

1. **Cupons na `/assinar`: LIGADOS** (como na `/precos`). Não desligar `allow_promotion_codes`.
2. **"Crie sua senha": opção (a)** — o aviso trava e manda link por e-mail (reusa o "Definir senha"), provando o e-mail.
3. **E-book: e-mail com link de download** para o PDF hospedado pelo dono.
4. **Comissão de afiliado: só sobre o plano**, nunca sobre o e-book.
5. **Contas antigas do quiz (#640): não precisa tratar** — o dono afirma que nenhuma conta foi criada pelo quiz ainda. Não fazer contagem em produção.
6. **"Você já tem conta" na `/assinar`: aceito**, com os limites (10/h por IP, 3/h por e-mail).

## Ajustes do PR 1 (2026-09-27), valem por cima da D-c (seção 2), do PR 1 (seção 5) e da seção 6

- A "tomada de conta pelo upsert" deixou de ser limite aceito: os três criadores de conta (confirm do register, Google/Apple, `/assinar`) passam por `inserir_conta_nova` (advisory lock por `email_hash` + `ON CONFLICT DO NOTHING`) e recusam, sem sessão, e-mail que já tem conta. A `/assinar` não usa `email_verification_codes` (`criar_conta_sem_codigo`) e responde `cadastro_pendente` quando há register com senha em curso, e 409 `ocupado` quando a trava está tomada (não espera). Balde por e-mail próprio: `quiz-conta`.
- O `/xquiz/webhook` (#640) ainda usa o balde `register`: sai no PR 6.

## Resultado da etapa 0 (2026-09-27), vale por cima do texto acima

- **Embutido FUNCIONA dentro do navegador do Instagram (iOS)**, provado pelo dono com a prova de conceito em modo de teste: cartão 4242 → `status complete`, `payment_status paid`, assinatura `trialing`; cartão de 3DS → a tela de verificação abriu e fechou, `complete`. O hospedado também funciona no Instagram (Payment Link de teste). Consequência: `HOSPEDADO_UA` começa só com `PigBankApp`; Instagram/Facebook ficam no embutido, com o plano B de sempre.
- **E-book com trial é cobrado NA HORA:** sessão com e-book → `amount_total 990` e 1ª fatura paga 990 com a assinatura em `trialing`. Sem e-book → 0.
- **`ui_mode="embedded_page"`** (o `"embedded"` é recusado pela API 2026-08-26.dahlia). O hospedado volta `hosted_page`.
- **Stripe.js precisa ser o versionado:** `https://js.stripe.com/dahlia/stripe.js` e `stripe.createEmbeddedCheckoutPage({fetchClientSecret})`. Com `js.stripe.com/v3/` o formulário mostra "Something went wrong". A CSP libera `js.stripe.com` (o caminho `/dahlia/` está dentro).
- **Moeda:** o Stripe mostrou USD/BRL (Adaptive Pricing) no navegador de fora do Brasil. Fixar BRL na sessão do quiz (desligar o adaptive pricing).
- `optional_items` com trial aceito nos dois modos.
