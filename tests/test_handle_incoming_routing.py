"""
Integração: comandos determinísticos têm precedência sobre a IA.

Pra um user Pro, "saldo"/"meus lancamentos"/"apagar CCnn" devem rodar pelo
route() tradicional (sem chamar a IA). A IA só entra com prefix explícito
("piggy ...") ou no fallback de baixa confiança de handle_incoming.

Regressão do bug onde handle_ai_chat_command mandava TODA msg de Pro pra IA,
engolindo os comandos determinísticos.
"""
from __future__ import annotations

import pytest

import db
import parsers
from core.intent_classifier import classify
from core.types import IncomingMessage
import core.handle_incoming as hi


@pytest.fixture
def spy_ai(monkeypatch):
    """Espiona core.services.ai_chat.chat — registra se a IA foi chamada."""
    calls: list[str] = []
    import core.services.ai_chat as ai_chat_mod

    def fake_chat(user_id, text, *, monthly_limit, platform):
        calls.append(text)
        return f"[IA] {text}"

    monkeypatch.setattr(ai_chat_mod, "chat", fake_chat)
    return calls


@pytest.fixture
def pro_small_uid():
    """User Pro com id < 2bi. handle_incoming só re-normaliza ids > 2bi (via
    _internal_user_id), então um id pequeno é estável — espelha os ids
    canônicos internos reais (ex: user prod 88648360). Necessário pra is_pro
    enxergar o plano dentro de handle_incoming."""
    import uuid as _uuid
    import db as _db
    from db.connection import get_conn

    uid = int(_uuid.uuid4().int % 1_000_000_000)  # < 1bi, nunca normalizado
    _db.ensure_user(uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts(user_id, email, password_hash, plan) "
                "values (%s, %s, 'x', 'pro')",
                (uid, f"pro-{uid}@test.local"),
            )
        conn.commit()
    return uid  # _auto_cleanup_orphan_users (conftest) limpa depois


@pytest.fixture
def free_small_uid():
    import uuid as _uuid
    import db as _db
    uid = int(_uuid.uuid4().int % 1_000_000_000)
    _db.ensure_user(uid)
    return uid


def _msg(uid: int, text: str) -> IncomingMessage:
    return IncomingMessage(
        platform="whatsapp", user_id=uid, text=text,
        message_id="1", attachments=[], external_id="", raw={},
    )


def test_pro_saldo_vai_pro_tradicional_sem_ia(spy_ai, pro_small_uid):
    out = hi.handle_incoming(_msg(pro_small_uid, "saldo"))
    assert spy_ai == []
    assert "Conta Corrente" in out[0].text


def test_pro_listar_vai_pro_tradicional_sem_ia(spy_ai, pro_small_uid):
    out = hi.handle_incoming(_msg(pro_small_uid, "meus lancamentos"))
    assert spy_ai == []
    assert "lançament" in out[0].text.lower()


def test_pro_com_prefix_piggy_vai_pra_ia(spy_ai, pro_small_uid):
    out = hi.handle_incoming(_msg(pro_small_uid, "piggy como economizo?"))
    assert spy_ai == ["como economizo?"]
    assert "[IA]" in out[0].text


def test_free_saldo_vai_pro_tradicional(spy_ai, free_small_uid):
    out = hi.handle_incoming(_msg(free_small_uid, "saldo"))
    assert spy_ai == []
    assert "Conta Corrente" in out[0].text


# ---------------------------------------------------------------------------
# Pergunta comparativa com verbo de lançamento → IA, nunca launches.add.
# "gastei mais esse mês que no passado?" perguntava o valor (e "50" em seguida
# virava despesa); "gastei mais em 2025 ou 2026?" gravava R$ 2.025.
# ---------------------------------------------------------------------------
_COMPARATIVAS = [
    "gastei mais esse mês que no passado?", "gastei mais esse mês?",
    "gastei muito esse mês?", "gastei menos esse mês?", "gastei demais?",
    "gastei mais que mês passado", "gastei menos que semana passada",
    "gastei mais em mercado esse mês que no mês passado?",
    "gastei mais do que ganhei?", "gastei mais ou menos que abril?",
    "gastei mais em abril ou maio?", "gastei mais em janeiro ou em fevereiro",
    "recebi mais esse mês que no passado?", "ganhei mais esse ano que no anterior?",
    "recebi menos do que em março?", "paguei mais caro esse mês?",
    "paguei mais de luz que o mês anterior?", "comprei mais que o normal?",
    "gastando mais que o normal?", "entrou mais que o normal?", "caiu menos esse mês?",
    "mandei mais pro meu irmão esse mês que no passado?",
    "gastei mais um pouco esse mês?", "gastei mais uma vez que o normal?",
    "gastei muito no ifood?", "gastei demais esse mes né?",
    "gastei mais em 2025 ou 2026?", "gastei mais em 2025 ou 2026",
    "gastei mais que 5 mil esse mês?", "gastei mais esse mês que nos últimos 3 meses?",
    "gastei mais de 100 no mercado?", "gastei mais no cartão esse mês que no passado?",
    "gastei mais no credito que no debito?",
    "gastei mais que 5 mil esse mês", "gastei mais que devia no ifood 80",
]

