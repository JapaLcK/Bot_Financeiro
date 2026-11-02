"""Molde dos testes do PR-B (ADR 0006): "banco" grava a declaração, atrás da flag `DECLARACAO_BANCO`.

Ana tem o Nubank conectado (1 conta BANK/BRL) e Carteira de R$ 200,00. Toda mensagem entra pelo `handle_incoming`
(`manda_wamid`: wamid ÚNICO por mensagem; o `manda()` fixa "1" e esconderia a colisão da chave de idempotência).
`PORTAS` é a tabela porta x flag do plano (seção 3.2): cada porta que "banco" destrava, com o número de declarações
que ela cria com a flag ligada (0 = mensagem com dois gastos, que a declaração recusa com T16: E1 da #1310). Os mesmos passos rodam desligados (`test_declaracao_b_flag_desligada`) e ligados
(`test_declaracao_b_conversa`): a diferença entre os dois é a flag, não o roteiro.
"""
from __future__ import annotations

import pytest

import db
from tests._fusao_of_helpers import ia_fora, manda_wamid, uid_pro  # noqa: F401 (fixtures)
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank


@pytest.fixture
def ana(uid_pro, ia_fora):  # noqa: F811
    _bank(uid_pro)
    q("update accounts set balance=200 where user_id=%s returning 1", (uid_pro,))
    return uid_pro


@pytest.fixture
def liga(monkeypatch):
    monkeypatch.setenv("DECLARACAO_BANCO", "1")


def carteira(uid) -> float:
    return float(q("select balance from accounts where user_id=%s", (uid,))["balance"])


def declaracoes(uid) -> list[dict]:
    return q("""select l.id, l.tipo, l.valor, l.alvo, l.categoria, l.external_id, l.criado_em, d.matched_transaction_id
                  from bank_movement_declarations d join launches l on l.id = d.launch_id
                 where d.user_id=%s order by l.id""", (uid,), fetch=True)


def n_lancamentos(uid) -> int:
    """Linhas manuais do usuário (declaração e Carteira), sem as sombras do banco."""
    return q("select count(*) n from launches where user_id=%s and coalesce(source,'manual') <> 'open_finance'",
             (uid,))["n"]


def pendencia(uid):
    p = db.get_pending_action(uid)
    return p["action_type"] if p else None


def sem_pergunta(uid) -> bool:
    """Nenhuma pergunta de pé. Os botões one-shot "categoria errada?" e "desfazer" do áudio ficam, como na Carteira."""
    return pendencia(uid) in (None, "recategorize_launch_offer", "undo_audio")


class _Audio:
    data, filename, content_type = b"x", "audio.ogg", "audio/ogg"


def fala(monkeypatch, uid, frase, wamid=None) -> list[str]:
    """Um áudio cuja transcrição é `frase` (o Whisper é trocado; o resto é o caminho de produção)."""
    import uuid
    import core.handle_incoming as hi
    from core.types import IncomingMessage
    monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: frase)
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text="", message_id=wamid or "wamid.test-" + uuid.uuid4().hex,
        attachments=[_Audio()], external_id=str(uid), raw={}))
    return [o.text for o in out]


def _imagem(uid, m):
    """O recibo lido por OCR (a prévia que `_handle_image` salva) e o "sim"."""
    db.set_pending_action(uid, "confirm_media_launch", {
        "tipo": "despesa", "valor": 50.0, "alvo": "mercado", "categoria": "mercado", "data": None,
        "platform": "whatsapp", "text": "gastei 50,00 mercado"})
    return manda_wamid(uid, "sim") + manda_wamid(uid, "banco")


# nome -> (roteiro(uid, monkeypatch) -> respostas, declarações que cria com a flag ligada)
PORTAS = {
    "texto_forma_dita": (lambda u, m: manda_wamid(u, "gastei 50 no mercado no pix"), 1),
    "texto_duas_frases": (lambda u, m: manda_wamid(u, "gastei 50 no mercado e 30 na padaria no pix"), 0),
    "pergunta_e_banco": (lambda u, m: manda_wamid(u, "gastei 50 no mercado") + manda_wamid(u, "banco"), 1),
    "audio_uma_frase": (lambda u, m: fala(m, u, "gastei 50 no mercado no pix"), 1),
    "audio_duas_frases_com_forma": (lambda u, m: fala(m, u, "gastei 50 no mercado no pix e 30 na padaria no pix"), 0),
    "audio_pergunta_e_banco": (
        lambda u, m: fala(m, u, "gastei 50 no mercado e 30 na padaria") + manda_wamid(u, "banco"), 0),
    "audio_pedaco_sem_valor_e_fila": (
        lambda u, m: fala(m, u, "gastei 50 no mercado no pix e paguei o aluguel") + manda_wamid(u, "1500"), 2),
    "clarification_descricao": (
        lambda u, m: manda_wamid(u, "gastei 50") + manda_wamid(u, "banco") + manda_wamid(u, "mercado"), 1),
    "clarification_valor": (
        lambda u, m: manda_wamid(u, "paguei o aluguel") + manda_wamid(u, "banco") + manda_wamid(u, "1500"), 1),
    "imagem_sim_e_banco": (_imagem, 1),
}
