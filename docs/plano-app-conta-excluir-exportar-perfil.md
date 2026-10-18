# Plano: Excluir conta, Exportar dados e Perfil no app nativo

> **Só plano.** Nenhum código de produção foi escrito. O dono aprova antes de qualquer PR de código.
> Escrito em 2026-10-17 sobre a `origin/main` em `484e995b` (inclui #1069 e #1082).
> Cobre dois pedidos que andam juntos: (A) Excluir conta e Exportar meus dados no app, e (B) a issue #1083, o Perfil do app (par do #1082).
> Faixa de todos os PRs de código abaixo: **Completo** (conta, sessão, dado do usuário), salvo onde escrito o contrário.
> Origem das regras: `docs/paridade-app-dashboard.md` (linhas "Excluir conta e exportar dados" e "Contato"), CLAUDE.md §4 (paridade) e a regra do dono "app e dashboard andam juntos".

## 0. Resumo para decidir

1. **O servidor já serve o app.** As duas rotas aceitam Bearer, o CSRF não pega quem manda Bearer sem cookie, e o prefixo `/auth` é isento do corte de plano. O app consegue chamá-las hoje, sem rota nova, **para quem tem senha**.
2. **O buraco real é quem não tem senha** (entrou só por Google ou Apple): hoje exclusão e exportação respondem 409 "defina uma senha". Quem criou conta com a Apple, o caso que o revisor da loja testa, não consegue excluir. Isso pede uma decisão do dono (pergunta 1) e um PR de servidor.
3. **A exclusão tem três lacunas que o app herdaria**, todas medidas no código (seção A.2): não existe cancelamento (o texto do site promete que existe), nada cancela a assinatura Stripe, e não há revogação do token da Apple. A terceira bloqueia a submissão à App Store.
4. **Exportar não precisa de arquivo no app.** O servidor manda um link de uso único por e-mail; o app só pede e mostra "enviamos". Zero mudança de servidor para quem tem senha.
5. **Perfil (#1083)** reaproveita `GET /settings/{uid}/security` e `PATCH /settings/{uid}/security/contact` sem rota nova. Os códigos de erro são os mesmos do web, e o 401 "Senha incorreta." já tem tratamento no cliente (`credencialSecundaria`).
6. **Ordem sugerida:** S1 (servidor, reautenticação) → **S2 (Stripe) ou, no mínimo, a guarda que recusa excluir com assinatura viva** → P1 (Perfil, leitura) → C1 (Conta: exportar e excluir) → P2 (Perfil, edição) → S3 + C2 (Apple). O C1 não pode sair antes do S2: sem tratar o Stripe, o app passaria a oferecer um botão que deixa a pessoa pagando sem saída (L3). Detalhes na seção D.
7. **Revisão cruzada feita.** Um segundo agente (`manager`) conferiu este plano contra o código em 2026-10-17 e achou 8 erros factuais e 11 lacunas; os que se confirmaram estão corrigidos abaixo. As perguntas ao dono (seção F) cresceram por causa disso.

---

## A. Excluir conta e Exportar meus dados

### A.1 As rotas aceitam a sessão do app? (pergunta 1)

**Sim, as duas.** Medido lendo o código:

| Item | Resultado | Onde |
|---|---|---|
| Identidade | `resolve_dashboard_user_id` lê `extract_bearer_token(request)` e só depois o cookie. Valida o `jti` em `auth_sessions`. | `frontend/routes/shared.py:1036` |
| Rotas | `POST /auth/account/export`, `GET /auth/account/export/download/{token}`, `DELETE /auth/account` | `frontend/finance_bot_websocket_custom.py:4347`, `:4447`, `:4522` |
| CSRF | O middleware só exige o par cookie+header quando a requisição traz cookie. O app manda `credentials: omit`, então passa. É o mesmo caminho das escritas de MFA que o app já usa. | `finance_bot_websocket_custom.py:2405` (`_sem_credencial_ambiente`), `docs/api-app-painel.md` |
| Corte de plano | `_GATE_EXEMPT_PREFIXES = ("/billing", "/auth", "/conta")`: quem perdeu o plano ainda exclui e exporta. | `shared.py:1067` |
| Teto | Exclusão 5/min por IP. Exportação 3/h por IP, mais 1 link pendente por usuário: `has_recent_export_request` exige `used_at is null and expires_at > now()`, então na prática o bloqueio dura os **15 min** de validade do token, não 60 (429 com `Retry-After`). **Defeito que já existe e o C1 vai expor:** o token é criado (`:4401`) antes do e-mail; se o envio falha (500, `:4433`), a nova tentativa toma 429 "já existe um link enviado" por 15 min, o que é falso. | `:4523`, `:4348`, `:4381`, `db/privacy.py:158` |
| 401 da senha | "Senha incorreta." sai **sem** `WWW-Authenticate`. O cliente do app já separa isso de "sessão expirou" com `credencialSecundaria: true` (`app/src/api/client.ts:315-327`, usado em `desativarMfa`). | `app/src/services/mfa.ts:39` |

Contrato atual: `DELETE /auth/account` com corpo `{password}` devolve `{ok, user_id, status:"scheduled", deletion_scheduled_for, grace_days:7, message}`. `POST /auth/account/export` com `{password}` devolve `{status:"email_sent", expires_in_minutes:15, expires_at}`.

### A.2 O que a exclusão faz hoje (pergunta 2)

**Agendar (síncrono, no `DELETE`)**: `db/privacy.py:263` (`schedule_account_deletion`)
- Confere a senha. Marca `auth_accounts.deletion_status='scheduled'`, `deletion_scheduled_for = agora + 7 dias`. Repetir o pedido não move a data.
- Apaga `dashboard_sessions`, `link_codes`, `platform_onboarding_tokens` e `password_reset_tokens` do usuário.
- **Não** apaga `auth_sessions` nem os refresh tokens. O 403 `conta_em_exclusao` vem de **quem chama a guarda**, não de `resolve_dashboard_user_id` (que não a chama, `shared.py:1036-1062`): `_get_current_user` (`:3102`), `authorize_account_access` (`shared.py:1220`), `api/v2/sessao.py`, as rotas de export (`:4362`, `:4472`), `_concluir_login` (login e Google/Apple `exchange`), `mfa/verify-login` e `/d/`. **Sem guarda**: `/auth/refresh`, `/auth/logout`, parte de `onboarding.py` e `push.py` e o próprio `DELETE /auth/account`. Logo, a conta agendada **continua renovando o access token** pelos 7 dias; só as rotas de dados respondem 403.
- Manda o e-mail "Exclusão de conta agendada" e limpa os cookies (o app não usa cookie).

**Concluir (job)**: `process_due_account_deletions` roda por `scripts/account_deletion_job.py` (Railway Cron, 06:00 UTC, `docs/railway_account_deletion_job.md`) e por `_account_deletion_worker` (de hora em hora, dentro do app).
- Apaga os items na Pluggy (best-effort: falha remota loga e a exclusão segue; decisão do dono, prazo da LGPD ganha do lock).
- Apaga todas as tabelas de `user_owned_tables` e a linha de `users` (cascade), incluindo `user_identities`, MFA e o telefone/WhatsApp (que moram em `auth_accounts`).
- **Fica de propósito**: `plan_trials` (trava de teste por telefone, só desvincula o `user_id`) e `pix_charges` (obrigação fiscal; os identificadores de rastreio e o QR são zerados).
- Manda o e-mail "Conta excluída" sem registrar o endereço no log.

**Confirmação hoje**: só a senha. Sem código de MFA, mesmo com MFA ligado.

**Lacunas medidas**

| # | Lacuna | Evidência | Efeito |
|---|---|---|---|
| L1 | **Conta sem senha não exclui.** `PasswordNotSetError` vira 409 "Sua conta ainda não tem senha". | `db/privacy.py:286`, `finance_bot_websocket_custom.py:4531` | Quem criou conta com Apple ou Google não consegue excluir no app. |
| L2 | **Não existe cancelamento.** O texto de `frontend/settings.html:2966` diz "Você pode cancelar a exclusão fazendo login novamente nesse período". Mas o login devolve 403 para conta agendada (`_concluir_login`, `finance_bot_websocket_custom.py:3616`), e os comentários de `frontend/routes/open_finance.py:1063` e `:1239` dizem que **não existe** cancelamento. `grep -rn cancel_account_deletion` acha só esses comentários. | `settings.html:2966`, `shared.py:991` | O texto promete algo falso. Quem teve a sessão roubada só recupera a conta pelo suporte, no banco. |
| L3 | **Nada cancela a assinatura Stripe.** `grep` por `Subscription.cancel/delete/modify` acha só `cartao_recusado_por_pix.py:62` e `pix_drain_effects.py:191`. `stripe_customer_id` mora em `auth_accounts` e some com ele. | `db/privacy.py` e `scripts/account_deletion_job.py` não citam Stripe | A cobrança pode continuar depois da exclusão, **e a pessoa não consegue cancelar sozinha**: `/billing/portal` usa `_get_current_user` (403 para conta agendada, `:3102`, `:7239`) e, depois do job, o `stripe_customer_id` some. **Hipótese não rodada:** o `user_id` é determinístico pelo e-mail ou pelo provedor (`db/users.py:418`), então quem exclui e se cadastra de novo com o mesmo e-mail recebe o mesmo `user_id`; o webhook resolve o dono por `metadata.finbot_user_id` (`:6069`), e uma assinatura velha não cancelada cobraria o cartão antigo e aplicaria `invoice.paid` à conta nova. Uma troca de plano agendada (`SubscriptionSchedule`, `:5930`) também precisa ser liberada ao cancelar. **Não verificado em runtime.** |
| L4 | **Sem revogação do token da Apple.** O app manda só o `identity_token` (`app/src/features/auth/apple.ts`); o backend só verifica o JWKS (`core/services/apple_signin.py`) e não tem chave `.p8`, Team ID nem Key ID. | grep de `revoke` em `core/frontend/db` | Segundo o que sei das regras da Apple (confirmar na documentação oficial antes de submeter), o app que oferece "Entrar com a Apple" precisa revogar o token ao excluir a conta. **Bloqueia a submissão.** |
| L5 | **O bot do WhatsApp não checa a exclusão agendada.** `grep` de `deletion|exclu|agendad` em `core/handle_incoming.py` não acha nada; o e-mail promete "conta bloqueada". | `core/services/email_service.py:1003` | Durante os 7 dias o WhatsApp provavelmente segue gravando. **Não testado.** Depois do job o vínculo some (telefone em `auth_accounts`, identidades em `user_identities`) e o número vira "desconhecido". |
| L6 | **Exportar fica bloqueado depois de agendar** (403 em `:4362` e `:4472`). Quem excluir sem exportar antes perde o direito de baixar. | `:4362` | A ordem na tela importa (A.3). |
| L7 | **Sessão antiga viva.** Refresh tokens e `auth_sessions` não são revogados ao agendar, e `/auth/refresh` não tem guarda: outros aparelhos (e o próprio, se `sair()` falhar no cofre, `sessao.tsx:92-115`) seguem logados e renovando por 7 dias, com 403 nas rotas de dados e sem nunca cair em `SessaoExpirada`. O app também não tem estado "conta em exclusão" (`conta_em_exclusao` só existe em `features/auth/google.ts:42`). | `db/privacy.py:263-320`, `finance_bot_websocket_custom.py:3747-3863` | Não é só higiene: ver L9. A revogação fecha os dois. |
| L8 | **A carência não para os envios proativos.** Nenhum job de `core/` (relatório diário, e-mails de engajamento, agentes) filtra exclusão agendada; só `scripts/send_update_whatsapp.py:113` filtra. O e-mail de agendamento promete "conta bloqueada" (`email_service.py:1006`). | revisão cruzada | Além de o bot gravar (L5), a conta continua recebendo mensagens. |
| L9 | **Corrida em `schedule_account_deletion`.** Um segundo `DELETE` com `deletion_status='processing'` cai no `else` (`db/privacy.py:295-310`) e volta a `scheduled` com mais 7 dias. Se o job falhar, `_restore_account_deletion_schedule` (WHERE `processing`) não faz nada e a exclusão atrasa uma semana. Alcançável porque as sessões não são revogadas. | revisão cruzada | O S1 deve (a) recusar reagendar conta em `processing` e (b) revogar as sessões (L7). |
| L10 | **Exportar e excluir em seguida mata o link.** O download recusa conta agendada (`:4472`): quem pede a exportação e exclui nos 15 min seguintes perde o link já enviado. A ordem das linhas na tela (A.3) não protege disso. | `:4472` | A tela do "Excluir" avisa em uma linha quando há link pendente, ou o S1 libera o download de links já emitidos (pergunta 9). |

**Assinatura da Apple (IAP)**: o app não vende nada (grep de `storekit|iap|revenuecat` em `app/` vazio), então não há assinatura da loja para cancelar. Quem paga, paga no site (Stripe) ou por Pix.

### A.3 Fluxo nativo (pergunta 3)

Regras do dono: **visual, mínimo de texto, quanto mais nativo melhor.**

**Onde**: `app/app/(app)/configuracoes.tsx` ganha um grupo "Conta" entre "Falar com o suporte" e "Sair". Duas linhas, como as existentes (`ListRow`):
- "Exportar meus dados" (chevron). A de cima, de propósito: a exportação some depois de excluir (L6).
- "Excluir conta" (`tom="danger"`, igual ao "Sair").

**Excluir**
1. Toque abre uma **sheet** (mesmo padrão de `mfa-desativar.tsx`: `SheetConteudo` + `TemaProvider`, rota nova `excluir-conta` no `_layout.tsx` com `OPCOES_SHEET`). Campo de senha (ou o passo de reautenticação da seção A.4) e o botão "Excluir conta" (`variante="danger"`).
2. Ao enviar, `Alert.alert` destructivo nativo, como em `DesativarMfa.tsx`: título "Excluir a conta?", uma frase ("Seus dados serão apagados em 7 dias."), botões "Cancelar" e "Excluir". Sem tela de texto longo. A frase de 7 dias é a única copy obrigatória e precisa ser verdadeira (ver L2).
3. `DELETE /auth/account` com `credencialSecundaria: true` (para o 401 da senha errada não virar logout).
4. Sucesso: um `Alert` com a data ("Exclusão em 24/10"), depois `useSessao().sair()`. O `sair()` limpa o cofre **antes** de chamar `/auth/logout`, e o logout **não tem a guarda de exclusão**: responde 200 e revoga a sessão e os refresh tokens daquele aparelho (os outros aparelhos seguem como em L7). A tela de entrada reaparece sozinha pelo `Stack.Protected`. Se `sair()` devolver `false` (falha do cofre), o aparelho fica logado numa conta agendada: a sheet precisa mostrar "Conta em exclusão" com a data e um botão "Sair", e o app precisa tratar o 403 `conta_em_exclusao` das rotas de dados do mesmo jeito (hoje só o fluxo do Google o conhece).
5. Falhas: 400 "Informe sua senha" (senha vazia), 401 senha (campo, sem sair), 404 (`LookupError`, conta de login não encontrada), 409 sem senha (leva ao passo A.4), 429 (mensagem curta), rede (mantém a sheet). Nenhuma delas encerra a sessão.

**O que ainda sobra no aparelho** (medido): o cofre guarda 3 chaves em `app/src/storage/secure.ts`: `pb.credenciais`, `pb.trava.desligada` e `pb.of.tentativa`. O `sair()` apaga as duas primeiras do par sessão/tentativa. Sobram `pb.trava.desligada` (preferência do aparelho, aceitável) e **os layouts do painel por usuário** (`chaveLayout(uid, perfil)` em `app/src/features/painel/provider.tsx:157-183`), que o `sair()` comum também não apaga. Decisão pequena: limpar os layouts do `uid` excluído na mesma ação. O cache em memória do painel some com o processo.

**Exportar**
1. Toque abre a sheet de senha (conta com senha) ou vai direto ao botão "Enviar para o meu e-mail" (conta sem senha, sem reautenticação: o link já vai para o e-mail da conta, A.4). Se o envio falhar, a nova tentativa toma 429 por 15 min (defeito do servidor, A.1; o S1 conserta).
2. `POST /auth/account/export` (`credencialSecundaria: true`). Sucesso: linha de estado "Enviamos o link para o seu e-mail. Vale 15 min." com o e-mail mascarado só se o servidor devolver (hoje não devolve; ver pergunta 4).
3. O link abre no navegador do aparelho (`GET /auth/account/export/download/{token}`, sem sessão, uso único) e o iOS baixa o ZIP para o app Arquivos.

### A.4 Quem não tem senha (L1) e a reautenticação

A senha é hoje a única prova de que quem segura a sessão é o dono (defesa contra sessão roubada). Para quem não tem senha, três caminhos:

| Opção | Como | Prós | Contras |
|---|---|---|---|
| **a. Definir senha primeiro** | É o que o site faz (manda o link de `password-reset`). O app já tem `EsqueciSenha` na tela Segurança. | Zero servidor. | O revisor testa com conta Apple recém-criada e dá com "defina uma senha pelo e-mail". Atrito alto. **E o reset de senha revoga todas as sessões** (`finance_bot_websocket_custom.py:3921`; comentário em `EsqueciSenha.tsx:16`): o app é deslogado no meio do fluxo. |
| **b. Código de 6 dígitos por e-mail** | Novo `POST /auth/account/reauth-code` envia o código para o e-mail da conta; `DELETE` aceita `code` no lugar de `password`. **Precisa de armazenamento próprio**: `email_verification_codes` é a tabela do cadastro (chave por `email`, `password_hash NOT NULL`, sem `user_id`, sem propósito, sem contador de tentativas, `db/schema.py:1236`) e o `/auth/quiz/conta` a lê para responder `cadastro_pendente`; misturar os dois códigos seria bug. | Serve Google e Apple com um mecanismo só. Também resolve o site (hoje a conta só-Google fica presa). | Servidor novo, Completo: tabela própria (`user_id`, propósito, hash do código, `expires_at`, tentativas, uso único), teto de **tentativas por código e por conta** (o 5/min do `DELETE` é por IP e quem tem a sessão troca de IP), e teto de envio. **Pré-requisito a verificar antes:** o e-mail "Ocultar meu e-mail" da Apple só repassa mensagem de domínio remetente registrado no Apple Developer; nada no repositório mostra esse registro. Se o domínio do Resend não estiver registrado, o código não chega a quem entrou com a Apple, que é justamente o caso do revisor. |
| **c. Reautenticar com o provedor** | Apple: `signInAsync` de novo, servidor verifica o identity token e confere se o `sub` é o da conta. Google: fluxo OAuth de novo. | É a prova mais forte, e **no caso da Apple entrega o `authorizationCode` de que a revogação (L4) precisa**. | Mais código no cliente e no servidor; Google exige abrir o navegador. |

**Recomendação:** `code` (opção b) **só para conta SEM senha** no PR S1; conta com senha continua só com `password`. Aceitar `password` **ou** `code` para todos deixaria quem roubou a sessão e lê o e-mail pular a senha, o que é decisão de segurança do dono (pergunta 1). A opção c fica **só para a Apple**, no S3, porque também entrega o código da revogação. Quem tem MFA ligado manda também o TOTP **ou um código de backup** (o `/auth/mfa/disable` aceita os dois; sem isso quem perdeu o autenticador não consegue excluir). Pergunta 3.

**Exportar não precisa de `code`**: o link já vai para o e-mail da conta, então a posse do e-mail já é o fator. Pedir código antes dele cria dois e-mails seguidos sem ganho. Recomendo, para conta sem senha, **dispensar a reautenticação no export** (o ganho: zero servidor novo no export); o `DELETE` é que pede a prova.

Regra de compatibilidade: o app instalado não se atualiza com o deploy e o site velho segue mandando `{password}`. Por isso `DeleteAccountBody` e `DataExportBody` passam a ter `password` **opcional** e ganham `code` e `totp` opcionais. O servidor só acrescenta campo.

### A.5 Exportar: como entregar (pergunta 4)

Recomendação: **manter a entrega por e-mail**.

- Hoje o ZIP nunca viaja na resposta da sessão: `build_user_export_zip` monta os bytes **em memória** (`db/privacy.py:334`) e só o link de uso único (15 min) o entrega. Isso é deliberado ("sessão roubada não basta"), e o aviso de conclusão também vai por e-mail.
- Entregar dentro do app exigiria uma rota nova autenticada por Bearer que devolve o ZIP, `expo-file-system` + folha de compartilhar, e a decisão de aceitar que quem segura a sessão e a senha leva todos os dados. Além disso o tamanho do ZIP **não foi medido**; um arquivo grande em memória no servidor e no aparelho é risco de falha.
- O que o app faz hoje por e-mail é suficiente para a LGPD e custa só a tela.

Limites declarados: conta sem e-mail (400), e-mail de retransmissão da Apple, link que expira em 15 min, e o 3/h por IP.

### A.6 Servidor × cliente e a menor quebra em PRs (pergunta 5)

**Servidor**
- **S1 (Completo)** reautenticação e carência: `password` opcional, `code` e `totp` (ou código de backup) opcionais em `DeleteAccountBody` e `DataExportBody`; tabela e rota `reauth-code` com tetos de tentativa e de envio (A.4); revogar `auth_sessions` e refresh tokens e recusar reagendar conta em `processing` em `schedule_account_deletion` (L7, L9); consertar o token de export criado antes do e-mail (E5 da seção A.1); corrigir a copy de `settings.html:2966` (L2). Mexe em `db/privacy.py`, `finance_bot_websocket_custom.py` (corpos e rotas), `db/schema.py` (tabela do código) e `frontend/settings.html`.
- **S2 (Completo, cobrança)** Stripe (L3), **antes do C1**: ao agendar, cancelar a assinatura (e liberar o `SubscriptionSchedule`) ou, no mínimo, uma guarda no `DELETE` que recusa com 409 enquanto houver assinatura Stripe viva e manda cancelar antes. Depende da pergunta 5.
- **S3 (Completo)** Apple (L4). Revogar exige o `authorizationCode` (vale cerca de 5 min, só existe no `signInAsync` nativo): uma exclusão iniciada pelo `/settings` ou pela C3 do `/painel` por conta com identidade Apple (inclusive a auto-vinculada por e-mail, #988) nunca revogaria. Decisão de desenho que falta: o servidor **guarda o refresh token da Apple no login** (para o job revogar de onde quer que a exclusão venha) e revoga no job, ou só revoga quando o app manda o código. Recomendo guardar no login, cifrado como o resto da PII. **Contas Apple que já existem** (criadas antes do S3) só entregaram o identity token e não têm nada guardado a revogar; guardar a partir do deploy não as alcança. Para elas: no app, exigir reautenticação com a Apple (`signInAsync` de novo, opção c da seção A.4) antes de aceitar a exclusão, o que entrega um `authorizationCode` fresco; na exclusão iniciada pelo `/settings` ou pelo `/painel`, o fallback é a revogação manual (o servidor instrui a pessoa a remover o PigBank em Ajustes > ID Apple > Entrar com a Apple). Conferir o texto exato da orientação na documentação da Apple (TN3194, que não li nesta sessão) antes de implementar. Precisa de segredos novos do dono (Team ID, Key ID, chave `.p8`). O S3 volta ao Arquiteto antes de entrar na fila.
- **S4 (Completo)** o bot do WhatsApp e os envios proativos checarem a exclusão agendada (L5, L8). Decide acesso no caminho de toda mensagem do WhatsApp (código compartilhado, área de segurança), então não é Leve. Pode virar issue.

**Cliente**
- **C1 (Completo)** grupo "Conta" em Configurações; sheets "Excluir conta" e "Exportar meus dados"; serviços `excluirConta` e `exportarDados` em `app/src/services/`; esquemas em `app/src/api/schemas/`; limpeza dos layouts do painel. Funciona para contas com senha antes do S1; com o S1 funciona para todas.
- **C2 (Completo)** Apple: pedir o `authorizationCode`, enviar no `DELETE`.

**Testes** (cada grupo com os dois controles do CLAUDE.md §3: desligue o conserto e veja vermelho; o caminho legítimo continua verde)
- Servidor: senha certa agenda; senha errada 401 sem `WWW-Authenticate`; conta só-Google com `code` agenda; conta só-Google sem nada 409; usuário B não agenda a conta de A (o `user_id` vem só da sessão); sessão revogada depois de agendar.
- Cliente (Jest): 401 de senha não chama `falha(SessaoExpirada)`; sucesso chama `sair()`; 409 abre o passo do código; cancelar o `Alert` não chama o servidor.
- Smoke pós-deploy: `scripts/post_deploy_deletion_check.py` já cria e exclui um usuário temporário via `DELETE /auth/account`; estender para o modo `code`.

### A.7 Riscos (pergunta 6)

| Risco | Estado | Mitigação proposta |
|---|---|---|
| Apagar conta alheia | `user_id` vem só da sessão (`_resolve_dashboard_user_id`), nunca do corpo ou do path. Sem endpoint com `{user_id}` para exclusão. | Manter. Teste cruzado A × B no S1. |
| Sessão roubada | Hoje: senha basta, MFA não é pedido, não há cancelamento. | Pedir TOTP quando há MFA (pergunta 3); e-mail "agendada" já existe; cancelamento por link (pergunta 2). |
| Exclusão sem volta | 7 dias de carência, sem caminho de volta além do suporte. | Corrigir a copy (L2) ou construir cancelamento. Atenção: o código assume que não existe cancelamento (`open_finance.py:1063,1239`: a guarda de adoção de item Pluggy depende disso). Construí-lo é um PR Completo próprio. |
| Cobrança depois de excluir | Stripe não é cancelado (L3) e a pessoa não consegue cancelar sozinha depois de agendar. | S2 **antes** do C1. |
| Força bruta do código por e-mail | Só o envio teria teto. | Teto de tentativas por código e por conta (A.4). |
| Token da Apple vivo | L4. | S3. |
| WhatsApp ativo na carência | L5. | S4 ou aceitar. |
| Enumeração de contas | A resposta do `code` não pode dizer se o e-mail existe; o teto vale por conta. | Teto no S1. |
| Perda dos dados por clique | Exportar fica abaixo do "Excluir"? | Exportar acima; a copy do `Alert` não ensina a exportar (mínimo de texto). |

---

## B. Perfil no app (issue #1083)

### B.1 O que o servidor já serve (medido)

- **Ler**: `GET /settings/{user_id}/security` (`frontend/routes/settings.py:277`). Devolve `email`, `display_name`, `phone`, `phone_status`, `phone_confirmed_at`, `whatsapp_verified_at`, `plan`, `plan_expires_at`, `created_at` e `identities`. Autoriza por `authorize_account_access` (aceita Bearer; só a perna do DIREITO do plano cai, então quem perdeu o plano ainda lê). O `{user_id}` do path tem de ser o da sessão (senão 403); o app o conhece por `perfil().user_id`. Faltam no GET os dois 402 (`plan_selection_required` do cadastro sem plano escolhido; só a perna do DIREITO cai, `shared.py:1221`). **"WhatsApp conectado" NÃO vem de `whatsapp_verified_at`**: o servidor define conectado como a existência da identidade (`user_identities`, provider `whatsapp`), `finance_bot_websocket_custom.py:3255-3265`, e o web lê `whatsapp_linked` de `/auth/dashboard-profile` (`webapp/src/dashboard/conta/Perfil.tsx:85`). Quem liga por código com número diferente do digitado fica ligado sem o marcador. O app lê `identities` do mesmo `/security` ou `whatsapp_linked` do `/auth/dashboard-profile`, nunca o marcador.
- **Escrever**: `PATCH /settings/{user_id}/security/contact` (`settings.py:286`). Corpo: `{display_name}` ou `{phone}` ou `{email, current_password}`. Usa `authorize_dashboard_access(exige_credencial=False)`, então **quem perdeu o plano leva 402** (decisão do dono do #380: não "conserte" isentando).
- **Conectar WhatsApp**: `POST /auth/link-code` (`:3939`, 15/h). Devolve `whatsapp_link` (`https://api.whatsapp.com/send?...`). Conta sem credencial leva 403 `password_required`.

Códigos do PATCH e o que o cliente faz (o web já fez a mesma tabela em `webapp/src/dashboard/conta/api.ts`):

| Status | Quando | App |
|---|---|---|
| 400 | nome <2 ou >50, telefone/e-mail inválido, nada para salvar, **senha atual vazia ao trocar e-mail**. `display_name: ""` NÃO é erro: apaga o nome (`settings.py:315`) | mostra o `detail` do servidor (já em português) |
| 403 | uid do caminho diferente do da sessão ("Acesso negado"); conta agendada para exclusão | estado "conta em exclusão" (A.3 passo 4) |
| 422 | corpo fora do formato | erro genérico |
| 401 | "Senha incorreta." **sem** `WWW-Authenticate` | `credencialSecundaria: true`; foco na senha; **não** é sessão expirada |
| 402 | `plan_selection_required` (cadastro sem plano escolhido) ou `subscription_required` (sem direito). O PATCH NÃO isenta a perna do DIREITO | estado "plano", não erro |
| 409 | e-mail/telefone em uso; conta **só-Google/Apple trocando e-mail** | em uso: foco no campo; sem senha: leva ao `EsqueciSenha` |
| 429 | teto de 5 trocas por hora (conta também as que dão 409) | mensagem curta |

Efeitos que o texto da tela precisa respeitar: trocar o **telefone** zera `whatsapp_verified_at`, `phone_confirmed_at` e põe `phone_status='pending'` (`settings.py:391-411`), mas **NÃO mexe em `user_identities`**: o WhatsApp já ligado continua ligado e o bot continua respondendo àquele número. Não há `Alert` de "vai desconectar"; trocar o telefone só deixa o telefone do cadastro como não confirmado. Trocar o **e-mail** avisa o e-mail antigo e agenda a atualização no Stripe.

### B.2 Decisões que o #1083 pediu e a recomendação

| Pergunta do issue | Recomendação |
|---|---|
| O app edita ou só mostra? | **Mostra e edita**, com o mesmo contrato do web e sem rota nova. P1 só mostra; P2 edita. |
| Onde mora? | Primeira linha de **Configurações** ("Perfil", com o nome e o e-mail do usuário), abrindo a tela `perfil` (cabeçalho nativo, como `seguranca`). Cada campo é uma linha que abre uma sheet de edição. |
| Quem entrou só por Google ou Apple | O e-mail só-Google leva 409; a sheet mostra o `EsqueciSenha` (já existe em `app/src/features/auth/EsqueciSenha.tsx`). Nome e telefone seguem editáveis. |
| Contrato | Idêntico ao do web. Ver B.3 para as duas dívidas de §0.7. |

### B.3 Pontos de atenção

- **Máscara do telefone** (§0.7): hoje há duas cópias (`formatPhoneBR` em `settings.html` e `telefoneBR` em `webapp/.../conta/api.ts`), presas por um teste. O app seria a terceira. Opção melhor: o servidor acrescentar um campo `phone_formatado` ao `GET /security` (só acrescenta, compatível). **Pergunta 6.**
- **Cache do `display_name`**: o app lê o nome de `perfil()` (`/auth/me`). Depois de editar, o app precisa reler `perfil()` (e o servidor já chama `invalidate_auth_user_cache`). **Não verifiquei** onde o app guarda o perfil em memória; é a primeira coisa a conferir no P1.
- **Link do WhatsApp**: o web valida o esquema (https ou `whatsapp://`) antes de abrir. O app deve validar o host (`api.whatsapp.com`/`wa.me`) e abrir com `Linking.openURL`.
- **Problemas de servidor já abertos que o app herda**: #1070 (pedir TOTP ao trocar e-mail com MFA) e #1071 (confirmar o e-mail novo; auto-vínculo do #988). O app não deve prometer mais segurança do que o servidor dá.
- **Telefone novo não desconecta o WhatsApp** (corrigido na revisão cruzada): sem `Alert` de desconexão. O telefone passa a "não confirmado"; o que liga o WhatsApp continua sendo só o código do `/auth/link-code`.
- **`link_whatsapp` devolve `""` sem `WHATSAPP_NUMBER`**: o web trata `sem_link`; o app também (esconde o "Conectar" e mostra o erro curto).
- **Apple**: e-mail de retransmissão (`privaterelay.appleid.com`) aparece como e-mail do perfil; não é erro.

### B.4 PRs do Perfil

- **P1 (Completo)** leitura: serviço `lerContato` (GET), esquema Zod, tela `perfil` com nome, e-mail, telefone mascarado, WhatsApp conectado ou "Conectar" (link-code) e "Desde …". Sem escrita de contato. Completo, e não Leve: o P1 lê dado pessoal e chama `/auth/link-code`, endpoint de autenticação, e o CLAUDE.md §0 manda a faixa mais alta na dúvida. "Conectado" vem de `identities`/`whatsapp_linked` (B.1), nunca de `whatsapp_verified_at`. Alternativa: o P1 sai sem o "Conectar" e fica Leve. **"Conectar" tem porta de credencial:** `/auth/link-code` responde 403 `password_required` para conta SEM credencial (sem senha E sem identidade Google/Apple, `db.conta_sem_credencial`: a conta do quiz/funil que ainda não criou a senha; quem entrou por Google ou Apple tem credencial e passa). O app já lê `precisa_criar_senha` do `/auth/me` (`perfilSchema`): com ele `true`, o P1 esconde o "Conectar" e leva à criação de senha (o `EsqueciSenha` da tela Segurança); se mesmo assim chegar o 403, trata como esse mesmo estado, nunca como erro genérico. Teste do P1: 403 `password_required` não mostra erro e não deixa o botão como beco sem saída.
- **P2 (Completo)** edição: sheets de nome, telefone e e-mail (senha atual), tabela de erros acima, confirmação do telefone, `credencialSecundaria`, releitura de `perfil()`.
- Testes P2 (Jest, com os dois controles): 401 de senha não derruba a sessão; 409 sem senha mostra o caminho; 402 mostra o estado de plano; cancelar não chama o servidor.

---

## C. Sobreposição com outras sessões

| Área | Quem | Onde colide | Como evitar |
|---|---|---|---|
| Menu Conta do `/painel` (C3 "dados/exclusão" em React, reusa as rotas antigas) | sessão do menu Conta (#1062, #1069, #1082) | **Mesmo contrato de servidor** (`DeleteAccountBody`, `DataExportBody`) e a mesma copy de `settings.html:2966`. Se a C3 sair antes do S1, ela só terá o fluxo de senha e a conta só-Google seguirá presa. | S1 entra **antes** da C3. A C3 lê o contrato novo (`code`, `totp`). |
| `settings.py` / `settings_contato.py` | #1069 e #1082 (mergeados) | O Perfil do app usa o PATCH de contato sem tocar nele. Nenhuma mudança proposta. | — |
| `app/app/(app)/configuracoes.tsx` e `_layout.tsx` | P1, P2, C1 | Os três PRs mexem nos mesmos dois arquivos. | Ordem estrita: P1 → C1 → P2, cada um rebaseado na `main`. Um deles abre a rota; os outros só acrescentam linhas. |
| Seu dia / Extrato, Previsão, Assinaturas do app | outras sessões | Mexem em `extrato.tsx`, `gastos.tsx` e nas abas, não em Configurações. | Sem sobreposição conhecida. |
| `docs/paridade-app-dashboard.md` | todos | Linhas 73 e 77 (exclusão/exportação e contato). | Cada PR de código atualiza a sua linha. |

## D. Ordem sugerida

1. **S1** (servidor, Completo): reautenticação por `code` (só conta sem senha), `totp` ou backup quando há MFA, revogação de sessões, sem reagendar conta em `processing`, copy corrigida.
2. **S2** (Stripe, Completo): cancelar a assinatura ao agendar, ou no mínimo a guarda 409. **Antes do C1.**
3. **P1** (Perfil, leitura, Completo): abre a navegação de Configurações.
4. **C1** (Conta, Completo): exportar e excluir, com o estado "conta em exclusão".
5. **P2** (Perfil, edição, Completo).
6. **S4** (WhatsApp e envios proativos na carência, Completo), em paralelo, independente do app.
7. **S3 + C2** (Apple, Completo): **antes de submeter à App Store**; o S3 volta ao Arquiteto (A.6). A data de submissão (pergunta 7) pode puxá-lo para antes do P2.

Cada PR de servidor traz o marcador do time-dev no corpo. Cada PR declara "Par no outro lado" (CLAUDE.md §4): S1 e a exclusão têm par na C3 do `/painel`; o Perfil já tem o web (#1082).

## E. O que foi medido e o que NÃO foi verificado

**Medido (arquivos lidos, `origin/main` em `484e995b`)**
- `frontend/finance_bot_websocket_custom.py`: rotas `:4347`, `:4447`, `:4522`; `_reauth_password_ok` `:4054`; `/auth/link-code` `:3939`; `_concluir_login` `:3618`; `csrf_middleware` `:2468`.
- `frontend/routes/shared.py`: `resolve_dashboard_user_id` `:1036`, `raise_if_account_scheduled_for_deletion` `:991`, `authorize_account_access` `:1149`, `_GATE_EXEMPT_PREFIXES` `:1067`.
- `db/privacy.py`: `schedule_account_deletion` `:263`, `delete_user_data` `:871`, `process_due_account_deletions` `:1371`, `build_user_export_zip` `:334`.
- `frontend/routes/settings.py` `:277-448` e `settings_contato.py:32`; `frontend/settings.html:2840-3040`; `docs/railway_account_deletion_job.md`.
- App: `api/client.ts`, `services/auth.ts`, `storage/secure.ts`, `configuracoes.tsx`, `seguranca.tsx`, `mfa-desativar.tsx`, `DesativarMfa.tsx`, `features/auth/apple.ts`, `features/painel/provider.tsx`, `(app)/_layout.tsx`.
- Verificação por `grep`: `cancel_account_deletion` (só comentários), `Subscription.cancel` (só Pix), `revoke` e Apple (nada), `storekit/iap` no app (nada), exclusão agendada no bot (nada).

**NÃO verificado**
- Nenhuma rota foi chamada. Tudo é leitura estática; nenhum teste foi rodado (sessão só de plano).
- Revisão cruzada (`manager`, 2026-10-17) também só por leitura. Tudo o que ela apontou e eu conferi (E1 `whatsapp_linked`, E4 tabela do cadastro, E5 janela de 15 min, E2/E3 guardas) está corrigido acima. Não conferi à parte: os números de linha novos de `settings.py:391-411` e `:315`, `shared.py:1221`, `finance_bot_websocket_custom.py:3921`, `db/users.py:418`, `:6069`, `:7239`, `scripts/send_update_whatsapp.py:113`.
- A hipótese de reaproveitamento do `user_id` com assinatura Stripe velha (L3), o comportamento do webhook do Stripe para conta apagada ou recriada, e o repasse do e-mail "Ocultar meu e-mail" da Apple (A.4).
- O comportamento do bot do WhatsApp durante a carência (L5), e do webhook do Stripe para cliente de usuário apagado (L3).
- A exigência atual da Apple sobre revogar o token do "Entrar com a Apple": é do meu conhecimento; confirmar na documentação oficial e com o revisor.
- Tamanho real do ZIP de exportação (A.5).
- Se `/auth/me` já devolve telefone (o `perfilSchema` do app não o lê), e onde o app guarda o perfil em memória (B.3).
- Que `git fetch` falhou nesta sessão por rede; usei a `origin/main` local, que já tinha o #1082.
- Nada foi visto em aparelho (iOS, VoiceOver, e-mail real, Apple real).

## F. Perguntas ao dono (com a recomendação)

1. **Como confirmar a exclusão de quem não tem senha (Apple e Google)?** Recomendo código de 6 dígitos por e-mail (opção b) só para conta sem senha, no S1 (depende da pergunta 11), e reautenticar com a Apple no S3. Alternativa: só mandar definir senha (opção a), o que o revisor da loja pode recusar.
2. **A exclusão passa a poder ser cancelada?** Hoje não pode, e o site diz que pode. Recomendo **só corrigir o texto agora** (S1) e abrir uma issue para o cancelamento por link no e-mail "agendada", porque ele mexe na guarda de adoção de item Pluggy. Quem teve a sessão roubada ainda depende do suporte nesse meio-tempo.
3. **Exigir o código do app autenticador (TOTP) ou um código de backup para excluir (e exportar) quando há MFA?** Recomendo sim, aceitando os dois como o `/auth/mfa/disable`. Hoje a senha basta. É o mesmo ponto do #1070.
4. **O app mostra o e-mail mascarado ao exportar** ("enviamos para a••••@gmail.com")? Recomendo sim, o servidor acrescenta o campo na resposta.
5. **O que acontece com a assinatura Stripe ao excluir?** Cancelar na hora (sem reembolso proporcional), cancelar no fim do período, ou bloquear a exclusão até o cancelamento? Recomendo **cancelar na hora e avisar por e-mail**, e que o dono diga a política de reembolso.
6. **O servidor devolve `phone_formatado`** no `GET /security` para evitar a terceira cópia da máscara? Recomendo sim.
7. **Quando precisamos submeter à App Store?** Define se S3/C2 (Apple) entram antes de P2. Preciso também de Team ID, Key ID e chave `.p8` da Apple, que só o dono tem.
8. **O bot do WhatsApp passa a responder "conta em exclusão" durante a carência** (S4)? Recomendo sim; é pequeno.
9. **Exportar depois de agendar a exclusão**: manter bloqueado (hoje) ou liberar nos 7 dias? Recomendo liberar (LGPD), mas isso mexe no 403 de `:4362`/`:4472`.
10. **Código por e-mail também para conta que TEM senha?** Recomendo não: só quem não tem senha usa o código (A.4). Aceitar os dois para todos deixa a sessão roubada com acesso ao e-mail pular a senha.
11. **O domínio de envio (Resend) está registrado no Apple Developer** para o e-mail "Ocultar meu e-mail"? Sem isso o código por e-mail não chega a quem entrou com a Apple. Preciso que o dono confirme antes do S1.
12. **O servidor guarda o refresh token da Apple no login** (cifrado) para o job revogar de qualquer origem? Recomendo sim (S3).
