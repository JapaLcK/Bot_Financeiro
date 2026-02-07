# Bot Financeiro

Simple overview of use/purpose.  
Bot de controle financeiro pessoal para Discord, com persistência em PostgreSQL, suporte a comandos em linguagem natural simplificada e testes automatizados com pytest.

## Description

Bot financeiro desenvolvido em Python para auxiliar no controle de despesas e receitas diretamente pelo Discord. O projeto permite registrar gastos, ganhos, consultar lançamentos, verificar saldo e organizar dinheiro em caixinhas (poupanças por objetivo). Os dados são armazenados em banco de dados PostgreSQL, garantindo persistência entre execuções. O bot foi estruturado de forma modular, com foco em testes, organização de código e possibilidade de expansão futura, incluindo integrações com IA e novos módulos financeiros.

## Getting Started

### Dependencies

- Python 3.10+
- PostgreSQL
- Discord Bot Token
- pip
- Sistema operacional: macOS, Linux ou Windows

Bibliotecas principais (requirements.txt):
- discord.py
- psycopg[binary]
- python-dotenv
- pytest
- openai (opcional)

### Installing

- Clonar o repositório do projeto
- Criar e ativar um ambiente virtual
- Instalar as dependências
- Configurar as variáveis de ambiente
- Garantir que o banco PostgreSQL esteja acessível

### Executing program

- Criar ambiente virtual
- Ativar o ambiente
- Instalar dependências
- Exportar variáveis de ambiente
- Rodar o bot

Comandos:

python -m venv .venv  
source .venv/bin/activate  
pip install -r requirements.txt  
export DATABASE_URL="postgresql://user:pass@host:port/db"  
export DISCORD_TOKEN="seu_token"  
export OPENAI_API_KEY="sua_chave_opcional"  
python bot.py  

## Help

Se o bot não conectar no banco:

- Verifique se a variável DATABASE_URL está configurada corretamente
- Confirme se o PostgreSQL está rodando
- Rode o teste de conexão:

    python smoke_db.py

Se o bot não subir no Discord:

- Verifique se o DISCORD_TOKEN está correto
- Confirme se o bot foi adicionado ao servidor do Discord
- Confira os logs no terminal

## Authors

Lucas (autor do projeto)

## Version History

- 0.2
  - Melhorias na estrutura do banco e fluxo de confirmação de ações
  - Ajustes nos testes automatizados

- 0.1
  - Initial Release

## License

Non-existent


