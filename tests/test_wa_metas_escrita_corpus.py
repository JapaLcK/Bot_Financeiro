"""PR C4: o gate gramatical (`core/services/metas_gate.py`, puro em `tests/test_metas_gate.py`) POR DENTRO do runner,
com o modelo FALSO que SEMPRE chama `create_meta` (o pior caso) e banco real.
Controles (injetados e restaurados): o gate da passada 1 (`antigo`, abaixo) devolvido ao lugar -> `test_controle_com_o_gate_antigo_*`
(as hedgeadas do Manager gravam direto); o gate sempre False -> `test_controle_sem_a_regra_*`; `except Exception: return False`
no `_confirmar_criar` -> `test_falha_inesperada_*` (grava sem "sim"); relançar a exceção -> o mesmo teste (tool_call órfão no histórico).
"""
from __future__ import annotations

import re

import pytest
from fastapi import HTTPException

from conftest import usuario_pagante
from core.intent_classifier import _OUTRO_ASSUNTO, _normalize
from core.services.ai_chat import runner
from core.services.ai_chat._context import CURRENT_USER_MESSAGE
from core.services.ai_chat.tools import metas_escrita as me
from tests._ia_falsa_helpers import openai_falso, texto
from tests._metas_gate_corpus import MGR_HEDGE, TESTER3
from tests.test_ai_chat_metas_escrita import linhas
from tests.test_ai_chat_metas_escrita_ambiguo import PERGUNTA, VIAGEM, k, modelo_cria  # noqa: F401  (k é fixture)
from tests.test_wa_metas_escrita import intent
from tests.test_wa_minhas_metas import chave  # noqa: F401  (fixture)

LEGITIMAS = [("cria uma meta de 5000 pra viagem", 5000, "Viagem"), ("faz uma meta de R$ 20.000 pra casa até dezembro de 2027", 20000, "Casa"),
             ("registra uma meta chamada Carro de 30 mil", 30000, "Carro"), ("pode criar uma meta de 1500 pro notebook", 1500, "Notebook"),
             ("cria a meta Viagem de 5 mil", 5000, "Viagem")]
CONTROLE = ["cria uma meta de 5000 pra viagem, o que acha", "cria uma meta de 5000 pra viagem, deixa pra la", "cria uma meta de 5000 pra viagem, sei lá",
            "cria uma meta de 5000 pra viagem, confirma comigo", "da pra criar uma meta de 5000 pra viagem", "criar uma meta de 5000 pra viagem é fácil"]
# uma de cada grupo do Manager que gravava com a IA real (hedge no fim, cancelamento, confirmação, citação, sujeito, pergunta sem ?)
ESTRANHAS = ["cria uma meta de 5000 pra viagem?", "cria uma meta de 5000 pra viagem\napaga a meta casa"]
DO_MANAGER = [v[i] for v in MGR_HEDGE.values() for i in (0, len(v) - 1)] + ["cria uma meta de 5000 pra viagem, o que acha", "cria uma meta de 5000 pra viagem, deixa pra la",
              "cria uma meta de 5000 pra viagem, confirma comigo", "da pra criar uma meta de 5000 pra viagem", "criar uma meta de 5000 pra viagem é fácil"]
_ANTIGO = re.compile(r"^(?:(?:pode|podia|da pra|por favor) )*(?:cri(?:a|e|ar)|faz(?:er)?|registr(?:a|e|ar)|cadastr(?:a|e|ar)|abr(?:e|a|ir)) ")


def antigo(texto, args=None):  # o gate da passada 1 (regex de início + lista de bloqueio), só para o controle negativo
    n = _normalize(texto)
    return bool(_ANTIGO.match(n) and re.search(r"\b(?:metas?|reserva)\b", n) and not _OUTRO_ASSUNTO.search(n)
                and not re.search(r"\b(?:se|caso|talvez|sera|seria|nao|mas|quando|depois|amanha)\b", n) and "?" not in texto)


@pytest.mark.parametrize("frase", LEGITIMAS, ids=[f for f, _, _ in LEGITIMAS])
def test_comando_legitimo_grava_direto_pelo_runner(k, monkeypatch, frase):
    frase, alvo, nome = frase
    args = {**VIAGEM, "alvo": alvo, "nome": nome, **({"data_alvo": "2027-12-31"} if "dezembro" in frase else {})}
    assert modelo_cria(k, frase, monkeypatch, args).startswith(f'✅ Criei a meta "{nome}"') and len(linhas(k)) == 1