# Controle positivo: o desvio restringe, então lançamento legítimo com
# mais/menos/muito/demais continua launches.add com o MESMO valor.
_LANCAMENTOS = [
    ("gastei 50 no mercado", 50), ("gastei no mercado", None),
    ("gastei mais 30 no uber", 30), ("gastei 20 a mais no posto", 20),
    ("gastei 40 no almoço mais 15 no café", 40),
    ("gastei 500 no ifood e mais 800 no mercado", 500),
    ("paguei menos 10", 10), ("paguei 132 50?", 13250),
    ("gastei mais 30 que o combinado no uber", 30),
    ("gastei mais ou menos 50 no mercado", 50),
    ("gastei mais ou menos cinquenta no mercado", 50),
    ("gastei mais trinta no uber", 30), ("gastei mais dez no uber", 10),
    ("gastei mais 30?", 30), ("recebi mais 200 do freela", 200),
    ("recebi mais 1.500 esse mes", 1500), ("paguei mais R$ 40 de taxa", 40),
    ("gastei mais r$30 no uber", 30), ("gastei mais uma vez 30 no uber", 30),
    ("gastei muito 50 no mercado", 50), ("gastei 30 mais que o normal no mercado", 30),
    ("gastei muito hoje no mercado, uns 200", 200),
    ("gastei demais no rolê, 150 no bar", 150),
    ("paguei muito caro na gasolina 250", 250),
    ("comprei um tênis de 300 mais barato que o normal", 300),
    ("gastei mais de 100 no mercado", 100),
    ("caiu mais ou menos 3 mil de salário", 3000), ("paguei a luz", None),
    ("recebi 1500 de salário", 1500), ("paguei 132,50 na luz?", 132.5),
    # "mais ou menos (uns)" é aproximação, e "que" longe do comparativo é relativo.
    ("gastei mais ou menos uns 50 no mercado", 50),
    ("gastei mais ou menos umas 40 pila no bar", 40),
    ("gastei mais ou menos uns cinquenta no mercado", 50),
    ("gastei mais ou menos no mercado, uns 50", 50), ("paguei mais ou menos no uber 30", 30),
    ("gastei mais uns 30 no uber?", 30), ("gastei mais umas 20 pila no bar", 20),
    ("recebi mais uns 200 do freela", 200),
    ("caiu mais um pix de 50 que o joao mandou", 50),
    ("mandei mais um pix de 50 pro joao que tava devendo", 50),
    ("gastei muito no bar que fui ontem 80", 80),
    ("recebi mais uma parcela de 800 do seguro que eu tinha pedido", 800),
    ("gastei mais uns 30 no uber que eu tinha esquecido", 30),
    ("gastei mais de 100 no mercado que abriu ali", 100),
    ("gastei mais uma vez no uber 30 que o motorista cobrou errado", 30),
    ("gastei demais no fds que passou 100 no bar e 50 no uber", 100),
]


@pytest.mark.parametrize("text", _COMPARATIVAS)
def test_comparativa_sai_out_of_scope(text):
    r = classify(text, allow_ai=False)
    assert (r.intent, r.confidence) == ("out_of_scope", 0.4)


@pytest.mark.parametrize("text,valor", _LANCAMENTOS)
def test_lancamento_com_mais_menos_continua_launches_add(text, valor):
    assert classify(text, allow_ai=False).intent == "launches.add"
    assert parsers._extract_valor(text) == valor


@pytest.mark.parametrize("text", [
    "gastei mais esse mês que no passado?", "gastei mais que mês passado",
    "gastei mais em janeiro ou em fevereiro", "gastei mais do que ganhei?",
])
def test_describe_valueless_veta_comparativa(text):
    # Pedaço de multi-lançamento: a pergunta não pode virar "Quanto foi no ...?".
    assert parsers.describe_valueless_launch(text) is None


@pytest.mark.parametrize("text,desc", [
    ("gastei no mercado", "mercado"),
    ("paguei mais a agua que venceu", "mais a agua que venceu"),
    ("paguei mais a luz que tava atrasada", "mais a luz que tava atrasada"),
    ("gastei mais no uber que eu esqueci o valor", "mais no uber que eu esqueci o valor"),
    ("gastei muito no bar que fui ontem", "muito no bar que fui ontem"),
])
def test_describe_valueless_continua_pedindo_valor(text, desc):
    assert parsers.describe_valueless_launch(text) == ("despesa", desc)


@pytest.mark.parametrize("text,esperado", [
    ("gastei mais nos ultimos 3 meses?", False),   # criava o cartão com vencimento dia 3
    ("gastei mais que 10 dias antes?", False),     # definia aviso de 10 dias
    ("10", True), ("dia 10", True), ("5 dias antes", True),
])
def test_so_numero_recusa_comparativa(text, esperado):
    from core.handlers.credit import _so_numero
    assert _so_numero(text) is esperado


