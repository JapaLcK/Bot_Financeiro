# Job dedicado de exclusão de conta no Railway

Este job conclui exclusões de conta cujo período de carência de 7 dias já venceu.
Ele foi feito para Railway Cron: roda uma vez, processa as contas vencidas, envia o e-mail final e encerra o processo.

## Serviço no Railway

Crie um serviço separado no mesmo projeto Railway, apontando para este mesmo repositório.

Configuração recomendada:

- Nome do serviço: `account-deletion-job`
- Config as Code file path: `/railway.account-deletion.toml`
- Start command: `python scripts/account_deletion_job.py`
- Cron schedule: `0 * * * *`
- Restart policy: `NEVER`

O arquivo `railway.account-deletion.toml` já define o start command, o cron horário e a política de restart.
O cron do Railway usa UTC.

## Variáveis necessárias

Copie para este serviço as mesmas variáveis de produção usadas pelo app principal:

- `DATABASE_URL`
- `RESEND_API_KEY`
- `EMAIL_FROM`
- `SUPPORT_EMAIL` opcional, default `contato@pigbankai.com`
- `ACCOUNT_DELETION_JOB_LIMIT` opcional, default `50`

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
