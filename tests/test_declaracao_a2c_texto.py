"""PR-A2c (ADR 0006): O QUE o lembrete diz (G7), com o texto reescrito pelo dono (D11) e o isolamento entre usuários.
Postgres real, `send_text` real, relógio injetado.

Controles NEGATIVOS (numa CÓPIA; `core/services/declaracao_lembrete.py`):
- enviar uma mensagem por declaração (o `for` de envio dentro do laço das linhas) -> VERMELHO
  `test_tres_declaracoes_viram_uma_mensagem`; reservar uma a uma -> o mesmo + `test_mais_de_cinco_...`;
- calcular `targets` com `list_identities_by_user(<primeiro user_id da listagem>)` -> VERMELHO
  `test_cada_telefone_recebe_so_as_suas`;
- voltar o texto anterior (negrito no alvo, "toque em *movimentações não confirmadas*", "ou apaga") -> VERMELHO os
  textos exatos;
- voltar a incluir o "Para apagar por aqui, mande *apagar #N*." ou o `#N` na linha -> VERMELHO os textos exatos e
  `test_o_lembrete_nao_oferece_apagar_por_whatsapp`;
- tirar o `[:60]` -> VERMELHO `test_nome_longo_corta_em_60_depois_de_juntar_espacos`; embrulhar o alvo em `*` de novo
  -> VERMELHO os textos exatos e `test_nome_com_marcacao_vai_como_o_usuario_escreveu`;
- `_MAX_LINHAS = 5` virar 6 ou 4 -> VERMELHO `test_mais_de_cinco_mostra_as_cinco_mais_antigas_e_reserva_todas`;
- ordenar o `sorted` do claim pelo `launch_id` só -> VERMELHO `test_ordem_e_a_do_gasto_mais_antigo_primeiro`;
- imprimir o dia UTC em vez do dia do app (`dia()` com `declared_at.astimezone(utc)`) -> VERMELHO
  `test_a_data_impressa_e_o_dia_do_app_e_nao_o_utc` (as 3 formas).
Positivos: os textos exatos de despesa, receita, lista de 3, lista de 8 e alvo vazio.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

import core.services.declaracao_lembrete as dl
import db
from tests._lembrete_declaracao import (  # noqa: F401 (_event_logs é fixture autouse)
    FONE_A, FONE_B, LINK, RODAPE, _event_logs, agora, armar, corpos, declara_ha, dono, para, reservadas)
from tests._patrimonio_helpers import q

AG = agora()


def _d(dias: int) -> str:
    return (AG - timedelta(days=dias)).strftime("%d/%m")


def _conferir(acoes: str) -> str:
    """A instrução do dono (D11) mais o link; a seta e os acentos saem como UTF-8, sem escape."""
    return ("\nPara conferir, abra o app e vá em Visão geral → Patrimônio total → Movimentações não confirmadas. "
            f"Lá, você pode {acoes} ou excluir o lançamento. {LINK}")


def _manda(uid, posts, fone=FONE_A):
    assert dl.notify_declaracoes_whatsapp_once(now=AG) >= 1
    return corpos(posts, fone)


def test_despesa_texto_exato(user_id, monkeypatch):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    declara_ha(user_id, AG, 8, valor=50, alvo="mercado")

    assert _manda(user_id, posts) == [
        f"🐷 O gasto com mercado, de R$ 50,00, que você marcou como pago pelo banco em {_d(8)}, "
        "ainda não apareceu no seu extrato."
        + _conferir("vincular o gasto à transação do banco, indicar que pagou em dinheiro") + RODAPE]


def test_receita_texto_exato(user_id, monkeypatch):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    declara_ha(user_id, AG, 8, valor=300, tipo="receita", alvo="freela")

    assert _manda(user_id, posts) == [
        f"🐷 A receita de freela, de R$ 300,00, que você marcou como recebida pelo banco em {_d(8)}, "
        "ainda não apareceu no seu extrato."
        + _conferir("vincular a receita à transação do banco, indicar que recebeu em dinheiro") + RODAPE]


@pytest.mark.parametrize("alvo", ["", None, "   \n "], ids=["vazio", "nulo", "so_espacos"])
def test_alvo_vazio_nao_escreve_com_sem_descricao(user_id, monkeypatch, alvo):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    declara_ha(user_id, AG, 8, valor=50, alvo=alvo)

    assert _manda(user_id, posts) == [
        f"🐷 O gasto, de R$ 50,00, que você marcou como pago pelo banco em {_d(8)}, ainda não apareceu no seu extrato."
        + _conferir("vincular o gasto à transação do banco, indicar que pagou em dinheiro") + RODAPE]


def test_receita_de_alvo_vazio_texto_exato(user_id, monkeypatch):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    declara_ha(user_id, AG, 8, valor=300, tipo="receita", alvo="")

    assert _manda(user_id, posts) == [
        f"🐷 A receita, de R$ 300,00, que você marcou como recebida pelo banco em {_d(8)}, "
        "ainda não apareceu no seu extrato."
        + _conferir("vincular a receita à transação do banco, indicar que recebeu em dinheiro") + RODAPE]


def test_tres_declaracoes_viram_uma_mensagem(user_id, monkeypatch):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    declara_ha(user_id, AG, 10, valor=50, alvo="mercado")
    declara_ha(user_id, AG, 9, valor=300, tipo="receita", alvo="freela")
    declara_ha(user_id, AG, 8, valor=20, alvo="")

    assert _manda(user_id, posts) == [
        "🐷 Estas 3 movimentações que você marcou como feitas pelo banco ainda não apareceram no seu extrato:\n"
        f"• gasto mercado, R$ 50,00, {_d(10)}\n"
        f"• receita freela, R$ 300,00, {_d(9)}\n"
        f"• gasto sem descrição, R$ 20,00, {_d(8)}"
        + _conferir("vincular cada uma à transação do banco, indicar que foi em dinheiro") + RODAPE]
    assert reservadas(user_id) == 3


def test_ordem_e_a_do_gasto_mais_antigo_primeiro(user_id, monkeypatch):
    """Criada primeiro (id menor) mas com gasto mais RECENTE: sai depois."""
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    recente = declara_ha(user_id, AG, 8, alvo="recente")
    antiga = declara_ha(user_id, AG, 20, alvo="antiga")

    [corpo] = _manda(user_id, posts)
    assert corpo.index("gasto antiga,") < corpo.index("gasto recente,") and recente < antiga


def test_mais_de_cinco_mostra_as_cinco_mais_antigas_e_reserva_todas(user_id, monkeypatch):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    for i in range(8):  # g0 é a mais antiga
        declara_ha(user_id, AG, 20 - i, valor=10 + i, alvo=f"g{i}")

    assert _manda(user_id, posts) == [
        "🐷 Estas 8 movimentações que você marcou como feitas pelo banco ainda não apareceram no seu extrato:\n"
        + "\n".join(f"• gasto g{i}, R$ {10 + i},00, {_d(20 - i)}" for i in range(5)) + "\n• e mais 3, no app."
        + _conferir("vincular cada uma à transação do banco, indicar que foi em dinheiro") + RODAPE]
    assert reservadas(user_id) == 8  # as omitidas também ficam como lembradas
    assert dl.notify_declaracoes_whatsapp_once(now=AG) == 0 and len(para(posts, FONE_A)) == 1


def test_o_lembrete_nao_oferece_apagar_por_whatsapp(user_id, monkeypatch):
    """D9: o `#N` reaproveitado (DP-5) faria "apagar #1"+"sim" apagar o lançamento NOVO; o lembrete só aponta o app."""
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    declara_ha(user_id, AG, 8)
    declara_ha(user_id, AG, 9, alvo="padaria")

    [corpo] = _manda(user_id, posts)
    assert "#" not in corpo and "Para apagar" not in corpo


def test_o_texto_do_app_que_a_mensagem_manda_abrir_existe(user_id):
    """§0.7: a mensagem manda abrir o que o app mostra; se o dashboard mudar esse texto, a instrução fica falsa.
    "Movimentações não confirmadas" PARAFRASEIA o botão do app ("N movimentação(ões) não confirmada(s)"), então a
    prova é sobre os literais REAIS do dashboard.js, sem diferenciar maiúscula."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "frontend" / "dashboard.js").read_text(encoding="utf-8").lower()
    assert "patrimônio total" in js and "movimentação(ões) não confirmada(s)</button>" in js


