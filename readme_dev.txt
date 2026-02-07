========================================
MANUAL RÁPIDO – COMANDOS ESSENCIAIS DO BOT
Projeto: Bot Financeiro (Discord + Postgres)
========================================

---------------------------
1) Ambiente / Setup
---------------------------

# Ativar venv (se existir)
source .venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# (Opcional) atualizar pip
pip install --upgrade pip


---------------------------
2) Variáveis de Ambiente
---------------------------

# Exportar DATABASE_URL (Postgres)
export DATABASE_URL="postgresql://USUARIO:SENHA@HOST:PORT/NOME_DO_BANCO"

# Exportar token do Discords
export DISCORD_TOKEN="SEU_TOKEN_DO_DISCORD"

# (Opcional) IA
export OPENAI_API_KEY="SUA_CHAVE_OPENAI"


---------------------------
3) Inicializar Banco
---------------------------

# Rodar init_db manualmente (opcional)
python -c "from db import init_db; init_db(); print('DB OK')"


---------------------------
4) Rodar o Bot Localmente
---------------------------

python bot.py


---------------------------
5) Testes de Fumaça (Smoke DB)
---------------------------

# (Arquivo local – não versionado)
python smoke_db.py

# Esperado:
#   ✅ SMOKE DB OK


---------------------------
6) Testes Automatizados (Pytest)
---------------------------

# Rodar todos os testes
pytest -q

# Rodar com detalhes
pytest -vv


---------------------------
7) Reset de Banco (DBeaver / SQL)
---------------------------

-- Zerar dados (mantém tabelas)
TRUNCATE TABLE
users,
accounts,
launches,
pockets,
investments,
pending_actions
RESTART IDENTITY CASCADE;

-- Listar tabelas existentes
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;


---------------------------
8) Git – Comandos Úteis
---------------------------

# Ver status
git status

# Adicionar mudanças
git add .

# Commit
git commit -m "mensagem do commit"

# Enviar para o repositório
git push

# Tirar arquivo do controle de versão (sem apagar local)
git rm --cached smoke_db.py


---------------------------
9) Debug / Logs
---------------------------

# Ver logs no Railway:
# Dashboard Railway → Service (worker) → Logs

# Rodar local e ver logs no terminal:
python bot.py


---------------------------
10) Fluxo de Teste no Discord
---------------------------

# Criar lançamento
gastei 10 teste
recebi 100 salario

# Listar lançamentos
listar lancamentos

# Apagar com confirmação
apagar id 11
sim
nao

# Saldo
saldo

# Caixinhas
criar caixinha viagem
coloquei 100 na caixinha viagem
retirei 50 da caixi
