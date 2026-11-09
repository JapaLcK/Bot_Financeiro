-- =====================================================================
-- #657 (PR-9a, P8): troca o wamid cru e o assunto com texto do usuário nas
-- linhas antigas de `system_event_logs`
-- =====================================================================
-- Contexto: o wamid (`wamid.` + base64) decodifica para o telefone inteiro, e o
-- assunto do e-mail levava o código de verificação (#1378) e o texto do usuário
-- (título do agente, assunto e nome do formulário de contato). O código do PR-9a
-- grava o digest `mask_wamid` (`utils_phone.py`) e um rótulo fixo no assunto.
-- Este script deixa as linhas antigas iguais às novas, sem esperar a retenção de
-- 90 dias. Só mudam três campos, e nenhuma outra coluna nem outra chave:
--   A. `details.message_id` e `details.message_ids` dos 5 eventos do `wa_client`;
--   B. o trecho `message_id=<id>` da `message` do WARNING de typing (`error=` fica);
--   C. `details.subject` de `email_sent` e `email_failed`.
-- As linhas-alvo têm `user_id` nulo, e o script não filtra por ele: linha com
-- dono e wamid cru seria PII do mesmo jeito. A coluna `user_id` nunca é escrita.
--
-- >>> ORDEM DE DEPLOY (NÃO INVERTER) <<<
--   1. Deploy do PR-9a em TODO serviço que roda o monólito (web e réplicas): é ali
--      que rodam `wa_client`, `wa_runtime`, `wa_demo`, o e-mail de verificação, o
--      contato e os e-mails de agente. Um processo antigo continua gravando wamid
--      e assunto crus depois deste UPDATE, e o script não volta para pegá-los.
--   2. Rodar este script, passos 1 a 5.
--   3. No dia seguinte, rodar o passo 6, numa sessão NOVA.
-- O passo 4 dá 0, salvo linha crua que um processo antigo commite depois do
-- UPDATE (em READ COMMITTED a contagem a enxerga). O 0 NÃO prova o deploy:
-- processo antigo que não gravou nesse instante não aparece. O passo 6 olha uma
-- janela maior, e o 0 dele também não prova o deploy.
--
-- >>> COMO RODAR <<<
--   Um comando por vez, numa sessão que mantenha a transação. O script nunca faz
--   commit sozinho: o COMMIT e o ROLLBACK do passo 5 estão comentados, e quem
--   roda escolhe um deles depois de ler o passo 4.
--   DBeaver (NÃO TESTADO nele): auto-commit desligado (modo manual), um comando
--   por vez. O COMMIT é o botão de commit: colar o comentário do passo 5 não faz
--   nada. Nunca "executar script" inteiro.
--   psql: interativo, colando passo a passo. No passo 5, digite o comando sem o
--   comentário. Não use `psql -f`: ele não salva nada, porque termina sem COMMIT,
--   e as contagens dão 0 mesmo assim.
--   Nos dois: feche a sessão depois do COMMIT, e rode o passo 6 em outra.
--   Não use cliente de painel que não mantém a sessão entre comandos.
--
-- Codificação: os literais de C e do resíduo têm emoji e travessão (U+2014). Se o
-- cliente colar com `client_encoding` errado, o UPDATE toca 0 linhas e as
-- contagens também dão 0, porque usam os mesmos literais: um sucesso falso. O
-- canário do passo 1 (`literais_ok`) TEM de dar `true`. Se der `false`, pare.
--
-- Idempotente: o WHERE de cada UPDATE só pega o que ainda não está na forma nova
-- (string que não é digest de 12 hex; assunto que ainda tem o prefixo antigo), e
-- o SET usa a MESMA regra. Rodar de novo toca 0 linhas. Todo literal com barra é
-- E'...': o resultado não depende de `standard_conforming_strings`. Provado por
-- `tests/test_limpa_wamid_assunto_657.py`, que lê este arquivo. Teórico: um id cru
-- de 12 hex MAIÚSCULOS não casa a guarda e vira digest a cada rodada.
-- Precisa de PG 12+ (jsonpath). Sintaxe provada no 16.15; a versão de produção
-- não foi medida.
--
-- Tempo: só ordem de grandeza. Medido em 2026-11-09 em tabela TEMPORÁRIA, sem FK e
-- sem WAL, com os 3 índices reais e 1 milhão de linhas (1,6 GB), 3 rodadas: A em
-- 200 mil linhas, 1,6 a 4,8 s; B em 100 mil, 8 a 29 s (o mais caro: 4 regex por
-- linha); C em 100 mil, 2,7 a 4 s; cada contagem, 3 a 9 s. A tabela real tem WAL e FK:
-- remedir. O passo 1 dá o tamanho, e as colunas de `antes` dão quantas linhas
-- cada UPDATE vai tocar.
-- Teto de 5 min por comando: pela medição do Tester (2026-11-09,
-- `tempo.py`), o UPDATE B estoura entre 1 e 5,6 milhões de linhas tocadas, e sob
-- carga 100 mil linhas JÁ estouraram o teto (444 s): ele pode disparar bem abaixo
-- de 1 milhão. Se `b_wamid_message` passar de 1 milhão, não comece: relate o
-- número na issue. Se o `statement_timeout` disparar em qualquer UPDATE, a
-- transação aborta: ROLLBACK, e rode os passos 1 a 5 de novo em horário de pouco
-- tráfego (os UPDATEs são idempotentes). Se estourar de novo, ROLLBACK e relate,
-- SEM subir o teto: a saída é dividir o B em lotes, decidida à parte.
-- Nenhuma coluna indexada muda, mas cada linha tocada vira uma versão nova, com
-- entrada nos índices e WAL do mesmo tamanho: confira o espaço do volume antes.
--
-- O que pode dar ROLLBACK sem defeito no UPDATE (não autoriza COMMIT; o ROLLBACK
-- não perde nada, porque os UPDATEs são idempotentes): uma `*_like` do passo 4
-- acima de 0 por forma que o UPDATE pula de propósito e nenhum código atual grava
-- (`🔐 <não dígitos> — seu código…`, `WA <outro texto> typing…`, id com espaço antes
-- de ` error=`), ou linha nova de processo antigo entre o passo 1 e o UPDATE.
-- Relate na issue; quem decide é quem lê o relato, fora desta sessão.
--
-- Lock: RowExclusiveLock (não bloqueia leitura nem INSERT de outras linhas). Os
-- locks de linha ficam até o COMMIT. A poda (`apagar_em_lotes`) pula as linhas
-- travadas; o "apagar" do painel admin ESPERA o fim da transação. Não use o
-- painel com a transação aberta, nem a deixe parada entre os passos 4 e 5. Sem
-- gatilhos de usuário; os 2 `RI_ConstraintTrigger` da FK não disparam, porque
-- `user_id` não muda.
--
-- O que NÃO é tocado (só contado no resíduo do passo 4):
--   R1: wamid em texto livre: `details.response` (corpo da Graph), o `error=` do
--       WARNING de typing, `whatsapp_worker_error` e qualquer outro texto. O
--       número vai para a #1297.
--   R2: assunto de `email_*` que começa por `🔐`, `[Contato` ou emoji de agente
--       e que o UPDATE não reconhece (ex.: `🎤 {título} — PigBank`, de 2026-08).
--       Conta também assunto de catálogo com o mesmo emoji (ex.: o `📈 ...` de
--       `scripts/send_update_email.py`): confira as linhas antes de decidir.
-- Fora e não contados: o `to` dos e-mails, o telefone de `whatsapp_send_*` e de
-- `whatsapp_status_error` (issue própria, P10); assunto dinâmico de catálogo;
-- assunto sem prefixo reconhecível (`scripts/smoke_test_emails.py`).
-- =====================================================================

-- 1. Antes. `literais_ok` TEM de dar true. As 3 colunas de `antes` são quantas
--    linhas cada UPDATE vai tocar: anote-as.
SELECT version();
SELECT convert_to('🔐—çãó🤠🎤📬🔍🎯🎩📈', 'UTF8') = decode('f09f9490e28094c3a7c3a3c3b3f09fa4a0f09f8ea4f09f93acf09f948df09f8eaff09f8ea9f09f9388', 'hex') AS literais_ok;
SELECT
  count(*) FILTER (WHERE s.source = 'wa_client' AND jsonb_typeof(s.details) = 'object'
   AND s.event_type IN ('whatsapp_send_success', 'whatsapp_typing_indicator_success', 'whatsapp_typing_indicator_failed', 'whatsapp_typing_indicator_exception', 'whatsapp_token_invalid')
   AND (s.details @? 'strict $.message_id ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))'
        OR s.details @? 'strict $.message_ids[*] ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))')) AS a_wamid_details,
  count(*) FILTER (WHERE s.event_type = 'logger.warning'
   AND s.message ~ E'^(WA (?:demo )?typing indicator failed message_id=)[^ ]+( error=)'
   AND substring(s.message from E'^WA (?:demo )?typing indicator failed message_id=([^ ]+) error=') !~ '^[0-9a-f]{12}$') AS b_wamid_message,
  count(*) FILTER (WHERE s.event_type IN ('email_sent', 'email_failed')
   AND s.details->>'subject' ~ E'^(?:🔐 [0-9]+ — seu código de verificação|\\[Contato site\\] |(🤠 Xerife|🎤 Repórter|📬 Carteiro|🔍 Detetive|🎯 Banqueiro|🎩 Barão|📈 Faria Limer) — )') AS c_assunto
FROM system_event_logs s;
SELECT pg_size_pretty(pg_total_relation_size('system_event_logs'));

-- 2. Transação com teto de espera e de execução
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';

-- 3. As três trocas. Anote o número de linhas de cada uma: tem de bater com a
--    coluna correspondente de `antes`.
--    A: cada string que ainda não é digest vira o digest de `mask_wamid`. No array,
--    a ordem fica, e elemento que não é string fica. `details` tem de ser objeto,
--    e o jsonpath é `strict` nas duas chaves: no modo `lax`, uma string solta em
--    `message_ids` passaria por array, e um array em `message_id` seria desembrulhado.
UPDATE system_event_logs s
   SET details = s.details
     || CASE WHEN s.details @? 'strict $.message_id ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))'
             THEN jsonb_build_object('message_id', left(encode(sha256(convert_to(s.details->>'message_id', 'UTF8')), 'hex'), 12))
             ELSE '{}'::jsonb END
     || CASE WHEN s.details @? 'strict $.message_ids[*] ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))'
             THEN jsonb_build_object('message_ids', (
                    SELECT jsonb_agg(CASE WHEN jsonb_typeof(e) = 'string' AND e #>> '{}' !~ '^[0-9a-f]{12}$'
                                          THEN to_jsonb(left(encode(sha256(convert_to(e #>> '{}', 'UTF8')), 'hex'), 12))
                                          ELSE e END ORDER BY o)
                      FROM jsonb_array_elements(s.details->'message_ids') WITH ORDINALITY t(e, o)))
             ELSE '{}'::jsonb END
 WHERE s.source = 'wa_client' AND jsonb_typeof(s.details) = 'object'
   AND s.event_type IN ('whatsapp_send_success', 'whatsapp_typing_indicator_success', 'whatsapp_typing_indicator_failed', 'whatsapp_typing_indicator_exception', 'whatsapp_token_invalid')
   AND (s.details @? 'strict $.message_id ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))'
        OR s.details @? 'strict $.message_ids[*] ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))');

--    B: só o id depois de `message_id=` vira o digest.
UPDATE system_event_logs s
   SET message = regexp_replace(s.message, E'^(WA (?:demo )?typing indicator failed message_id=)[^ ]+( error=)',
         E'\\1' || left(encode(sha256(convert_to(substring(s.message from E'^WA (?:demo )?typing indicator failed message_id=([^ ]+) error='), 'UTF8')), 'hex'), 12) || E'\\2')
 WHERE s.event_type = 'logger.warning'
   AND s.message ~ E'^(WA (?:demo )?typing indicator failed message_id=)[^ ]+( error=)'
   AND substring(s.message from E'^WA (?:demo )?typing indicator failed message_id=([^ ]+) error=') !~ '^[0-9a-f]{12}$';

--    C: o assunto vira o que o código novo grava: o assunto fixo da verificação,
--    `Contato site`, ou o rótulo do agente (`_AGENT_EMAIL_LABEL`).
UPDATE system_event_logs s
   SET details = jsonb_set(s.details, '{subject}', to_jsonb(CASE
         WHEN s.details->>'subject' LIKE '🔐 %' THEN 'Seu código de verificação PigBank'
         WHEN s.details->>'subject' LIKE '[Contato site] %' THEN 'Contato site'
         ELSE (regexp_match(s.details->>'subject', E'^(?:🔐 [0-9]+ — seu código de verificação|\\[Contato site\\] |(🤠 Xerife|🎤 Repórter|📬 Carteiro|🔍 Detetive|🎯 Banqueiro|🎩 Barão|📈 Faria Limer) — )'))[1] END))
 WHERE s.event_type IN ('email_sent', 'email_failed')
   AND s.details->>'subject' ~ E'^(?:🔐 [0-9]+ — seu código de verificação|\\[Contato site\\] |(🤠 Xerife|🎤 Repórter|📬 Carteiro|🔍 Detetive|🎯 Banqueiro|🎩 Barão|📈 Faria Limer) — )';

-- 4. Depois. As 6 colunas TÊM de dar 0. As 3 primeiras são as de `antes`; as 3
--    últimas (`*_like`) não dependem da regex nem do jsonpath. O 0 confirma a
--    troca, não o deploy (ver o cabeçalho).
SELECT
  count(*) FILTER (WHERE s.source = 'wa_client' AND jsonb_typeof(s.details) = 'object'
   AND s.event_type IN ('whatsapp_send_success', 'whatsapp_typing_indicator_success', 'whatsapp_typing_indicator_failed', 'whatsapp_typing_indicator_exception', 'whatsapp_token_invalid')
   AND (s.details @? 'strict $.message_id ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))'
        OR s.details @? 'strict $.message_ids[*] ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))')) AS a_wamid_details,
  count(*) FILTER (WHERE s.event_type = 'logger.warning'
   AND s.message ~ E'^(WA (?:demo )?typing indicator failed message_id=)[^ ]+( error=)'
   AND substring(s.message from E'^WA (?:demo )?typing indicator failed message_id=([^ ]+) error=') !~ '^[0-9a-f]{12}$') AS b_wamid_message,
  count(*) FILTER (WHERE s.event_type IN ('email_sent', 'email_failed')
   AND s.details->>'subject' ~ E'^(?:🔐 [0-9]+ — seu código de verificação|\\[Contato site\\] |(🤠 Xerife|🎤 Repórter|📬 Carteiro|🔍 Detetive|🎯 Banqueiro|🎩 Barão|📈 Faria Limer) — )') AS c_assunto,
  count(*) FILTER (WHERE s.source = 'wa_client' AND jsonb_typeof(s.details) = 'object'
   AND s.event_type IN ('whatsapp_send_success', 'whatsapp_typing_indicator_success', 'whatsapp_typing_indicator_failed', 'whatsapp_typing_indicator_exception', 'whatsapp_token_invalid')
   AND ((jsonb_typeof(s.details->'message_id') = 'string' AND s.details->>'message_id' LIKE 'wamid.%')
        OR CASE WHEN jsonb_typeof(s.details->'message_ids') = 'array' THEN EXISTS (SELECT 1 FROM jsonb_array_elements(s.details->'message_ids') e
                WHERE jsonb_typeof(e) = 'string' AND e #>> '{}' LIKE 'wamid.%') END)) AS a_like,
  count(*) FILTER (WHERE s.event_type = 'logger.warning'
   AND s.message LIKE 'WA %typing indicator failed message_id=wamid.% error=%') AS b_like,
  count(*) FILTER (WHERE s.event_type IN ('email_sent', 'email_failed')
   AND (s.details->>'subject' LIKE '🔐 _% — seu código de verificação%' OR s.details->>'subject' LIKE '[Contato site] %'
        OR s.details->>'subject' LIKE ANY (ARRAY['🤠 Xerife — %', '🎤 Repórter — %', '📬 Carteiro — %', '🔍 Detetive — %', '🎯 Banqueiro — %', '🎩 Barão — %', '📈 Faria Limer — %']))) AS c_like
FROM system_event_logs s;

--    Resíduo: o que o UPDATE não toca, de propósito (ver o cabeçalho). Só anote:
--    R1 vai para a #1297; R2 > 0 se decide à parte.
SELECT
  count(*) FILTER (WHERE s.message LIKE '%wamid.%' OR s.details::text LIKE '%wamid.%') AS r1_wamid_em_texto,
  count(*) FILTER (WHERE s.event_type IN ('email_sent', 'email_failed')
   AND s.details->>'subject' LIKE ANY (ARRAY['🔐%', '[Contato%', '🤠%', '🎤%', '📬%', '🔍%', '🎯%', '🎩%', '📈%'])
   AND s.details->>'subject' NOT IN ('🤠 Xerife', '🎤 Repórter', '📬 Carteiro', '🔍 Detetive', '🎯 Banqueiro', '🎩 Barão', '📈 Faria Limer')
   AND s.details->>'subject' !~ E'^(?:🔐 [0-9]+ — seu código de verificação|\\[Contato site\\] |(🤠 Xerife|🎤 Repórter|📬 Carteiro|🔍 Detetive|🎯 Banqueiro|🎩 Barão|📈 Faria Limer) — )') AS r2_assunto_desconhecido
FROM system_event_logs s;

-- 5. Decida logo (ver Lock). As 6 colunas do passo 4 em 0, e cada UPDATE com o
--    número da coluna dele em `antes`: COMMIT. Qualquer outro número, em qualquer
--    das 6 colunas ou em qualquer UPDATE: ROLLBACK e relato na issue (ver "O que
--    pode dar ROLLBACK sem defeito" no cabeçalho). Depois, feche a sessão.
-- COMMIT;
-- ROLLBACK;

-- 6. No dia seguinte, numa sessão NOVA: a sessão do UPDATE dá 0 mesmo sem COMMIT,
--    porque enxerga a própria troca, e fechá-la sem COMMIT perde a troca. As 6
--    colunas devem dar 0. Se não derem, a causa pode ser:
--      - o COMMIT não foi feito;
--      - processo antigo: confira o deploy.
--    Depois, rode de novo os passos 1 a 5 (os UPDATEs são idempotentes). No fim,
--    feche esta sessão também: no modo manual, ela deixa a transação aberta.
SELECT
  count(*) FILTER (WHERE s.source = 'wa_client' AND jsonb_typeof(s.details) = 'object'
   AND s.event_type IN ('whatsapp_send_success', 'whatsapp_typing_indicator_success', 'whatsapp_typing_indicator_failed', 'whatsapp_typing_indicator_exception', 'whatsapp_token_invalid')
   AND (s.details @? 'strict $.message_id ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))'
        OR s.details @? 'strict $.message_ids[*] ? (@.type() == "string" && !(@ like_regex "^[0-9a-f]{12}$"))')) AS a_wamid_details,
  count(*) FILTER (WHERE s.event_type = 'logger.warning'
   AND s.message ~ E'^(WA (?:demo )?typing indicator failed message_id=)[^ ]+( error=)'
   AND substring(s.message from E'^WA (?:demo )?typing indicator failed message_id=([^ ]+) error=') !~ '^[0-9a-f]{12}$') AS b_wamid_message,
  count(*) FILTER (WHERE s.event_type IN ('email_sent', 'email_failed')
   AND s.details->>'subject' ~ E'^(?:🔐 [0-9]+ — seu código de verificação|\\[Contato site\\] |(🤠 Xerife|🎤 Repórter|📬 Carteiro|🔍 Detetive|🎯 Banqueiro|🎩 Barão|📈 Faria Limer) — )') AS c_assunto,
  count(*) FILTER (WHERE s.source = 'wa_client' AND jsonb_typeof(s.details) = 'object'
   AND s.event_type IN ('whatsapp_send_success', 'whatsapp_typing_indicator_success', 'whatsapp_typing_indicator_failed', 'whatsapp_typing_indicator_exception', 'whatsapp_token_invalid')
   AND ((jsonb_typeof(s.details->'message_id') = 'string' AND s.details->>'message_id' LIKE 'wamid.%')
        OR CASE WHEN jsonb_typeof(s.details->'message_ids') = 'array' THEN EXISTS (SELECT 1 FROM jsonb_array_elements(s.details->'message_ids') e
                WHERE jsonb_typeof(e) = 'string' AND e #>> '{}' LIKE 'wamid.%') END)) AS a_like,
  count(*) FILTER (WHERE s.event_type = 'logger.warning'
   AND s.message LIKE 'WA %typing indicator failed message_id=wamid.% error=%') AS b_like,
  count(*) FILTER (WHERE s.event_type IN ('email_sent', 'email_failed')
   AND (s.details->>'subject' LIKE '🔐 _% — seu código de verificação%' OR s.details->>'subject' LIKE '[Contato site] %'
        OR s.details->>'subject' LIKE ANY (ARRAY['🤠 Xerife — %', '🎤 Repórter — %', '📬 Carteiro — %', '🔍 Detetive — %', '🎯 Banqueiro — %', '🎩 Barão — %', '📈 Faria Limer — %']))) AS c_like
FROM system_event_logs s;
