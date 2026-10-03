"""Produção hasheia no custo padrão do bcrypt (12), não no custo 4 dos testes.

O conftest troca `bcrypt.gensalt` por custo 4 para a suíte não gastar minutos
hasheando — o que deixa os testes de auth verdes mesmo se alguém baixar o custo
em `db/`. Este teste desfaz a troca (o original está em `bcrypt.gensalt_padrao`)
e confere o prefixo dos dois hashes de senha do sistema.
"""
import bcrypt

import db.mfa
import db.users


def test_hash_de_producao_usa_custo_12(monkeypatch):
    monkeypatch.setattr(bcrypt, "gensalt", bcrypt.gensalt_padrao)

    senha = db.users._hash_password("x")
    backup = db.mfa._hash_backup_code("ABCDE-FGH23")

    assert senha.startswith("$2b$12$"), senha[:7]
    assert backup.startswith("$2b$12$"), backup[:7]
