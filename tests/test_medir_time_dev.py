"""Testa só a lógica pura da medição do time-dev (`scripts/medir_time_dev.py` e
`scripts/_time_dev_metricas.py`): marcador, escapados, conferência, resumo e
soma de tokens deduplicada. Sem rede, sem `gh`, sem disco."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from _time_dev_metricas import (  # noqa: E402
    conferir,
    contar_escapados,
    parse_marcador,
    resumir,
)
from medir_time_dev import extrair_mapa_agentes, somar_tokens  # noqa: E402


def _usage(mid, entrada=10, cache_criacao=0, cache_leitura=0, saida=5):
    return {
        "id": mid,
        "usage": {
            "input_tokens": entrada,
            "cache_creation_input_tokens": cache_criacao,
            "cache_read_input_tokens": cache_leitura,
            "output_tokens": saida,
        },
    }


MARCADOR = "<!-- time-dev: faixa=Leve tester=3 tester_so=1 manager=2 manager_so=0 codex_antes=4 -->"


def test_parse_marcador_valido():
    assert parse_marcador(f"texto\n{MARCADOR}\nresto") == {
        "faixa": "Leve", "tester": 3, "tester_so": 1,
        "manager": 2, "manager_so": 0, "codex_antes": 4,
    }


def test_parse_marcador_codex_antes_nd():
    corpo = MARCADOR.replace("codex_antes=4", "codex_antes=nd")
    assert parse_marcador(corpo)["codex_antes"] is None


def test_parse_marcador_ausente():
    assert parse_marcador("PR sem marcador nenhum") is None
    assert parse_marcador("") is None
    assert parse_marcador(None) is None


def test_parse_marcador_malformado():
    assert parse_marcador(MARCADOR.replace("faixa=Leve", "faixa=Direto")) is None
    assert parse_marcador(MARCADOR.replace(" codex_antes=4", "")) is None
    assert parse_marcador(MARCADOR.replace("tester=3", "tester=x")) is None
    # exclusivos não podem passar do total do agente
    assert parse_marcador(MARCADOR.replace("manager_so=0", "manager_so=3")) is None


def test_parse_marcador_exemplo_citado_no_texto_nao_conta():
    assert parse_marcador(f"o formato é `{MARCADOR}`, gravado pelo orquestrador") is None
    assert parse_marcador(f"veja: {MARCADOR}") is None
    # o real, sozinho na linha (com recuo), continua valendo mesmo depois do exemplo
    real = MARCADOR.replace("tester=3", "tester=7")
    assert parse_marcador(f"exemplo `{MARCADOR}`\n  {real}\n")["tester"] == 7


def test_parse_marcador_formato_antigo_fica_fora():
    assert parse_marcador("<!-- time-dev: grupo=com faixa=Leve internos=3 bloqueantes=1 -->") is None


MERGE = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def test_escapados_contam_na_janela():
    merges = {100: MERGE, 200: MERGE}
    itens = [
        (301, "conserta\nOrigem: #100", MERGE + timedelta(days=1)),
        (302, "origem:#100 e ORIGEM: #200", MERGE + timedelta(days=14)),
        (303, "Origem: #100 Origem: #100", MERGE + timedelta(days=2)),  # uma vez só
        (304, "Origem: #100", MERGE + timedelta(days=15)),  # fora da janela
        (100, "Origem: #100", MERGE + timedelta(days=1)),  # o próprio PR
        (305, "Origem: #999", MERGE + timedelta(days=1)),  # PR fora da janela do relatório
        (306, None, MERGE),
    ]
    assert contar_escapados(itens, merges) == {100: 3, 200: 1}


def test_escapados_variacoes_de_escrita():
    merges = {100: MERGE}
    dia = MERGE + timedelta(days=1)
    variacoes = ["Origem : #100", "Origem — #100", "origem: PR #100", "Origem - issue #100"]
    itens = [(300 + i, v, dia) for i, v in enumerate(variacoes)]
    assert contar_escapados(itens, merges) == {100: 4}
    # palavra que só contém "origem" (ou parece) não conta
    falsos = [(400, "originalmente #100", dia), (401, "desorigem #100", dia)]
    assert contar_escapados(falsos, merges) == {100: 0}


def test_escapados_sem_citacao_dao_zero():
    assert contar_escapados([], {100: MERGE}) == {100: 0}


def _linha(faixa="Leve", tester=0, tester_so=0, manager=0, manager_so=0,
           codex_github=0, escapados=0, aberta=False, tokens=(1000, 10), codex_antes=0):
    return {"faixa": faixa, "tester": tester, "tester_so": tester_so, "manager": manager,
            "manager_so": manager_so, "codex_antes": codex_antes, "codex_github": codex_github,
            "escapados": escapados, "aberta": aberta, "tokens": tokens}


def test_resumo_denominador_zero_e_nd():
    r = resumir([_linha()])
    assert r["Leve"]["eficacia"] is None
    assert r["Leve"]["tokens_por_exclusivo"] is None
    assert r["Completo"]["prs"] == 0 and r["Completo"]["eficacia"] is None


def test_resumo_por_faixa_e_por_agente():
    r = resumir([
        _linha(tester=2, tester_so=1, manager=1, manager_so=1, codex_github=1, aberta=True),
        _linha(tester=1, tester_so=0, escapados=1),
        _linha(faixa="Completo", manager=4, manager_so=2),
    ])
    leve = r["Leve"]
    assert (leve["tester_so"], leve["manager_so"], leve["exclusivos"]) == (1, 1, 2)
    assert leve["eficacia"] == 4 / 6  # (3 + 1) / (3 + 1 + 1 + 1)
    assert leve["abertas"] == 1
    assert leve["tokens_por_exclusivo"] == (1000.0, 10.0)  # 2000 / 2, 20 / 2
    assert r["Completo"]["exclusivos"] == 2 and r["Completo"]["eficacia"] == 1.0


def test_resumo_tokens_nd_nao_vira_zero():
    # PR sem transcript sai da conta de tokens, e os exclusivos dele também.
    r = resumir([_linha(tester=2, tester_so=2, tokens=None),
                 _linha(tester=1, tester_so=1, tokens=(500, 50))])
    assert r["Leve"]["exclusivos"] == 3
    assert r["Leve"]["tokens_por_exclusivo"] == (500.0, 50.0)
    so_nd = resumir([_linha(tester=2, tester_so=2, tokens=None)])
    assert so_nd["Leve"]["tokens_por_exclusivo"] is None


def test_resumo_codex_antes_nd_fica_fora_dos_exclusivos():
    # Sem a lista do Codex local não se sabe o que foi exclusivo naquele PR.
    r = resumir([_linha(tester=3, tester_so=3, manager=1, manager_so=1, codex_antes=None),
                 _linha(tester=1, tester_so=1, tokens=(800, 80))])
    leve = r["Leve"]
    assert (leve["tester"], leve["manager"]) == (4, 1)  # os achados continuam contando
    assert (leve["tester_so"], leve["manager_so"], leve["exclusivos"]) == (1, 0, 1)
    assert leve["tokens_por_exclusivo"] == (800.0, 80.0)
    so_nd = resumir([_linha(tester=2, tester_so=2, codex_antes=None)])
    assert so_nd["Leve"]["exclusivos"] == 0 and so_nd["Leve"]["tokens_por_exclusivo"] is None


def test_conferir_marcador_com_transcript():
    m = parse_marcador(MARCADOR)
    tokens = {"principal": {"entrada": 5, "saida": 1}, "coder": {"entrada": 5, "saida": 1}}
    assert conferir(m, tokens, True) == "marcador não bate com o transcript"
    tokens["tester"] = {"entrada": 5, "saida": 1}
    assert conferir(m, tokens, True) == "marcador não bate com o transcript"  # falta o manager
    tokens["manager"] = {"entrada": 5, "saida": 1}
    assert conferir(m, tokens, True) == ""
    assert conferir(m, {}, False) == "não conferido"


def test_conferir_agente_sem_bug_nao_exige_transcript():
    m = parse_marcador(MARCADOR.replace("tester=3 tester_so=1", "tester=0 tester_so=0")
                       .replace("manager=2", "manager=0"))
    assert conferir(m, {"principal": {"entrada": 1, "saida": 1}}, True) == ""


def test_somar_tokens_dedup_por_message_id():
    # mesma message.id em duas linhas (dois blocos de conteúdo) conta uma vez.
    linhas = [
        {"gitBranch": "b", "message": _usage("m1", entrada=100, saida=10)},
        {"gitBranch": "b", "message": _usage("m1", entrada=100, saida=10)},
        {"gitBranch": "b", "message": _usage("m2", entrada=1, saida=1)},
    ]
    soma = somar_tokens(linhas, "b")
    assert soma == {"entrada": 101, "saida": 11}


def test_somar_tokens_filtra_branch_exato_mesmo_com_prefixo():
    # "Japa/foo" é prefixo de "Japa/foo-bar": não pode vazar tokens do outro branch.
    linhas = [
        {"gitBranch": "Japa/foo", "message": _usage("a", entrada=5, saida=1)},
        {"gitBranch": "Japa/foo-bar", "message": _usage("b", entrada=999, saida=999)},
    ]
    assert somar_tokens(linhas, "Japa/foo") == {"entrada": 5, "saida": 1}
    assert somar_tokens(linhas, "Japa/foo-bar") == {"entrada": 999, "saida": 999}


def test_somar_tokens_branch_none_nao_filtra():
    # subagente sem gitBranch próprio: já vem pré-filtrado pelo chamador (I/O).
    linhas = [{"message": _usage("a", entrada=3, saida=2)}]
    assert somar_tokens(linhas, None) == {"entrada": 3, "saida": 2}


def test_extrair_mapa_agentes():
    linhas = [
        {"type": "assistant", "message": {}},
        {"type": "user", "message": {"content": [{"type": "tool_result"}]},
         "toolUseResult": {"agentId": "id1", "agentType": "coder"}},
        {"type": "user", "message": {"content": [{"type": "tool_result"}]},
         "toolUseResult": {"agentId": "id2", "agentType": "tester"}},
        {"type": "user", "toolUseResult": "não é dict"},
    ]
    assert extrair_mapa_agentes(linhas) == {"id1": "coder", "id2": "tester"}


def test_extrair_mapa_agentes_sem_agenttype_usa_a_chamada():
    # Forma real de metade dos transcripts: o resultado não traz agentType.
    linhas = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu1", "input": {"subagent_type": "tester"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu1"}]},
         "toolUseResult": {"agentId": "a1"}},
    ]
    assert extrair_mapa_agentes(linhas) == {"a1": "tester"}