def test_nome_longo_corta_em_60_depois_de_juntar_espacos(user_id, monkeypatch):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    declara_ha(user_id, AG, 8, alvo=("a" * 30) + "  \n  " + ("b" * 40))

    [corpo] = _manda(user_id, posts)
    assert f"gasto com {'a' * 30} {'b' * 29}," in corpo and "b" * 30 not in corpo


def test_nome_com_marcacao_vai_como_o_usuario_escreveu(user_id, monkeypatch):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    declara_ha(user_id, AG, 8, alvo="*promo* _top_")

    [corpo] = _manda(user_id, posts)
    assert "O gasto com *promo* _top_, de R$ 50,00" in corpo and "**" not in corpo


def test_valor_e_o_da_declaracao_nao_o_projetado(user_id, monkeypatch):
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, AG)
    lid = declara_ha(user_id, AG, 8, valor=50)
    q("update launches set valor=51.23 where id=%s", (lid,))  # a projeção do par divergiu; o declarado é 50

    [corpo] = _manda(user_id, posts)
    assert "de R$ 50,00" in corpo and "51,23" not in corpo


def test_cada_telefone_recebe_so_as_suas(user_id, monkeypatch):
    from conftest import _cleanup_user
    posts = armar(monkeypatch)
    outro = user_id + 1
    try:
        db.ensure_user(outro)
        dono(user_id, FONE_A, AG)
        dono(outro, FONE_B, AG)
        declara_ha(user_id, AG, 8, alvo="do A", valor=11)
        declara_ha(outro, AG, 8, alvo="do B", valor=22)
        declara_ha(outro, AG, 9, alvo="do B2", valor=33)

        assert dl.notify_declaracoes_whatsapp_once(now=AG) == 2
        [a], [b] = corpos(posts, FONE_A), corpos(posts, FONE_B)
        assert "gasto com do A," in a and "R$ 11,00" in a and "do B" not in a and "22,00" not in a
        assert "gasto do B," in b and "gasto do B2," in b and "do A" not in b and "11,00" not in b
        assert reservadas(user_id) == 1 and reservadas(outro) == 2
    finally:
        _cleanup_user(outro)


