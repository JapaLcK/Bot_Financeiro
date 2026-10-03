# Job dedicado de exclusão de conta no Railway

Este job conclui exclusões de conta cujo período de carência de 7 dias já venceu.
Ele foi feito para Railway Cron: roda uma vez, processa as contas vencidas, envia o e-mail final e encerra o processo.

## Serviço no Railway

Crie um serviço separado no mesmo projeto Railway, apontando para este mesmo repositório.

A configuração é toda pelo painel do Railway, em Settings do serviço:

- Nome do serviço: `account-deletion-job`
- Custom Start Command: `python scripts/account_deletion_job.py`
- Cron Schedule: `0 6 * * *`
- Restart Policy: não se aplica — o painel avisa que "Services with a cron schedule do not have a restart policy"
- Config-as-code → Railway Config File: deixar **vazio**

O cron do Railway usa UTC; `0 6 * * *` roda uma vez por dia às 06:00 UTC, aproximadamente 03:00 no horário de Brasília.

## Por que não se usa Config as Code

O Railway descontinuou o Config as Code: arquivos de configuração existentes **param de funcionar em 2026-12-01**, e serviço que nunca usou não pode mais optar por ele desde 2026-08-28. Este serviço era configurado por um `railway.account-deletion.toml` na raiz; ele foi migrado para o painel e o arquivo foi apagado, para que ninguém reconfigure pelo caminho que morre em dezembro.

Migração conferida no painel em 2026-09-09: os avisos "The value is set in /railway.account-deletion.toml" sumiram do Custom Start Command e do Cron Schedule, o campo Railway Config File está vazio, a execução das 06:03 UTC de 2026-09-09 saiu verde em 2s e a aba Cron Runs seguia agendando a próxima para 06:00 UTC.

## Variáveis necessárias

Copie para este serviço as mesmas variáveis de produção usadas pelo app principal:

- `DATABASE_URL`
- `RESEND_API_KEY`
- `EMAIL_FROM`
- `SUPPORT_EMAIL` opcional, default `contato@pigbankai.com`
- `ACCOUNT_DELETION_JOB_LIMIT` opcional, default `50`
- `PLUGGY_CLIENT_ID`
- `PLUGGY_CLIENT_SECRET`
- `PLUGGY_BASE_URL` opcional, default `https://api.pluggy.ai`
- `PLUGGY_TIMEOUT` opcional, default `20` (segundos)

As duas variáveis `PLUGGY_*` obrigatórias não são decoração: desde a Onda 4 a
exclusão definitiva deleta os items do usuário na Pluggy antes do delete local
(`db/privacy.py::delete_user_data`, hook montado em
`process_due_account_deletions`). Sem elas, `create_pluggy_api_key`
(`core/services/pluggy.py`) levanta `PluggyConfigError`, o helper cai no ramo de
auth falhada, registra `pluggy_disconnect_auth_failed` em `system_event_logs` e
**não deleta item nenhum** — a conta some daqui e o banco dela continua vivo (e
pago) na Pluggy. O worker do monólito tem as variáveis; qual dos dois processa a
conta primeiro é ordem de chegada (`for update skip locked`), então configurar
só um dos dois deixa o desfecho sorteado.

`PLUGGY_TIMEOUT` é o teto por chamada HTTP e é o knob de controle do tempo de
parede do lote: o pior caso por conta é `1 auth + N deletes`, cada um até o
timeout, com o lock do item na mão. Se o lote ficar longo, baixe
`ACCOUNT_DELETION_JOB_LIMIT` e/ou `PLUGGY_TIMEOUT` neste serviço — não há teto
próprio no código.

O terceiro knob é `OF_SYNC_LOCK_WAIT_MS` (default `15000`): conta com um sync em
andamento espera esse teto inteiro antes de a exclusão seguir sem os locks, e
durante o resto da transação segura 1 conexão dedicada e 1 das 8 vagas de
`_lock_slots()` (`OF_SYNC_LOCK_MAX_CONN`, default 8). Medido em **23/09/2026**
nesta árvore: **15,17 s** para uma conta com o lock do item ocupado por outra
sessão — remeça antes de reusar (CLAUDE.md §2), com

```bash
# trocar o `monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "100")` do caso por "15000"
# antes de rodar — o monkeypatch ignora a variável do shell, e o 100 está lá para
# a suíte não custar 15 s.
.venv/bin/python -m pytest -q \
  tests/test_account_deletion_pluggy.py::test_t5_lock_ocupado_loga_e_segue --durations=3
```

## Onde investigar uma falha

O resumo que a rodada NORMAL grava em `system_event_logs`
(`event_type = account_deletion_job`) traz **contagens**, incluindo quantos erros
houve (`details.errors` é um número) — não a lista. O `user_id` e o texto do erro
saem só no **log do processo** (a saída `[account_deletion_job] concluído: …` do
Railway, retenção finita). O `--dry-run` grava no MESMO `event_type` um resumo
diferente e menor — `{"due_accounts": N, "dry_run": true}`, sem chave `errors`:
ele não apaga nada, logo não há erro a contar.

É de propósito: tudo depois do `conn.commit()` de `delete_user_data` roda com a
conta já apagada, então um erro dali carrega o `user_id` de uma conta que não
existe mais — e a linha de `system_event_logs` do resumo tem `user_id` NULL, logo
nenhuma cascata a apaga. Pelo mesmo motivo o e-mail final ("Conta excluída") é
logado sem o endereço: `event_type = email_sent` + assunto, e nada mais
(`send_email(..., log_recipient=False)`). Preso por
`tests/test_account_deletion_pii_logs.py`.

## Teste manual

Na raiz do projeto:

```bash
python scripts/account_deletion_job.py --dry-run
```

O `--dry-run` apenas conta contas vencidas, sem apagar dados e sem enviar e-mail.

Para processar de verdade:

```bash
python scripts/account_deletion_job.py --limit 10
```

O comando real não agenda novas exclusões. Ele apenas processa contas que já estão marcadas para exclusão e com `deletion_scheduled_for <= now()`.
