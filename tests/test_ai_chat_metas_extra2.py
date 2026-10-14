"""PR C1 (revisão do Codex): o texto de conversa não omite aviso que a TELA mostra, e o card `metas`
formata o dinheiro do mesmo jeito que a tool (strings do JSON, nunca float).

Controles: `atual is not None` de volta na condição do aviso do custo -> vermelho em
`test_o_texto_diz_o_que_a_tela_avisa`/`test_aviso_do_custo_velho_sem_saldo_...`; o leitor do card
voltando a `listar_metas(...)` (modelo -> `_py` -> float) -> vermelho em `test_card_e_tool_...`.
"""
import itertools
import json
from decimal import Decimal

import pytest

from _cards_apoio import mostrar  # noqa: F401
from core.services import metas_conversa as mc
from core.services.ai_chat import cards
from test_ai_chat_metas import FIX, conversa, dois, lista, meta_de, sem_float  # noqa: F401  (dois é fixture)
from test_api_v2_metas import conta_do_banco, cria, fonte_conta

VELHO = "recorrencias_banco_nao_lidas"


def reserva(**campos):
    (m,) = [m for m in FIX["varios"]["metas"] if m["tipo"] == "reserva"]
    ritmo = {**m["ritmo"], **campos.pop("ritmo", {})}
    return {**m, **campos, "ritmo": ritmo}


def custo(valor="3000.00", motivos=()):
    return {"valor": valor, "recorrencias": 5, "motivos": list(motivos)}


def tela_avisa(m, c):
    """As frases de aviso que `MetaCartao.tsx` mostra (na voz da conversa: `FRASE_*`), pela leitura do .tsx."""
    r, f = m["ritmo"], []
    reserva_ = m["tipo"] == "reserva"
    if reserva_ and c["valor"] is not None and m["alvo"] is not None and VELHO in c["motivos"]:
        f.append(mc.FRASE_CUSTO[VELHO] + " O valor pode estar velho.")  # avisoCusto: não depende do saldo
    if m["atual"] is None or (r["valor"] is None and not (m["falta"] is not None and Decimal(m["falta"]) == 0)):
        f.append(mc._frase(mc.FRASE_RITMO, r["motivos"], r["dias_de_historico"]))  # <Ritmo> / FraseComAcao
    if reserva_ and m["alvo"] is None:
        f.append(mc._frase(mc.FRASE_CUSTO, c["motivos"]))  # "reserva sem o tamanho"
    f += [mc.FRASE_RITMO[k] for k in mc.FRASE_RITMO if k in m["motivos"]]  # <Selos motivos={m.motivos}>
    return [x for x in f if x]


def combinacoes():
    """tipo/saldo/alvo x custo x ritmo x motivo da fonte: o produto inteiro, só estados que a API emite."""
    saldos = {"sem saldo": dict(atual=None, falta=None, percentual=None, chega_em=None, por_mes=None),
              "saldo zero": dict(atual="0.00", falta="18000.00", percentual=0),
              "saldo": dict(atual="4500.00", falta="13500.00", percentual=25),
              "batida": dict(atual="18000.00", falta="0.00", percentual=100, chega_em=None)}
    custos = {"custo com valor": custo(), "custo velho": custo(motivos=[VELHO]),
              "sem custo": custo(None, ["sem_recorrencias"]), "custo ilegível": custo(None, [VELHO])}
    ritmos = {"ritmo": {}, "ritmo curto": dict(valor=None, dias_de_historico=4, parcial=False, motivos=["historico_curto"]),
              "ritmo sem motivo": dict(valor=None, dias_de_historico=None, parcial=False, motivos=[])}
    for (sn, s), (cn, c), (rn, r), mot in itertools.product(
            saldos.items(), custos.items(), ritmos.items(), ([], ["banco_desatualizado"])):
        if s["atual"] is None:
            r = {**r, "valor": None, "chega_em": None} if r else dict(valor=None, dias_de_historico=None, parcial=False,
                                                                       motivos=["plano_sem_investimentos"])
        # a reserva sem custo fica sem alvo e, sem alvo, sem falta/percentual
        campos = {**s, "alvo": "18000.00"} if c["valor"] else {**s, **dict.fromkeys(("alvo", "falta", "percentual", "chega_em"))}
        yield f"{sn}|{cn}|{rn}|{mot}", reserva(**campos, ritmo=r, motivos=mot), c


