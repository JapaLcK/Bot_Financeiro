"""Consumo de pendência é compare-and-swap, não "apaga o que estiver lá".

`pending_actions` tem UMA linha por usuário. Quem consome lê a linha, faz o
trabalho e apagava com `clear_pending_action(uid)` — incondicional. Se outra
tarefa (Discord, ou a outra plataforma do mesmo usuário) armasse uma pergunta
nova nesse meio-tempo, ela era apagada por cima: o usuário ficava com uma
pergunta na tela cuja resposta já não resolvia nada.

Os testes abaixo montam a corrida de forma determinística — a outra tarefa
grava a pendência dela DEPOIS da leitura e ANTES do consumo — e cobram as duas
metades:

- **porteiro** (`test_delete_launch_perdendo_*`, `test_confirm_media_*`): quem
  perde o CAS não executa a ação destrutiva/de dinheiro E não apaga a pergunta
  nova;
- **controle positivo** (`test_delete_launch_sem_corrida_*`): sem corrida, o
  consumo legítimo continua apagando e a ação acontece. Sem ele o grupo passaria
  num código que recusa tudo.

Controle negativo (medido): trocando o corpo de `db.consume_pending_action` por
`clear_pending_action(user_id); return True` — o comportamento antigo — os três
testes de corrida falham e o controle positivo continua verde.
"""
import db
from core.handlers import pending as h_pending


def _corrida_apos_leitura(monkeypatch, uid: int, tipo: str, payload: dict):
    """A OUTRA tarefa grava a pendência dela logo depois da primeira leitura.

    É o interleaving exato do defeito: `resolve_delete` já leu a linha antiga e
    ainda não consumiu. Dispara uma vez só — o consumo relê nada, mas os ramos
    de erro podem ler de novo.
    """
    original = db.get_pending_action
    disparou: list[int] = []

    def espiao(user_id: int):
        row = original(user_id)
        if row is not None and not disparou:
            disparou.append(1)
            db.set_pending_action(uid, tipo, payload)
        return row

    monkeypatch.setattr(db, "get_pending_action", espiao)
    monkeypatch.setattr(h_pending.db, "get_pending_action", espiao, raising=False)
    return disparou


def _um_lancamento(user_id: int) -> int:
    db.add_launch_and_update_balance(user_id, "despesa", 50.0, "mercado", "mercado")
    return int(db.list_launches(user_id, limit=1)[0]["id"])


# ── porteiro ────────────────────────────────────────────────────────────────

def test_delete_launch_perdendo_cas_nao_apaga_nem_atropela_pergunta_nova(user_id, monkeypatch):
    lid = _um_lancamento(user_id)
    db.set_pending_action(user_id, "delete_launch", {"launch_id": lid, "display_id": 1})
    _corrida_apos_leitura(monkeypatch, user_id, "bill_amount_expected",
                          {"bill_id": 4242, "bill_name": "luz"})

    resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert resp is None, "quem perde o CAS sai como se não houvesse pendência"
    assert len(db.list_launches(user_id, limit=10)) == 1, "o lançamento não podia ser apagado"
    nova = db.get_pending_action(user_id)
    assert nova is not None, "a pergunta da outra tarefa foi apagada por cima"
    assert nova["action_type"] == "bill_amount_expected"
    assert (nova.get("payload") or {}).get("bill_id") == 4242


def test_delete_launch_bulk_perdendo_cas_nao_apaga_nada(user_id, monkeypatch):
    lid = _um_lancamento(user_id)
    db.set_pending_action(user_id, "delete_launch_bulk",
                          {"launch_ids": [lid], "display_ids": {}})
    _corrida_apos_leitura(monkeypatch, user_id, "bill_amount_expected", {"bill_id": 7})

    resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert resp is None
    assert len(db.list_launches(user_id, limit=10)) == 1
    assert db.get_pending_action(user_id)["action_type"] == "bill_amount_expected"


