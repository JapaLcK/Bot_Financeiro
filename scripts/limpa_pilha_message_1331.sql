-- =====================================================================
-- #1331: tira a pilha da `message` das linhas antigas COM DONO de
-- `system_event_logs`
-- =====================================================================
-- Contexto: até a #1331, o `_DashboardHandler` anexava a pilha à `message`, e a
-- `message` das linhas com dono sai na exportação LGPD. O código novo não grava
-- mais a pilha ali. Este script limpa as linhas antigas sem esperar a retenção
-- de 90 dias. Só a coluna `message` muda; `details` não é tocado. A pilha continua
-- em `details.traceback`, quando houver: não vale para linha que não é log nem
-- para o início de pilha longa (o teto guarda o fim).
--
-- >>> ORDEM DE DEPLOY (NÃO INVERTER) <<<
--   1. Deploy de TODO processo que escreve log. Um processo ainda com o handler
--      antigo continua gravando pilha depois deste UPDATE.
--   2. Rodar este script, passos 1 a 5.
--   3. No dia seguinte, rodar o passo 6, numa sessão NOVA.
-- O passo 4 dá 0, salvo pilha que um processo antigo commite depois do UPDATE (em
-- READ COMMITTED a contagem a enxerga). O 0 NÃO prova o deploy: processo antigo
-- que não gravou nesse instante não aparece. O passo 6 olha uma janela maior, mas
-- só acusa processo antigo que tenha gravado pilha com dono no intervalo, e o 0
-- dele também não prova o deploy.
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
-- Idempotente: o WHERE e o corte usam a MESMA regex, então rodar de novo toca 0
-- linhas. Todos os literais com barra são E'...': o resultado não depende de
-- `standard_conforming_strings`. Provado por
-- `tests/test_limpa_pilha_message_1331.py`, que lê este arquivo.
-- Sintaxe provada no 16.15; a versão de produção não foi medida.
--
-- Tempo: só ordem de grandeza. Medido em 2026-11-05 em tabela TEMPORÁRIA, sem FK
-- e sem WAL, com 1 milhão de linhas (1,9 GB; 13.334 cortadas): medição de 4 a 6 s
-- e UPDATE de 6 a 7 s sem índice; medição de 7 s e UPDATE de 9 a 10 s com
-- índices. A tabela real tem índices e WAL: remedir. O passo 1 dá a contagem e
-- o tamanho.
--
-- Lock: RowExclusiveLock (não bloqueia leitura nem INSERT de outras linhas). Um
-- DELETE nas linhas tocadas espera o fim da transação, e isso alcança a retenção
-- e a exclusão de usuário pela FK. Portanto, não deixe a transação parada entre
-- os passos 4 e 5. Sem gatilhos de usuário; há os 2 `RI_ConstraintTrigger` da FK.
-- =====================================================================

-- 1. Antes
SELECT version();
SELECT count(*) FROM system_event_logs s, (SELECT E'\r?\n?[ +|]*(Exception Group )?Traceback \\(most recent call last\\):.*$' AS re) p WHERE s.user_id IS NOT NULL AND s.message ~ p.re;
SELECT pg_size_pretty(pg_total_relation_size('system_event_logs'));

-- 2. Transação com teto de espera e de execução
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';

-- 3. O corte
UPDATE system_event_logs s
   SET message = regexp_replace(s.message, p.re, '')
  FROM (SELECT E'\r?\n?[ +|]*(Exception Group )?Traceback \\(most recent call last\\):.*$' AS re) p
 WHERE s.user_id IS NOT NULL
   AND s.message ~ p.re;

-- 4. Depois. As duas primeiras contagens TÊM de dar 0: a 1ª usa a regex, a 2ª não
--    depende dela. O 0 confirma o corte, não o deploy (ver o cabeçalho).
--    A 3ª é o resíduo: linhas com dono que ainda têm a linha final de uma exceção
--    que não começa por `Traceback` (causa nunca levantada, `exc_info=` de exceção
--    não levantada). O UPDATE não as corta, de propósito. Anote o número para
--    decidir à parte. Ele é um PISO, não o total: classes que não terminam em
--    `Error`, `Exception` ou `Violation` não contam.
SELECT count(*) FROM system_event_logs s, (SELECT E'\r?\n?[ +|]*(Exception Group )?Traceback \\(most recent call last\\):.*$' AS re) p WHERE s.user_id IS NOT NULL AND s.message ~ p.re;
SELECT count(*) FROM system_event_logs WHERE user_id IS NOT NULL AND message LIKE '%Traceback (most recent call last):%';
SELECT count(*) FROM system_event_logs WHERE user_id IS NOT NULL AND message ~ E'(The above exception|During handling of the above|\n[A-Za-z0-9_.]+(Error|Exception|Violation): )';

-- 5. Decida logo (ver Lock). As duas contagens em 0: COMMIT. Qualquer outro
--    número: ROLLBACK, e confira antes de rodar de novo. Depois, feche a sessão.
-- COMMIT;
-- ROLLBACK;

-- 6. No dia seguinte, numa sessão NOVA: a sessão do UPDATE dá 0 mesmo sem COMMIT,
--    porque enxerga o próprio corte, e fechá-la sem COMMIT perde o corte. As duas
--    devem dar 0. Se não derem, a causa pode ser:
--      - o COMMIT não foi feito;
--      - texto interpolado que traga o cabeçalho, mesmo com deploy correto: confira
--        a linha antes de concluir que é processo antigo;
--      - processo antigo: confira o deploy.
--    Depois, rode de novo os passos 2 a 5 (o UPDATE é idempotente). No fim, feche
--    esta sessão também: no modo manual, ela deixa a transação aberta.
SELECT count(*) FROM system_event_logs s, (SELECT E'\r?\n?[ +|]*(Exception Group )?Traceback \\(most recent call last\\):.*$' AS re) p WHERE s.user_id IS NOT NULL AND s.message ~ p.re;
SELECT count(*) FROM system_event_logs WHERE user_id IS NOT NULL AND message LIKE '%Traceback (most recent call last):%';