def _valores(uid):
    return sorted(float(r["valor"]) for r in db.list_launches(uid, limit=5))


def test_free_comparativa_nao_pergunta_valor_nem_grava(free_small_uid):
    uid = free_small_uid
    hi.handle_incoming(_msg(uid, "gastei mais esse mês que no passado?"))
    assert db.get_pending_action(uid) is None
    hi.handle_incoming(_msg(uid, "50"))
    assert db.list_launches(uid, limit=5) == []


def test_free_comparativa_com_ano_nao_grava(free_small_uid):
    uid = free_small_uid
    saldo = db.get_balance(uid)
    hi.handle_incoming(_msg(uid, "gastei mais em 2025 ou 2026?"))
    assert db.list_launches(uid, limit=5) == []
    assert db.get_balance(uid) == saldo


def test_pro_comparativa_vai_pra_ia(spy_ai, pro_small_uid):
    uid = pro_small_uid
    texto = "gastei mais esse mês que no passado?"
    hi.handle_incoming(_msg(uid, texto))
    assert spy_ai == [texto]
    assert db.get_pending_action(uid) is None
    hi.handle_incoming(_msg(uid, "50"))
    assert db.list_launches(uid, limit=5) == []


def test_lancamentos_com_mais_continuam_gravando(free_small_uid):
    uid = free_small_uid
    hi.handle_incoming(_msg(uid, "gastei mais 30 no uber"))
    assert _valores(uid) == [30]
    out = hi.handle_incoming(_msg(uid, "gastei no mercado"))
    assert "Quanto foi no *mercado*?" in out[0].text
    hi.handle_incoming(_msg(uid, "50"))
    assert _valores(uid) == [30, 50]
    assert "mercado" in db.list_launches(uid, limit=1)[0]["alvo"].lower()
    hi.handle_incoming(_msg(uid, "gastei mais ou menos 50 no mercado"))
    assert _valores(uid) == [30, 50, 50]


def test_lancamento_seguido_de_comparativa_fica_um(free_small_uid):
    uid = free_small_uid
    hi.handle_incoming(_msg(uid, "gastei 50 no mercado"))
    hi.handle_incoming(_msg(uid, "gastei mais em 2025 ou 2026?"))  # gravava R$ 2.025
    assert _valores(uid) == [50]


def test_comparativa_com_pendencia_viva_nao_perde_o_valor(free_small_uid):
    # Guarda, não discrimina: com uma clarification de pé a IA não é chamada
    # e o route() resolve a pendência — igual antes e depois do desvio.
    uid = free_small_uid
    hi.handle_incoming(_msg(uid, "gastei no mercado"))
    hi.handle_incoming(_msg(uid, "gastei mais esse mês que no passado?"))
    hi.handle_incoming(_msg(uid, "50"))
    assert _valores(uid) == [50]
    assert "mercado" in db.list_launches(uid, limit=1)[0]["alvo"].lower()


class _Audio:
    data = b"x"
    filename = "audio.ogg"
    content_type = "audio/ogg"


def test_audio_comparativa_com_ano_nao_grava(pro_small_uid, monkeypatch):
    monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: "gastei mais em 2025 ou 2026")
    msg = IncomingMessage(platform="whatsapp", user_id=pro_small_uid, text="",
                          message_id="m", attachments=[_Audio()], external_id="e", raw={})
    out = hi._handle_audio(msg, "whatsapp")
    assert "Entendi" in "\n".join(o.text for o in out)  # passou do portão de áudio
    assert db.list_launches(pro_small_uid, limit=5) == []


def test_multi_lancamento_ignora_pedaco_comparativo(free_small_uid):
    uid = free_small_uid
    hi.handle_incoming(_msg(uid, "gastei 50 no mercado e gastei mais que no mês passado?"))
    assert _valores(uid) == [50]
    p = db.get_pending_action(uid)
    assert p is None or p["action_type"] != "multi_launch_values"


def test_multi_lancamento_pula_comparativa_com_numero(free_small_uid):
    uid = free_small_uid
    o1 = hi.handle_incoming(_msg(uid, "gastei 50 no mercado e gastei mais em 2025 ou 2026?"))
    o2 = hi.handle_incoming(_msg(uid, "gastei 30 no uber e gastei mais que 5 mil esse mes?"))
    assert _valores(uid) == [30, 50]
    assert 'Não registrei "gastei mais em 2025 ou 2026?"' in o1[0].text
    assert 'Não registrei "gastei mais que 5 mil esse mes?"' in o2[0].text


def test_multi_lancamento_com_que_relativo_pede_o_valor(free_small_uid):
    uid = free_small_uid
    out = hi.handle_incoming(_msg(uid, "paguei 100 de luz e paguei mais a agua que venceu"))
    assert "Faltou o valor de *mais a agua que venceu*" in out[0].text
    assert db.get_pending_action(uid)["action_type"] == "multi_launch_values"
    hi.handle_incoming(_msg(uid, "80"))
    assert _valores(uid) == [80, 100]