def test_confirm_media_launch_perdendo_cas_nao_registra_dinheiro(user_id, monkeypatch):
    db.set_pending_action(user_id, "confirm_media_launch", {
        "valor": 99.9, "categoria": "mercado", "tipo": "despesa",
        "alvo": "mercado", "platform": "whatsapp",
    })
    _corrida_apos_leitura(monkeypatch, user_id, "bill_amount_expected", {"bill_id": 8})

    resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert resp is None
    assert db.list_launches(user_id, limit=10) == [], "registrou lançamento com o CAS perdido"
    assert db.get_pending_action(user_id)["action_type"] == "bill_amount_expected"


# ── controle positivo: sem corrida, o consumo legítimo continua limpando ────

def test_delete_launch_sem_corrida_apaga_o_lancamento_e_a_pendencia(user_id):
    lid = _um_lancamento(user_id)
    db.set_pending_action(user_id, "delete_launch", {"launch_id": lid, "display_id": 1})

    resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert resp is not None and "apagado" in resp
    assert db.list_launches(user_id, limit=10) == []
    assert db.get_pending_action(user_id) is None


def test_cancelar_sem_corrida_limpa_a_pendencia(user_id):
    db.set_pending_action(user_id, "delete_launch", {"launch_id": 1, "display_id": 1})

    assert h_pending.resolve_delete(user_id, confirmed=False) == "❌ Ação cancelada."
    assert db.get_pending_action(user_id) is None


# ── a própria leitura: linha vencida ────────────────────────────────────────

def test_leitura_de_pendencia_vencida_nao_apaga_a_nova(user_id, monkeypatch):
    """`get_pending_action` apagava a linha vencida sem condição.

    Duas leituras simultâneas se atropelavam: a segunda armava a pergunta nova
    na mesma linha e a primeira a apagava logo em seguida.
    """
    import db.pending as pend

    db.set_pending_action(user_id, "delete_launch", {"launch_id": 1}, minutes=-1)

    original = pend.get_conn
    estado = {"n": 0, "dentro": False}

    def get_conn_espiao():
        # 1ª conexão = o SELECT; 2ª = a limpeza da linha vencida. A outra tarefa
        # grava a pergunta dela exatamente entre as duas.
        if not estado["dentro"]:
            estado["n"] += 1
            if estado["n"] == 2:
                estado["dentro"] = True
                db.set_pending_action(user_id, "bill_amount_expected", {"bill_id": 5})
                estado["dentro"] = False
        return original()

    monkeypatch.setattr(pend, "get_conn", get_conn_espiao)
    assert db.get_pending_action(user_id) is None, "a vencida não vale como resposta"
    monkeypatch.setattr(pend, "get_conn", original)

    nova = db.get_pending_action(user_id)
    assert nova is not None, "a limpeza da vencida apagou a pendência nova"
    assert nova["action_type"] == "bill_amount_expected"


# ── ABA: pendência recriada com conteúdo idêntico ───────────────────────────

def test_consumo_nao_apaga_pendencia_identica_recriada(user_id):
    """`(action_type, payload)` identifica o CONTEÚDO, não a instância da linha.

    Outra tarefa consome e executa; o usuário repete o MESMO comando e nasce uma
    linha nova de conteúdo idêntico. Sem o `created_at` no predicado, o worker
    atrasado consome ESSA e executa a ação de novo — o dobro do que o CAS existe
    para impedir. Apontado pelo Codex no PR #141.
    """
    db.set_pending_action(user_id, "funding_source_choice", {"amount": 500.0})
    lida = db.get_pending_action(user_id)

    assert db.consume_pending_action(user_id, lida) is True

    # o usuário repete o comando: pendência nova, payload idêntico
    db.set_pending_action(user_id, "funding_source_choice", {"amount": 500.0})
    nova = db.get_pending_action(user_id)
    assert nova["created_at"] != lida["created_at"], "resolução de relógio insuficiente"

    # o worker atrasado volta com a linha VELHA em mãos
    assert db.consume_pending_action(user_id, lida) is False
    ainda = db.get_pending_action(user_id)
    assert ainda is not None, "consumiu a instância nova achando que era a velha"
    assert ainda["created_at"] == nova["created_at"]