def test_o_link_vem_do_dominio_configurado(user_id, monkeypatch):
    posts = armar(monkeypatch)
    monkeypatch.setenv("DASHBOARD_URL", "https://app.exemplo.test/")
    dono(user_id, FONE_A, AG)
    declara_ha(user_id, AG, 8)

    [corpo] = _manda(user_id, posts)
    assert "https://app.exemplo.test/app?view=overview" in corpo and "pigbankai.com" not in corpo


@pytest.mark.parametrize("tipos", [["despesa"], ["receita"], ["despesa", "receita"]], ids=["despesa", "receita", "lista"])
def test_a_data_impressa_e_o_dia_do_app_e_nao_o_utc(user_id, monkeypatch, tipos):
    """23h30 em São Paulo já é o dia seguinte em UTC; o dia esperado vem do `zoneinfo` direto, não do produto."""
    monkeypatch.setenv("REPORT_TIMEZONE", "America/Sao_Paulo")
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    dia = ag.date() - timedelta(days=8)
    for tipo in tipos:
        declara_ha(user_id, ag, tipo=tipo, quando=datetime.combine(dia, time(23, 30), tzinfo=ZoneInfo("America/Sao_Paulo")))

    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 1
    [corpo] = corpos(posts, FONE_A)
    local, utc = dia.strftime("%d/%m"), (dia + timedelta(days=1)).strftime("%d/%m")
    assert corpo.count(local) == len(tipos) and utc not in corpo
