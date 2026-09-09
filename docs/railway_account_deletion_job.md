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
