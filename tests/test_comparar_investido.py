"""`scripts/comparar_investido.py` contra o banco de teste, numa transação SÓ DE LEITURA.

Um usuário por balde; a saída padrão (todos os usuários) é só contagem, sem user_id.
"""
from conftest import usuario_pagante
from db.connection import get_conn
from scripts.comparar_investido import BALDES, comparar
from tests._patrimonio_helpers import conexao, investimento_manual, posicao


def _rodar(usuarios=None):
    with get_conn() as conn:
        conn.execute("set transaction read only")
        linhas = comparar(conn, usuarios)
        conn.rollback()
    return linhas


def test_um_usuario_por_balde_e_a_saida_padrao_sem_user_id():
    so_manual, manual_e_banco, so_banco, nada, igual = (usuario_pagante() for _ in range(5))
    investimento_manual(so_manual, "CDB", "100")
    investimento_manual(manual_e_banco, "CDB", "100")
    posicao(conexao(manual_e_banco, f"item-{manual_e_banco}"), "inv-1", "50")
    posicao(conexao(so_banco, f"item-{so_banco}"), "inv-1", "50")
    conexao(igual, f"item-{igual}")  # banco sem posição: 0 -> 0.00
    usuarios = [so_manual, manual_e_banco, so_banco, nada, igual]

    linhas = _rodar(usuarios)
    por_uid = {int(l.split(" | ")[0]): l.rsplit(" | ", 1)[1] for l in linhas if " | " in l}
    assert por_uid == {so_manual: "manual_sem_banco", manual_e_banco: "manual_com_banco",
                       so_banco: "so_banco", nada: "nada", igual: "igual"}
    assert linhas[-len(BALDES):] == [f"{b}: 1" for b in BALDES]

    padrao = _rodar()
    assert len(padrao) == len(BALDES) and all(l.split(": ")[0] in BALDES for l in padrao)
    assert not any(str(u) in "\n".join(padrao) for u in usuarios)