@pytest.mark.parametrize("frase", ESTRANHAS + DO_MANAGER)
def test_hedgeada_so_arma_a_pergunta_pelo_runner(k, monkeypatch, frase):
    assert modelo_cria(k, frase, monkeypatch) == PERGUNTA and linhas(k) == []


def test_criar_meta_sem_nome_na_frase_com_nome_inventado_pergunta(k, monkeypatch):
    assert modelo_cria(k, "criar meta de 5000", monkeypatch, {"nome": "Meta de 5000", "tipo": "meta", "alvo": 5000}) == (
        PERGUNTA.replace('"Viagem"', '"Meta de 5000"')) and linhas(k) == []


def test_valor_da_tool_diferente_do_da_frase_pergunta(k, monkeypatch):
    """O modelo pôs R$ 5.000 mas o usuário disse 3000 (valor do histórico, por ex.): não grava direto."""
    assert modelo_cria(k, "cria uma meta de 3000 pra viagem", monkeypatch) == PERGUNTA and linhas(k) == []


@pytest.mark.parametrize("frase", CONTROLE)
def test_controle_com_o_gate_antigo_a_hedgeada_grava_direto(k, monkeypatch, frase):
    monkeypatch.setattr("core.services.metas_gate.pede_criar_meta_ja", antigo)
    assert modelo_cria(k, frase, monkeypatch).startswith("✅ Criei") and len(linhas(k)) == 1  # é o buraco que o gate novo fecha


def test_controle_sem_a_regra_nem_o_inequivoco_grava(k, monkeypatch):
    monkeypatch.setattr("core.services.metas_gate.pede_criar_meta_ja", lambda t, a=None: False)
    assert modelo_cria(k, LEGITIMAS[0][0], monkeypatch) == PERGUNTA and linhas(k) == []


def _dois_turnos(k, monkeypatch, instala):
    """Turno 1: "queria uma meta..." com a falha instalada por `instala`; turno 2: "oi". Devolve (resposta 1, mensagens enviadas no 2)."""
    instala(monkeypatch)
    r1 = modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch)  # NÃO pode levantar
    vistos = []
    openai_falso(monkeypatch, lambda msgs: (vistos.extend(msgs), texto("🐷 oi"))[1])
    runner.chat(k, "oi", monthly_limit=50, platform="whatsapp")
    return r1, vistos


def _sem_tool_call_orfao(msgs):  # a OpenAI devolve 400 se um assistant(tool_calls) não for seguido de tool
    return all(m.get("role") != "assistant" or not m.get("tool_calls") or (i + 1 < len(msgs) and msgs[i + 1].get("role") == "tool")
               for i, m in enumerate(msgs))


def _ler_cai_na_1a(monkeypatch):
    real, vezes = me.mc.ler, []
    def ler(uid):
        vezes.append(1)
        if len(vezes) == 1:
            raise RuntimeError("banco caiu")
        return real(uid)
    monkeypatch.setattr(me.mc, "ler", ler)


def _http_503_na_1a(monkeypatch):
    real, vezes = me._prepara_criar, []
    def prepara(uid, args):
        vezes.append(1)
        if len(vezes) == 1:
            raise HTTPException(503, detail="indisponivel")
        return real(uid, args)
    monkeypatch.setattr(me, "_prepara_criar", prepara)


@pytest.mark.parametrize("instala", [_ler_cai_na_1a, _http_503_na_1a])
def test_falha_inesperada_na_conferencia_arma_a_pergunta_nao_grava_nem_levanta_nem_deixa_orfao(k, monkeypatch, instala):
    """Antes (`except Exception: return False`) o `execute` GRAVAVA sem "sim"; depois (relançar) o tool_call ficava sem resposta
    no histórico e todo turno seguinte levava 400 da OpenAI. Agora: pergunta, 0 linhas, histórico íntegro."""
    r1, vistos = _dois_turnos(k, monkeypatch, instala)
    assert r1 == PERGUNTA and linhas(k) == [] and vistos and _sem_tool_call_orfao(vistos)


