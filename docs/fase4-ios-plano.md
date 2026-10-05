# Fase 4 iOS — plano aprovado de execução

Base inspecionada em 2026-10-05: `09f63299` (PR #834, Onda 5 C2). Faixa Completo: Open Finance, acesso, sessão e schema. As decisões de produto abaixo já foram aprovadas pelo usuário; este documento concretiza sua execução e não abre uma nova entrevista.

## Objetivo

Entregar onboarding bancário e gestão de conexões nativos para iOS, mantendo o acesso decidido pelo servidor, e um mockup completo e interativo da Home conforme Dashboard V2.

## Escopo e fronteiras

- iOS primeiro. Cadastro nativo, autenticação social, MFA e biometria continuam.
- Conta Free padrão sem app; usuários assinantes, trial e vitalício obedecem às permissões existentes. Nunca conceder trial ou plano pelo cadastro nativo.
- Contratação Apple será a entrega seguinte. Nesta entrega não há checkout, compra simulada com efeito real, nem link de contratação implementado por suposição de regras da loja. A tela sem direito explica o estado, oferece conferir novamente, suporte e sair.
- Antes do primeiro Início: conexão obrigatória e primeira sincronização válida. Conexão sincronizada no site vale; parcial válida vale, com detalhe explícito. Depois da conclusão persistida, expiração, falha e remoção do último banco não revogam esse marco. O gate de plano continua independente.
- Home produtiva permanece provisória. A Home V2 é mockup, dados fictícios identificados, cinco abas (Resumo, Gastos, Piggy, Metas, Extrato), perfis, Organizar e Configurações pelo avatar. Não implementar endpoints V2 nesta entrega.
- Acesso de teste não deve desaparecer silenciosamente: recuperar o spike build 19 para servir de referência/diagnóstico e manter acesso explícito até validação do substituto no aparelho. Não preservar botão que burle plano/onboarding nem implantar um build de teste como Home real.

## Inventário e pontos de reutilização

1. `app/src/api/client.ts` já concentra Bearer, renovação, timeout e `RequisicaoSuperada`. Todas as chamadas usam esse cliente.
2. `app/src/services/auth.ts`, `features/auth/sessao.tsx` e `storage/secure.ts` resolvem identidade/geração da sessão; não criar cache independente de credenciais.
3. `app/src/features/openFinance/volta.ts` já oferece polling sequencial de 3 s/5 min, limite de POST e tipos de erro. Estender o controlador, sem duplicar um segundo laço nas telas. Sua flag de widget existe, porém o app não tem chamador nem dependência Pluggy na base.
4. `app/+native-intent.ts` já intercepta volta enquanto widget aberto; hoje descarta o item. Trocar descarte por captura persistente da intenção, preservando o widget em foco.
5. `/auth/me` já fornece `app_access`, `of_banks_max`, `cobranca_em_atraso`, `precisa_criar_senha`, `plan_tier`. Schema nativo perde os últimos campos; o Início ignora acesso.
6. `GET /open-finance/{uid}` e POST `pluggy-item` devolvem snapshot completo (id da conexão, item, last_sync_at, reconnected_at, status_reason, health, ui). Schema nativo só mantém item/nome/ui.
7. `GET /open-finance/{uid}/limite` já devolve `{ok,of_banks_max,em_uso,pode_adicionar,code,message}`; não criar cálculo de limite no app. `POST connect-token` já aceita `item_id` para reconexão própria e `app_scheme` por ambiente.
8. `connection_ui_state` em `core/services/pluggy_health.py` é a fonte de estado exibível. `UPDATING` não prova consentimento nem sync. Não derivar rótulos de `status` ou copiar regras de health para TS.
9. `db/open_finance_state.py` possui locks de item, versão/CAS, registry e `mark_items_removed`. `frontend/routes/open_finance.py` possui `_disconnect_sob_lock`, remoção remota best-effort, registro auditado e revalidação sob lock. Estender esses seams.
10. `disconnect_open_finance_connection(user_id, connection_id=None)` já filtra individualmente e limpa imports, cartões e caixinhas; preserves manual auto-merged. DELETE HTTP atual significa TODOS. Sua semântica permanece para site/reset.
11. `/onboarding/state` é wizard web, aceita skip e completed do cliente; `onboarding_completed_at` tem backfill histórico. Não serve de prova bancária e não deve ser reaproveitado como tal.
12. ADR 0005 documenta spike `react-native-pluggy-connect`, OAuth navegador (`forceOauthInBrowser=true`) comprovado Nubank iPhone; modo OAuth dentro de WebView não atende o banco com app. Recuperar código do spike antes de escrever widget novo. ADR status proposto pode ser atualizado à decisão deste escopo, preservando provas e limites.

## Contratos Backend ↔ Mobile

### 1. Marco bancário persistente

Adicionar `auth_accounts.open_finance_onboarding_completed_at timestamptz NULL`, SEM default e SEM backfill irrestrito. Esse marco é distinto do wizard web. Não recebe `completed` do cliente.

Criar `db/open_finance_onboarding.py`, responsabilidade única de ler/promover o marco com prova, e `GET /onboarding/open-finance` em `frontend/routes/onboarding.py` (router já registrado). Autenticação por `resolve_dashboard_user_id`, identidade da sessão, sem UID fornecido pelo cliente. Resposta exata:

```json
{"ok":true,"completed":false,"completed_at":null}
```

Quando completo: `completed:true`, `completed_at` ISO8601 com fuso. Esse GET pode promover idempotentemente um marco ausente a partir de prova existente no servidor; documentar que se trata de reconciliação derivada, nunca de concessão baseada em alegação do app. Permitir leitura sem assinatura é necessário para mostrar estado sem plano; não devolver dados financeiros neste endpoint.

Prova para promover: existe conexão do MESMO usuário, não terminal/pausada/removida, `last_sync_at IS NOT NULL` e (`reconnected_at IS NULL OR last_sync_at >= reconnected_at`), ao menos uma conta OU investimento espelhado pertencente à conexão e `connection_ui_state` em `updated` ou `partial`. Parcial sem carimbo ou espelho não vale; saldo zero verdadeiro vale (não testar balance > 0). Item saudável observado `ok=None` não vale. Dados de autorização anterior não valem. Uma conexão suficiente, não todas.

Reutilizar seleção de conexões do snapshot (extrair sua leitura/mapeamento se necessário), sem SELECT gigante financeiro duplicado. A comprovação e o carimbo ocorrem na mesma transação, sob `_lock_user` seguido de linhas próprias das conexões, na ordem já usada por remoção/sync. Isso fecha delete/reconexão entre prova e carimbo. Carimbar apenas se NULL e retornar o valor persistido. Não tocar no complexo writer `mark_sync_result` para esta fase: ele continua responsável só por sync/health. Se o usuário nunca abriu o app, o primeiro GET reconhece a prova do site; reinstalação lê marco existente. Remoção de conexão preserva marco. Reset explícito de todos os dados em `db/privacy.py` zera esse novo marco junto ao wizard web; excluir conta já faz cascade.

Opcionalmente expor mesmo objeto como campo aditivo `open_finance_onboarding` em `/auth/me` ou snapshot se isso eliminar chamada real demonstrada; não é necessário ao contrato mínimo e o Mobile não depende disso. O endpoint acima é a fonte obrigatória desta entrega.

### 2. Schemas nativos

`perfilSchema`: manter campos atuais e adicionar `of_banks_max: integer nullable optional`, `cobranca_em_atraso: boolean optional`, `precisa_criar_senha: boolean optional`. `app_access` existente precisa ser explicitamente `true` para liberar produto; campo ausente vira erro de contrato/atualização necessária com retry, não concessão nem alegação de plano Free. Não classificar assinaturas pelo nome do plano.

`conexaoSchema`: adicionar `id: integer positive`, `last_sync_at: ISO datetime nullable`, `reconnected_at: ISO datetime nullable`, `status: string`, `status_reason: string nullable`; preservar provider_item_id/nome/ui. `health` não é requerido pelo app para decidir UI. Novos fixtures de serviço precisam representar a resposta real; campos não podem ser inventados com defaults que abram o gate.

Schema separado do onboarding `{ok:true,completed:boolean,completed_at:datetime nullable}`, coerência completed ↔ completed_at. Schema do limite exatamente conforme inventário, incluindo teto NULL = ilimitado. Calls timeoutadas/abortáveis via cliente existente.

### 3. Conectar e reconectar

`pedirConnectToken(uid, itemId?)`: corpo `{app_scheme, item_id?}`; item_id só da conexão atual da sessão, não valor arbitrário do deep link. Novo banco exige consultar `/limite` antes de abrir widget. Reconexão própria não exige `pode_adicionar=true`; ainda respeita OF0/pausada/sem app e autorização da rota. POST `pluggy-item` permanece `{item:{id}}`. Callback fornece pista, GET snapshot confirma.

### 4. Remoção individual

Novo `DELETE /open-finance/{user_id}/connections/{connection_id}`. Usa `authorize_dashboard_access`, filtra dono antes de ação remota; inexistente/alheio = 404 uniforme `{detail:{code:"OF_CONNECTION_NOT_FOUND",message:"Não achamos esse banco nas suas conexões."}}` sem dados do outro usuário. Retorno `{ok:true,deleted:1}`. Após resposta perdida, Mobile confere snapshot e reconhece ausência como conclusão antes de repetir. Lock ocupado retorna 503 com mensagem segura existente. DELETE todos continua contrato atual.

Estender `_disconnect_sob_lock(user_id, connection_id=None)` de forma que caminho individual enumere/lock/remova SOMENTE item alvo. Revalidar posse e id após adquirir lock; remover remotamente os items enumerados desse alvo, depois chamar DB com mesmo connection_id. Segundo passe filtrado também só alvo. Não chamar `list_pluggy_item_ids(user_id)` no ramo individual para apagar todos remotamente. Registro auditado apenas se deleted; remoção local e lápide registry continuam atômicas. Falha da remoção remota é best-effort, não faz UI afirmar que o banco ficou ligado.

### 5. Antirressurreição

Defesa de servidor indispensável, inclusive clientes antigos. Antes de salvar item por `POST pluggy-item`, se registry contém remoção deliberada COM dono para esse mesmo item, rejeitar 409 `{detail:{code:"OF_ITEM_REMOVED",message:"Esse banco foi desconectado. Inicie uma nova conexão."}}`. Revalidar SOB `pluggy_item_lock` em `_salva_item_sob_lock`, pois o delete pode ocorrer durante o GET remoto. Usar `item_registry_origins` e critérios de dono/removed atuais; nunca tirar lápide por callback. Não rejeitar handoff legítimo do webhook, nem upsert/reconexão atual viva. Novo consentimento para um banco removido precisa criar item novo, não adotar id antigo.

## Máquina e navegação Mobile

Decisor central do fluxo bancário, sem duplicação em Entrar: após liberar biometria, carregar perfil e marco. Prioridade: credencial faltando → instrução existente; `app_access=false` → acesso indisponível; app_access válido + marco completo → Início; marco incompleto → snapshot + limite → primeiro onboarding. Segurança e Sair permanecem alcançáveis no gate. Deep link para Início não pula decisor. Não abrir produto antes de prova nem exibir zero financeiro como placeholder.

Estados do onboarding: `verificando`, `sem-acesso`, `cobranca-pendente`, `conectar`, `autorizando`, `organizando`, `recuperar`, `concluido`, `erro`. Carência sem marco + `of_banks_max=0` mostra aviso cobrança, não CTA impossível. OF0 sem cobrança mostra falta de permissão de conexão. Após marco completo, Home provisória mantém acesso de Configurações e bancos; problemas de bancos têm caminho direto de recuperação. Perfil e direito são reavaliados no retorno ao foreground; não bloquear inteiro por falha transitória de leitura depois de já renderizar dados não sensíveis.

Telas: `conectar-banco.tsx` (explica produtos lidos/privacidade/consentimento, CTA e sair), `autorizando.tsx` (wrapper widget oficial), `open-finance-volta.tsx` (evoluir para organizar, não nova lógica duplicada), `conexoes.tsx` (lista, última sincronização, UI servidor, adicionar, reconectar, desconectar), `configuracoes.tsx` (bancos, Segurança, suporte e sair). Início provisório oferece Configurações pelo avatar e ligação explícita Conexões; não vender Home provisória como mockup aprovado.

### Marcador e recuperação

Criar pequeno registro SecureStore separado de credenciais, com dono por identidade e guards de sessão/geração para operações em voo (uma renovação normal de token da mesma conta não apaga uma tentativa persistida): `{user_id,iniciada_em,modo:"nova"|"reconectar",item_id?:string,ids_antes:string[],tentativa_id:string}`. Não persistir connect token, raw bancário, saldo, e-mail ou segredo. Gravar marcador ANTES de emitir token/abrir widget; falha de persistência impede início (caso contrário retorno frio fica inseguro). Expiração de tentativa limita POST automático; timeout de UI não prova fracasso do banco.

Capturar item válido da intenção mesmo enquanto widget focado. Atualizar marcador de forma serializada e conferir geração antes de escrita. `onSuccess`, deep link e `onClose` convergem no mesmo controlador; nunca dois POST/polls concorrentes. Enquanto focado, o deep link não cobre widget; ao fechar, usar item capturado e consultar snapshot. `onError/onClose` não afirmam falha bancária sem snapshot.

Ao foreground/cold start/FaceID liberado: reler marcador da MESMA conta, conferir snapshot primeiro. Com item próprio vivo, observar UI; com item novo ainda não registrado e tentativa ativa correspondente, admitir POST limitado, guardas servidor. Sem tentativa válida, deep link só consulta snapshot, nunca registra arbitrariamente. Item já visto sumindo significa remoção: não POST de novo. Conexão terminal não vira candidata a readoção. Remover marcador só por conclusão observada ou cancelamento explícito da tentativa e limpar na saída/troca de identidade; resposta de operação velha nunca escreve marcador da nova conta.

Sem item conhecido: polling de snapshot por janela existente, comparando ids_antes para reconhecer conexão nova ou alvo de reconexão. Se mais de uma candidata, mostrar lista com estados em vez de escolher por posição. Se webhook/onSuccess/deep link TODOS se perderem e não existir item no snapshot, não há como descobrir um id remoto pelo app: timeout oferece conferir novamente, retomar widget quando disponível ou iniciar nova tentativa com aviso; jamais inventar sucesso. Não criar varredura remota Pluggy nem credenciais no aparelho para contornar essa limitação. Backend adota por webhook existente.

Pausa em background/travado: cancelar UI e laço, não limpar marcador nem afirmar cancelamento bancário. Na liberação, nova rodada GET primeiro. Usar AbortController e guard de rodada/geração para ignorar resultado antigo; esperar timer sem tocar UI desmontada. GET transiente mantém espera/recuperação; 401 delega sessão; 402 revalida acesso; 403 credencial informa; 404 reconexão reconsulta conexões; 409 removed limpa tentativa e mostra recuperação. Após 5 min mostrar ação, sem spinner eterno. Sair desta tela volta para gate/conexões, não contorna primeiro onboarding. VoiceOver anuncia transições úteis sem narrar cada tick.

## Divisão de ownership

**BackendCoder**: `db/schema.py`, novo `db/open_finance_onboarding.py`, exports `db/__init__.py` se necessário, `db/privacy.py`, `db/open_finance.py` se extrair seleção compartilhada, `frontend/routes/onboarding.py`, `frontend/routes/open_finance.py`, testes Python correspondentes e contratos backend em docs. Não editar app nem mockup. Prioridade: contratos/migração → antirressurreição → DELETE individual → testes dirigidos. Informar Mobile quando contratos estabilizados.

**MobileCoder**: `app/` exclusivamente (schemas, serviços, marcador, decisor, rotas, widget/dependências e testes Jest), ADR 0005 apenas seção nativa/provas após coordenar. Invocar pigbank-frontend ANTES de código UI, reutilizar componentes/tokens/tema de acesso onde adequado; não recolorir globalmente Segurança/Início. Recuperar spike comprovado, confirmar versão oficial compatível e usar expo install para WebView. Criar controlador coeso de fluxo, evitar provedor genérico de toda operação do app. Atenção: app/node_modules é symlink temporário para a worktree de build anterior; remover só o symlink e executar npm ci próprio ANTES de instalar a lib, sem alterar dependências da build anterior. Nenhuma alteração em Python.

**Root/mockup**: artefato independente servido localmente, baseado na inspeção do `/painel`/fontes V2 atuais. Reutilizar assets e módulos existentes quando fizer sentido; não publicar dados reais nem credenciais. Completo significa todos os blocos V2 identificados na inspeção, cinco abas navegáveis, perfis e Organizar funcionando no toque (reordenar/ocultar/restaurar), avatar abrindo configurações, tela de contratação Apple demonstrativa e rotulada, estados de conexão/sem dados. Não copiar balances de conta real para fixture pública. Mockup separado do bundle produtivo.

## Ordem de implementação

1. Fixar contratos acima e fixtures mínimas reais; registrar decisão e baseline só da área (root coordena). Ler a skill baseline-testes existente em .claude/skills/baseline-testes/SKILL.md; seguir isolamento descrito nela e em conftest. Ambiente verificado pelo root: PostgreSQL 15 local em 127.0.0.1:5432, DATABASE_URL=postgresql://lucaskuramoti@127.0.0.1:5432/postgres, conftest cria DB descartável; Python da venv /Users/lucaskuramoti/Projetos/bot/bot_wa/.venv/bin/python.
2. Backend implanta prova persistida e antirressurreição, DELETE individual; Mobile pode trabalhar em schemas/telas com fixtures enquanto recebe contrato final.
3. Mobile recupera widget, adiciona marcador/decisor e conecta telas ao controlador de retorno existente; mockup segue independente.
4. Verificação unitária/integrada dirigida, lint/typecheck app, inspeção visual desktop/mobile do mockup e RN/simulador; depois Tester e Manager conforme time-dev, com achados Codex local independentes.
5. PR por verificabilidade: backend e app/mockup podem ser separados se build/aparelho bloquear prova. Deploy backend compatível ANTES de distribuir binário que exige endpoint novo. Sem push direto main; Codex e CI atuais verdes para merge.
6. TestFlight iOS com roteiro real no aparelho. Só retirar acesso diagnóstico e declarar Fase 4 concluída após validar conexão e retomada real. Assinaturas Apple iniciam em entrega posterior.

## Critérios de pronto e ataque obrigatório

Backend: conta A não lê/deleta/reconecta banco B; sem assinatura não acessa rotas financeiras; marco não nasce com wizard skip, observação saudável, mockup, UPDATING ou sync antigo à reconexão; sync válido completo/parcial com dados promove, espelho saldo zero também; o marco persiste após remover último banco/reinstalar e zera em reset completo. Corrida promoção×delete/reconnect sem resultado falso. DELETE individual mantém dados/items de outro banco e manual merged; auth remota falhando deixa lápide/limpeza corretas; DELETE todos/reset seguem iguais; callback antigo/removido nunca ressuscita, callback novo e handoff webhook continuam válidos; guarda sob lock testada, não só pré-lock.

Mobile: e-mail/social/cadastro → perfil real/sem direito/carência; conta com banco do site pula coleta; conta sem marco não chega Início por voltar/deep link; marcador não vaza entre A→logout→B; callback antes do onSuccess; close sem callback mas com item capturado; sem item com webhook tardio; sem nenhum evento e timeout com saída útil; app encerrado/reaberto com FaceID; travar durante GET/POST; geração velha ignora respostas; pedido POST com resposta perdida reconciliado por GET; item removido em outro aparelho não reaparece; cap cheio permite reconexão própria mas barra banco novo; OF0 não abre widget; partial com aviso e ausência de dados sem falso sucesso; remover uma conexão confirma efeito e não manda DELETE todos; duplo toque não dispara operações duplicadas.

Mockup: testar cinco abas/perfis/organizar/reset/avatar/estado fictício; mobile safe area e toque, desktop inspeção do artefato, tema e fonte da marca. Estados não implementados não podem fingir ação financeira real.

Testes novos discriminam comportamento, não texto de arquivo. Para correções restritivas, grupo com controle negativo (desligar fix falha) e positivo (fluxo legítimo funciona). Testes dirigidos apenas; CI executa suíte total. Proveniência da validação separada: simulação Jest/server fixture, simulador iOS e aparelho/banco real não são equivalentes.

## Limites e decisões técnicas materiais

- Nada nesta entrega recupera id remoto desconhecido se todos os canais forem perdidos; oferecer recuperação explícita, não promessa de reconciliação garantida.
- Dependência WebView/lib do spike exige binário novo, OTA sozinha não serve.
- O marco introduz schema aditivo e reconciliação idempotente, sem alterar entitlement nem consentimento. Código web continua funcionando com campos extras/endpoint novo.
- TestFlight recebido não é prova do Nubank real. Guardas/remoção necessárias devem estar aprovadas e deployadas antes do teste real; nunca executar disconnect remoto na conta do usuário por conta própria.
