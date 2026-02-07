# Manual do Bot Financeiro (Uso Interno – Só para Devs)

Este documento serve como referência rápida para lembrar os comandos do bot e algumas operações comuns de desenvolvimento e manutenção do banco de dados.

## Ativar o venv no terminal

source .venv/bin/activate
Certifique-se que existe a pasta .venv

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
users,
accounts,
launches,
pockets,
investments,
pending_actions
RESTART IDENTITY CASCADE;

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

## Observações Importantes

- Este arquivo é apenas para referência interna do desenvolvedor.
- Não deve ser exposto ao público final.
- Sempre que novos comandos forem adicionados ao bot, atualize este manual.
