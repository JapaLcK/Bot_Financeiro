# Contratos bancários do app iOS — Fase 4

`GET /onboarding/open-finance` autentica a identidade da sessão, sem UID no
pedido e sem exigir assinatura. Devolve `{ok:true,completed:false,completed_at:null}`
ou `completed:true` com `completed_at` ISO8601 com fuso. Não recebe conclusão do
cliente e não devolve dados financeiros. O GET reconcilia prova já existente no
servidor, inclusive banco conectado pelo site.

O marco `auth_accounts.open_finance_onboarding_completed_at` nasce NULL, sem default
ou backfill do wizard. Uma conexão Pluggy própria basta quando tem sync da autorização
atual (`last_sync_at >= reconnected_at`, se houver reconexão), UI servidor `updated`
ou `partial`, e conta ou investimento espelhado. Saldo zero vale; wizard pulado,
observação saudável sem sync, mock e dados anteriores à reconexão não valem.
Seleção/prova/carimbo compartilham transação na ordem de locks accounts → conexões.
Remover o último banco preserva o marco; reset completo o limpa.

`DELETE /open-finance/{user_id}/connections/{connection_id}` usa o gate financeiro
existente. Posse/id são checados antes de ação remota e novamente sob os locks do
item alvo. Retorno `{ok:true,deleted:1}`. Inexistente ou alheio retorna 404 uniforme
`{detail:{code:"OF_CONNECTION_NOT_FOUND",message:"Não achamos esse banco nas suas conexões."}}`.
Lock ocupado devolve 503 seguro. Somente o alvo participa da limpeza remota e local;
manual mesclado e outro banco são preservados. DELETE sem `connections/{id}` mantém
a semântica de remover todos. Limpeza remota é best-effort; lápide e delete local
permanecem atômicos.

`POST /open-finance/{user_id}/pluggy-item` veta um item com remoção deliberada e dono
no registry antes do GET remoto e novamente dentro de `pluggy_item_lock`. Retorna
409 `{detail:{code:"OF_ITEM_REMOVED",message:"Esse banco foi desconectado. Inicie uma nova conexão."}}`.
Callback não revoga lápide. Após remoção, novo consentimento deve criar item novo.
Reconexão viva e handoff do webhook continuam válidos.

`POST /open-finance/{user_id}/connect-token` aceita o campo opcional `attempt_id`
UUID canônico em minúsculas, somente junto de `app_scheme` permitido (`pigbank`,
`pigbank-staging`, `pigbank-dev`). O servidor monta
`<scheme>://open-finance-volta/<attempt_id>` no campo oficial `oauthRedirectUri`;
não aceita uma URL de retorno escolhida pelo cliente. Sem `attempt_id`, pedidos
legados válidos mantêm a URI antiga (ou nenhum redirect, no site). `item_id` e
ownership da reconexão continuam iguais. O nonce não vai para o registry.
Após os gates de sessão e acesso, UUID/scheme inválidos, campos JSON repetidos
(mesmo valor ou nome escapado), JSON malformado, mais de 4096 bytes ou corpo lento
retornam 400 antes de qualquer chamada à Pluggy. Corpo vazio continua permitido.

Validação reproduzível: testes dirigidos em `tests/test_fase4_open_finance.py` mais
os testes existentes de onboarding, disconnect, reset, ownership, marca de remoção e
handoff; origem OAuth em `tests/test_of_connect_token_attempt.py` e contratos
legados em `tests/test_of_connect_token_volta_app.py`. Use o ambiente isolado descrito
em `docs/ambiente.md` e na skill
`baseline-testes`; isso prova contratos locais, não consentimento real no aparelho.
