# Manual do Bot Financeiro (Uso Interno – Só para Devs)

Este documento serve como referência rápida para lembrar os comandos do bot e algumas operações comuns de desenvolvimento e manutenção do banco de dados.

## Ativar o venv no terminal

source .venv/bin/activate
Certifique-se que existe a pasta .venv

## Comando para ativar o bot WhatsApp
Runtime recomendado:

```
python3 -m uvicorn adapters.whatsapp.wa_app:app --host 0.0.0.0 --port 5001
ngrok http 5001
```

Legado / compatibilidade Flask:

```
PORT=5001 python3 -m adapters.whatsapp.wa_webhook
ngrok http 5001
```

## Comandos do Bot (Usuário Final)

### Registrar despesa

Comando:
gastei 10 mercado

Função:
Registra uma despesa no banco de dados com valor e descrição informados.

### Registrar receita

Comando:
recebi 1000 salario

Função:
Registra uma receita no banco de dados com valor e descrição informados.

### Listar lançamentos

Comando:
listar lancamentos

Função:
Lista todos os lançamentos (despesas e receitas) cadastrados para o usuário.

### Apagar lançamento (com confirmação)

Comando:
apagar 11

Confirmação:
sim
nao

Função:
Solicita a exclusão de um lançamento pelo ID. O bot pede confirmação antes de apagar definitivamente.

### Consultar saldo

Comando:
saldo

Função:
Mostra o saldo atual calculado a partir das receitas menos as despesas.

## Caixinhas (Poupança por Objetivo)

### Criar caixinha

Comando:
criar caixinha viagem

Função:
Cria uma caixinha com o nome informado para organizar dinheiro por objetivo.

### Colocar dinheiro na caixinha

Comando:
coloquei 100 na caixinha viagem

Função:
Move o valor informado do saldo principal para a caixinha.

### Retirar dinheiro da caixinha

Comando:
retirei 50 da caixinha viagem

Função:
Move o valor informado da caixinha de volta para o saldo principal.

## Operações de Desenvolvimento (Banco de Dados / DBeaver)

### Limpar tabelas (resetar dados mantendo estrutura)

Objetivo:
Apagar todos os dados das tabelas sem apagar as tabelas em si.

Comando SQL (executar no DBeaver):

TRUNCATE TABLE
  credit_transactions,
  credit_bills,
  credit_cards,
  user_category_rules,
  pending_actions,
  launches,
  pockets,
  investments,
  ofx_imports,
  link_codes,
  user_identities,
  accounts,
  users
RESTART IDENTITY
CASCADE;

Função:
Remove todos os registros e reseta os IDs (auto increment) das tabelas.

### Ver tabelas existentes

Comando SQL:

SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;

Função:
Lista todas as tabelas existentes no schema público do Postgres.

### Testar conexão com o banco

Comando no terminal:

python smoke_db.py

Função:
Executa um teste rápido de conexão com o banco para garantir que as variáveis de ambiente e a conexão estão corretas.

## Operações Úteis de Desenvolvimento

### Inicializar banco manualmente

Comando:

python -c "from db import init_db; init_db()"

Função:
Cria as tabelas no banco caso ainda não existam.

### Rodar o bot localmente

Comando:

python bot.py

Função:
Inicia o bot localmente para testes no Discord.

### Rodar testes automatizados

Comando:

pytest -q

Função:
Executa a suíte de testes automatizados para validar as principais funcionalidades do projeto.

## E-mail Transacional (Boas-vindas no Cadastro)

O bot envia automaticamente um e-mail de boas-vindas quando um usuário cria conta via `register_auth_user()`.
O e-mail inclui o código de vinculação (válido por 15 min) e instruções para conectar no WhatsApp e Discord.

### Variáveis de ambiente necessárias

```
SMTP_HOST=smtp.gmail.com       # ou smtp.mailgun.org, smtp.sendgrid.net, etc.
SMTP_PORT=587
SMTP_USER=seuemail@gmail.com   # remetente
SMTP_PASSWORD=sua_app_password # para Gmail: gerar em myaccount.google.com/apppasswords
EMAIL_FROM_NAME=Bot Financeiro  # nome exibido no campo "De:"
```

### Como configurar com Gmail

1. Ative a verificação em duas etapas na conta Google
2. Acesse myaccount.google.com/apppasswords
3. Gere uma senha de app para "Correio"
4. Use essa senha em `SMTP_PASSWORD` (não a senha da conta Google)

### Comportamento em caso de falha

Se o SMTP não estiver configurado ou falhar, o cadastro **não é bloqueado**.
O erro é logado como WARNING e o usuário é criado normalmente.
O código de vinculação ainda é retornado na resposta da API para ser exibido na tela.

### Testar o envio localmente

```python
from core.services.email_service import send_welcome_email
send_welcome_email("teste@exemplo.com", "123456", "http://localhost:8000")
```

---

## Observações Importantes

- Este arquivo é apenas para referência interna do desenvolvedor.
- Não deve ser exposto ao público final.
- Sempre que novos comandos forem adicionados ao bot, atualize este manual.

## Comando para ativar dashboard
cd "Bot Financeiro"
source .venv/bin/activate
python3 finance_bot_websocket_custom.py