def test_resumo_nunca_levanta_com_alvo_ilegivel():
    assert me._resumo_criar({"nome": "Viagem", "tipo": "meta", "alvo": "abc"}) == 'a meta "Viagem": juntar abc'
    assert me._resumo_criar({"nome": "Viagem", "tipo": "meta", "alvo": 5000}) == 'a meta "Viagem": juntar R$ 5.000,00'


def test_erro_de_usuario_na_conferencia_segue_para_a_frase_de_erro(k, monkeypatch):
    """Positivo do MÉDIO 2: `_Recusa` (nome repetido) continua virando a frase do erro, sem pendência."""
    modelo_cria(k, LEGITIMAS[0][0], monkeypatch)
    assert modelo_cria(k, "queria uma meta de 5000 pra viagem", monkeypatch).startswith('🐷 Você já tem a meta "Viagem"')


@pytest.mark.parametrize("frase", ["registra 100 na meta 2028", "faz um aporte de 100 na meta", "guarda 100 na meta viagem",
                                   "registra uma receita de 100 pra meta", "faz uma transferência de 100 pra meta"])
def test_aporte_deposito_e_receita_que_citam_meta_nao_sao_criar(chave, frase):
    from core.services.metas_gate import pede_criar_meta_ja
    u = usuario_pagante(plan="pro_max")
    chave(u)
    assert not pede_criar_meta_ja(frase)
    if "registra 100 na meta" not in frase:  # esta ainda vai à IA como `metas.escrita` (limite declarado: a IA não deposita)
        assert intent(frase, u)[0] != "metas.escrita", frase
    else:
        assert intent(frase, u)[0] == "metas.escrita"


@pytest.mark.parametrize("frase", TESTER3[::6])
def test_dirigida_ao_slot_do_nome_so_arma_a_pergunta_pelo_runner(k, monkeypatch, frase):
    assert modelo_cria(k, frase, monkeypatch) == PERGUNTA and linhas(k) == []


@pytest.mark.parametrize("frase", TESTER3[::6])
def test_controle_com_o_gate_antigo_a_dirigida_ao_slot_do_nome_grava_direto(k, monkeypatch, frase):
    monkeypatch.setattr("core.services.metas_gate.pede_criar_meta_ja", antigo)
    assert modelo_cria(k, frase, monkeypatch).startswith("✅ Criei") and len(linhas(k)) == 1


def test_http_com_codigo_conhecido_segue_para_a_frase_do_erro(k, monkeypatch):
    monkeypatch.setattr(me, "_prepara_criar", lambda u, a: (_ for _ in ()).throw(HTTPException(409, detail={"error": "reserva_ja_existe"})))
    token = CURRENT_USER_MESSAGE.set("queria uma reserva")
    try:
        assert me._confirmar_criar(k, VIAGEM) is False  # o `execute` responde a frase
    finally:
        CURRENT_USER_MESSAGE.reset(token)


def test_n_com_create_meta_imitado_e_mensagem_de_desejo_nao_arma_pendencia(chave, monkeypatch):
    """M10: sem o gate da chave no `confirmar_se`, N armaria "Só confirmando" de uma tool que não existe para ele."""
    n, kk = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(kk)
    token = CURRENT_USER_MESSAGE.set("queria uma meta de 5000 pra viagem")
    try:
        assert me._confirmar_criar(n, VIAGEM) is False and me._confirmar_criar(kk, VIAGEM) is True
    finally:
        CURRENT_USER_MESSAGE.reset(token)


@pytest.mark.parametrize("frase", ["cria uma meta de 2000 por mes pro presente", "cria uma meta de 5000 todo mes pra viagem", "apaga a meta Viagem, era mensal"])
def test_marcador_de_recorrencia_nao_tira_a_escrita_de_meta_de_k_e_n_fica_como_na_main(chave, frase):
    """BAIXO 3a: `vai_ao_tier3_por_marcador` vinha antes da regra e a frase ia ao tier 3 (caixinha). Só K com a chave."""
    kk, n = usuario_pagante(plan="pro_max"), usuario_pagante(plan="pro_max")
    chave(kk)
    assert intent(frase, kk)[0] == "metas.escrita" and intent(frase, n) == intent(frase, None) and intent(frase, n)[0] != "metas.escrita"
