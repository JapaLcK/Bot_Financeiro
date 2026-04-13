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

Webhook da Meta no runtime atual:

```
GET  /wa/webhook   # verificação
POST /wa/webhook   # mensagens recebidas
```

Compatibilidade legada também aceita:

```
GET  /webhook
POST /webhook
```

Legado / compatibilidade Flask:

```
PORT=5001 python3 -m adapters.whatsapp.wa_webhook
ngrok http 5001
```

## WhatsApp Cloud API - checklist operacional

Use este procedimento sempre que configurar um novo número, trocar token, migrar ambiente ou diagnosticar falhas de recebimento.

### Variáveis obrigatórias

No ambiente do serviço web/dashboard, configure:

```bash
WA_TOKEN=<token ativo com acesso ao app e ao WhatsApp Business>
WA_PHONE_NUMBER_ID=<phone_number_id do número conectado>
WA_VERIFY_TOKEN=<token usado na verificação do webhook>
WA_APP_SECRET=<App Secret da Meta para validar assinatura>
```

Observações:
- `WA_PHONE_NUMBER_ID` é o ID técnico do número na Meta, não o telefone em si.
- Sempre prefira um token novo gerado por `Usuário do sistema` no Business Manager.
- Depois de trocar variáveis no Railway, use `Restart` no serviço `dashboard`.

### Permissões do token

Ao gerar o token do `Usuário do sistema`, conceda no mínimo:

- `business_management`
- `whatsapp_business_management`
- `whatsapp_business_messaging`
- `whatsapp_business_manage_events`

Também confirme que o `Usuário do sistema` tem acesso com controle total a:

- App da Meta usado pelo bot
- Conta do WhatsApp Business (WABA)
- Número do WhatsApp, quando aparecer como ativo atribuível

### IDs que precisam bater

No painel da Meta, em `WhatsApp > API Setup`, copie e confira:

- `phone_number_id`
- `whatsapp business account id` (`WABA_ID`)

Esses valores devem bater com o ambiente e com os testes de API.

### Verificação do webhook

O callback do ambiente de produção deve responder com o challenge:

```text
https://SEU_DOMINIO/wa/webhook?hub.mode=subscribe&hub.verify_token=SEU_WA_VERIFY_TOKEN&hub.challenge=1234
```

Resultado esperado:

```text
1234
```

Se voltar `forbidden`, o `WA_VERIFY_TOKEN` do ambiente está incorreto ou o serviço certo não está atendendo a URL.

### Subscription do app no WABA

Para números próprios, não basta ter o webhook configurado; o app também precisa estar inscrito no WABA para receber eventos reais.

Verificar:

```bash
curl -i -X GET "https://graph.facebook.com/v25.0/<WABA_ID>/subscribed_apps" \
  -H "Authorization: Bearer <WA_TOKEN>"
```

Inscrever o app no WABA:

```bash
curl -i -X POST "https://graph.facebook.com/v25.0/<WABA_ID>/subscribed_apps" \
  -H "Authorization: Bearer <WA_TOKEN>"
```

Depois do `POST`, o `GET` deve listar o app.

### Teste de envio direto pela API

Antes de culpar o webhook, valide se o token realmente consegue operar o número:

```bash
curl -i -X POST "https://graph.facebook.com/v25.0/<PHONE_NUMBER_ID>/messages" \
  -H "Authorization: Bearer <WA_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "messaging_product": "whatsapp",
    "to": "55DDDNUMERO",
    "type": "template",
    "template": {
      "name": "hello_world",
      "language": { "code": "en_US" }
    }
  }'
```

Se esse request falhar com `Object with ID ... does not exist` ou `missing permissions`, o token não tem acesso real ao número.

### Teste inbound / recebimento

Com a subscription ativa, mande uma mensagem real para o número e confira os logs HTTP do serviço `dashboard`.

Esperado:

```text
POST /wa/webhook 200
```

Nos logs de aplicação, procure por eventos como:

- `WA webhook received`
- `extracted_messages=1`
- `process_message`
- `send_text_result`

### Interpretação rápida de sintomas

- `GET /wa/webhook?...challenge=1234` retorna `1234`: webhook e verify token ok.
- `POST /wa/webhook 200` aparece só no teste do painel, mas não na mensagem real: faltando subscription do app no WABA ou problema de contexto do número.
- `POST /messages` funciona, mas mensagem real não chega no webhook: envio ok, recebimento ainda não inscrito/propagado.
- número de teste americano funciona e número próprio não: normalmente faltava `subscribed_apps` no WABA do número próprio.
- `Object with ID '<PHONE_NUMBER_ID>' does not exist`: token sem acesso operacional ao número.

### Procedimento completo recomendado para novo número

1. Conectar e verificar o número no WhatsApp Manager.
2. Executar o registro do número, se o fluxo pedir.
3. Copiar `PHONE_NUMBER_ID` e `WABA_ID` em `WhatsApp > API Setup`.
4. Gerar token novo via `Usuário do sistema`.
5. Atualizar `WA_TOKEN` e `WA_PHONE_NUMBER_ID` no ambiente.
6. Confirmar callback `https://SEU_DOMINIO/wa/webhook`.
7. Validar challenge com `hub.challenge=1234`.
8. Executar `POST /<WABA_ID>/subscribed_apps`.
9. Validar `GET /<WABA_ID>/subscribed_apps`.
10. Testar envio com `POST /<PHONE_NUMBER_ID>/messages`.
11. Mandar uma mensagem real para o número.
12. Confirmar `POST /wa/webhook 200` e resposta do bot.

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

### Ambiente isolado de testes

Recomendação:
- Produção e testes devem usar `DATABASE_URL`, tokens do Discord e credenciais do WhatsApp diferentes.
- O projeto agora aceita `APP_ENV` para carregar um arquivo dedicado, como `.env.staging`.

Exemplo de setup local:

```bash
cp .env.example .env.staging
```

Preencha no `.env.staging`:
- `DATABASE_URL` apontando para um Postgres separado
- `DISCORD_BOT_TOKEN` de um segundo bot do Discord
- `JWT_SECRET` próprio
- credenciais próprias de WhatsApp, se quiser testar esse canal também

Para subir o ambiente de testes local:

```bash
APP_ENV=staging python launch.py
```

Para subir só o bot Discord de testes:

```bash
APP_ENV=staging python bot.py
```

No Railway:
- crie um segundo serviço/projeto para staging
- configure as variáveis desse serviço com banco e tokens de teste
- opcionalmente defina `APP_ENV=staging` se quiser manter o mesmo padrão local

### Conexão automática do WhatsApp

O dashboard agora pode gerar um link `wa.me` de onboarding automático.

Fluxo:
- o site gera um token curto de onboarding para o usuário autenticado
- o botão do WhatsApp abre o bot com uma mensagem pré-preenchida
- na primeira mensagem, o backend consome o token e vincula o número do WhatsApp à conta

Observações:
- o token expira e só pode ser usado uma vez
- o usuário não precisa mais digitar `vincular 123456` para o fluxo web → WhatsApp

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