CASOS = list(combinacoes())


@pytest.mark.parametrize("m, c", [(m, c) for _, m, c in CASOS], ids=[i for i, _, _ in CASOS])
def test_o_texto_diz_o_que_a_tela_avisa(m, c):
    item = conversa(m, custo=c)["metas"][0]
    texto = item["resumo"] + " " + " ".join(item["avisos"])
    faltou = [f for f in tela_avisa(m, c) if f not in texto]
    assert not faltou, faltou
    sem_float(item)


def test_aviso_do_custo_velho_sem_saldo_leva_o_tamanho_e_nao_duplica():
    m = reserva(atual=None, falta=None, percentual=None, chega_em=None,
                ritmo=dict(valor=None, dias_de_historico=None, parcial=False, motivos=["plano_sem_investimentos"]))
    r = conversa(m, custo=custo(motivos=[VELHO]))["metas"][0]["resumo"]
    assert r.endswith("O tamanho da reserva é R$ 18.000,00. Não consegui ler as cobranças do banco agora. O valor pode estar velho.")
    assert r.count("O valor pode estar velho") == 1
    com_saldo = conversa(reserva(), custo=custo(motivos=[VELHO]))["metas"][0]["resumo"]
    assert com_saldo.count("O valor pode estar velho") == 1 and "O tamanho da reserva é" not in com_saldo
    # positivo: sem o motivo, ou com a meta comum, nada disso aparece
    assert "pode estar velho" not in conversa(m, custo=custo())["metas"][0]["resumo"]
    assert "pode estar velho" not in conversa(meta_de("Viagem pro Japão"), custo=custo(motivos=[VELHO]))["metas"][0]["resumo"]


def test_reserva_com_saldo_e_sem_tamanho_diz_por_que_o_ritmo_nao_tem_numero():
    m = reserva(alvo=None, falta=None, percentual=None, chega_em=None,
                ritmo=dict(valor=None, dias_de_historico=4, parcial=False, motivos=["historico_curto"]))
    r = conversa(m, custo=custo(None, ["sem_recorrencias"]))["metas"][0]["resumo"]
    assert "Ainda estou juntando histórico (4 de 7 dias)" in r
    assert "histórico" not in conversa(reserva(alvo=None, falta=None, percentual=None, chega_em=None),
                                       custo=custo(None, ["sem_recorrencias"]))["metas"][0]["resumo"]


# ── B: o card e a tool formatam o mesmo dinheiro, mesmo além da precisão de um float ───────────────

# `atual` de conta não é quantizado (saldo numeric sem escala) e vem do banco com mais casas que um
# float guarda: 0,00499...9 vira 0.005 em float e o `ROUND_HALF_UP` do texto sobe para R$ 0,01.
MEIO_CENTAVO = "0.0049999999999999999"


def test_card_e_tool_dizem_o_mesmo_com_dinheiro_alem_do_float(dois):
    a, _, _ = dois
    conta_do_banco(a, saldo=MEIO_CENTAVO)
    cria(a, nome="Conta fina", fonte=fonte_conta(a), alvo="1000")
    r, (bloco,), _ = mostrar(a, card="metas")
    tool = {k: v for k, v in lista(a).items() if k != "onde_criar"}
    (fina,) = [m for m in bloco["abas"][0]["recursos"]["metas"]["metas"] if m["nome"] == "Conta fina"]
    assert fina["atual"] == MEIO_CENTAVO  # o bloco: string decimal, como `/api/v2/metas`
    (visao,) = [m for m in tool["metas"] if m["nome"] == "Conta fina"]
    assert visao["resumo"].startswith("R$ 0,00 de R$ 1.000,00")
    assert r["abas"] == [tool]  # o texto do modelo é o da tool
    sem_float(r)
    json.dumps(r)


def test_o_leitor_do_card_metas_devolve_o_dump_json_como_a_rota(dois):
    a, _, _ = dois
    from api.v2.metas import listar
    (_, objs), = cards.registro()["metas"].ler(a, {"meses": []})
    assert objs["metas"] == listar(uid=a).model_dump(mode="json")
    assert isinstance(objs["metas"]["metas"][0]["atual"], str)
