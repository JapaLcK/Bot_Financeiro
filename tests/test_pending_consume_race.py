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


# ── log do bulk: causa no log, nada sensível ────────────────────────────────

def test_delete_launch_bulk_loga_a_falha_sem_vazar_str_da_excecao(user_id, monkeypatch, caplog):
    """O `failed` do bulk perdia a causa: banco caído, deadlock e bug de código
    saíam como o mesmo "⚠️ Falha: #N". Agora vai pro log — com os DOIS ids
    (interno e user_seq) e sem `str(e)`, que pode trazer valor/descrição da
    linha no texto do psycopg."""
    import logging

    lid = _um_lancamento(user_id)
    db.set_pending_action(user_id, "delete_launch_bulk",
                          {"launch_ids": [lid], "display_ids": {str(lid): 7}})

    def explode(uid, launch_id):
        raise RuntimeError("mercadinho segredo 77,50")

    monkeypatch.setattr(h_pending.db, "delete_launch_and_rollback", explode)

    with caplog.at_level(logging.WARNING, logger="core.handlers.pending"):
        resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert "⚠️ Falha: #7" in resp, resp
    assert len(db.list_launches(user_id, limit=10)) == 1, "nada podia ser apagado"

    linhas = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("delete_launch_bulk:")]
    assert len(linhas) == 1, linhas
    # Igualdade EXATA é a assertiva de não-vazamento: qualquer valor ou
    # descrição que entrasse no log quebraria aqui.
    assert linhas[0] == (
        f"delete_launch_bulk: falha user_id={user_id} launch_id={lid} "
        f"user_seq=7 causa=RuntimeError sqlstate=None"
    ), linhas[0]
    assert "segredo" not in linhas[0] and "77,50" not in linhas[0]


def test_erro_ao_apagar_nao_devolve_str_da_excecao_ao_usuario(user_id, monkeypatch, caplog):
    """Causa INESPERADA (banco caído, deadlock, bug): a mensagem não pode
    carregar o texto do psycopg, mas tem de dizer O QUE FAZER.

    Só esta forma leva "tenta de novo": ela é a única em que tentar de novo
    pode funcionar. As duas formas PERMANENTES estão nos testes abaixo, e é a
    ausência delas aqui que fazia o grupo cimentar o conselho errado pra toda
    causa."""
    import logging

    lid = _um_lancamento(user_id)
    db.set_pending_action(user_id, "delete_launch", {"launch_id": lid, "display_id": 3})

    def explode(uid, launch_id):
        raise RuntimeError('DETAIL: Key (descricao)=(mercadinho segredo) valor=77,50')

    monkeypatch.setattr(h_pending.db, "delete_launch_and_rollback", explode)

    with caplog.at_level(logging.WARNING, logger="core.handlers.pending"):
        resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert "segredo" not in resp and "DETAIL" not in resp and "77,50" not in resp, resp
    assert "#3" in resp, "o usuário precisa saber QUAL lançamento falhou"
    assert "de novo" in resp.lower(), "mensagem genérica sem ação deixa o usuário parado"
    assert any(r.getMessage().startswith("delete_launch: falha") for r in caplog.records)


# ── condição PERMANENTE ≠ erro técnico: as três formas ──────────────────────
#
# `delete_launch_and_rollback` levanta MAIS que duas exceções previstas
# (enumeradas em `db/accounts.py`): `LookupError("NOT_FOUND")`, `LaunchNoEffects`,
# `InvestmentLotHasWithdrawal`, dois `ValueError` crus de dado corrompido
# (delta_pocket/delta_invest sem nome) e três implícitas (`JSONDecodeError` —
# que É subclasse de `ValueError` —, `int()` do bill_id, `date.fromisoformat`).
# Escrever "duas" aqui é o que produziu o `except ValueError` nu, que fundia
# TODAS numa só frase ("é antigo") — inclusive a do lote com resgate, que não é
# antiga nem permanente.
#
# Só as duas nomeadas são permanentes-ou-acionáveis e ganham frase própria; o
# resto cai no ramo técnico, com log e retry, que é o desfecho certo pra elas.
#
# Controle negativo medido: trocando os `except LookupError` /
# `except db.LaunchNoEffects` / `except db.InvestmentLotHasWithdrawal` de
# `resolve_delete` por um `except Exception` único (o estado do HEAD anterior),
# os testes desta seção falham e o
# `test_erro_ao_apagar_nao_devolve_str_da_excecao_ao_usuario` (causa
# inesperada) continua verde.

def _sql(q, *args):
    from db.connection import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(q, args)
        conn.commit()


def test_lancamento_ja_apagado_por_outra_porta_nao_promete_retry(user_id):
    """NOT_FOUND real: o usuário pede pra apagar no WhatsApp, apaga pelo
    dashboard e só então responde "sim" (a pendência vive 10 min)."""
    lid = _um_lancamento(user_id)
    db.set_pending_action(user_id, "delete_launch", {"launch_id": lid, "display_id": 3})
    _sql("delete from launches where id=%s and user_id=%s", lid, user_id)  # a outra porta

    resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert "#3" in resp, resp
    assert "de novo" not in resp.lower(), f"condição permanente prometendo retry: {resp!r}"
    assert "histórico" in resp.lower(), resp


def test_lancamento_sem_efeitos_nao_promete_retry_nem_vaza_str(user_id):
    """ValueError do domínio: mensagem em PT-BR escrita PRO USUÁRIO. Também
    permanente — sem `efeitos` gravados, a reversão não fica possível com o
    tempo. A frase é a mesma do "apagar tudo" (`kept_no_effects`)."""
    lid = _um_lancamento(user_id)
    db.set_pending_action(user_id, "delete_launch", {"launch_id": lid, "display_id": 4})
    _sql("update launches set efeitos=null where id=%s and user_id=%s", lid, user_id)

    resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert "#4" in resp, resp
    assert "de novo" not in resp.lower(), f"condição permanente prometendo retry: {resp!r}"
    assert "antigo" in resp.lower(), resp
    assert len(db.list_launches(user_id, limit=10)) == 1, "sem efeitos, nada podia ser apagado"


def _aporte_com_resgate(user_id: int) -> tuple[int, int]:
    """Aporte cujo lote JÁ teve resgate parcial — a condição do `:1171`.

    Devolve (launch_id do aporte, user_seq do resgate). Sem mock: o estado é o
    mesmo que o usuário produz aportando e resgatando de verdade.
    """
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_investment_db(user_id, "CDB Lote", rate=0.01, period="monthly", nota="t")
    aporte_id, *_ = db.investment_deposit_from_account(user_id, "CDB Lote", 200, "aporte")
    resgate_id, *_ = db.investment_withdraw_to_account(user_id, "CDB Lote", 50, "resgate")
    return int(aporte_id), int(resgate_id)


def test_aporte_com_lote_resgatado_diz_o_que_destrava(user_id, caplog):
    """Condição TEMPORÁRIA com contorno: nem "tenta de novo" (o tempo não
    destrava) nem "é antigo" (o dado está inteiro). A frase tem de dizer que o
    resgate vem primeiro."""
    import logging
    aporte_id, _ = _aporte_com_resgate(user_id)
    db.set_pending_action(user_id, "delete_launch", {"launch_id": aporte_id, "display_id": 9})

    with caplog.at_level(logging.WARNING, logger="core.handlers.pending"):
        resp = h_pending.resolve_delete(user_id, confirmed=True)
    registros = [r.getMessage() for r in caplog.records]

    assert "antigo" not in resp.lower(), f"condição temporária vendida como permanente: {resp!r}"
    assert "de novo" not in resp.lower(), f"retry que nunca funciona: {resp!r}"
    assert "resgate" in resp.lower(), f"não diz o que destrava: {resp!r}"
    assert "#9" in resp, resp
    assert any(r.startswith("delete_launch_lote_com_resgate:") for r in registros), registros
    assert any(int(l["id"]) == aporte_id for l in db.list_launches(user_id, limit=20)), \
        "o aporte não podia ser apagado"


def test_apagar_o_resgate_destrava_o_aporte(user_id):
    """Controle POSITIVO: o contorno que a frase promete funciona mesmo, e a
    correção não fechou o caminho legítimo."""
    aporte_id, resgate_id = _aporte_com_resgate(user_id)

    db.delete_launch_and_rollback(user_id, resgate_id)  # o contorno
    db.set_pending_action(user_id, "delete_launch", {"launch_id": aporte_id, "display_id": 9})
    resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert "apagado" in resp.lower(), resp
    assert not any(int(l["id"]) == aporte_id for l in db.list_launches(user_id, limit=20)), \
        "o aporte devia ter sido apagado depois de sumir o resgate"


def test_caixinha_com_saldo_e_caixinha_sumida_nao_prometem_retry(user_id, monkeypatch):
    """POCKET_NOT_ZERO (ValueError) e POCKET_NOT_FOUND (LookupError): as duas
    permanentes, as duas com resposta acionável própria."""
    db.set_pending_action(user_id, "delete_pocket", {"pocket_name": "viagem"})

    def saldo(uid, nome):
        raise ValueError("POCKET_NOT_ZERO")

    monkeypatch.setattr(h_pending.db, "delete_pocket", saldo)
    resp = h_pending.resolve_delete(user_id, confirmed=True)
    assert "de novo" not in resp.lower(), resp
    assert "saldo" in resp.lower() and "POCKET_NOT_ZERO" not in resp, resp

    db.set_pending_action(user_id, "delete_pocket", {"pocket_name": "viagem"})

    def sumiu(uid, nome):
        raise LookupError("POCKET_NOT_FOUND")

    monkeypatch.setattr(h_pending.db, "delete_pocket", sumiu)
    resp = h_pending.resolve_delete(user_id, confirmed=True)
    assert "de novo" not in resp.lower(), resp
    assert "POCKET_NOT_FOUND" not in resp and "viagem" in resp, resp


def test_investimento_com_saldo_e_sumido_nao_prometem_retry(user_id, monkeypatch):
    """INV_NOT_ZERO / INV_NOT_FOUND (`db/investments.py`) — o irmão que o
    varredor esqueceu."""
    db.set_pending_action(user_id, "delete_investment", {"investment_name": "cdb"})
    monkeypatch.setattr(h_pending.db, "delete_investment",
                        lambda uid, n: (_ for _ in ()).throw(ValueError("INV_NOT_ZERO")))
    resp = h_pending.resolve_delete(user_id, confirmed=True)
    assert "de novo" not in resp.lower(), resp
    assert "saldo" in resp.lower() and "INV_NOT_ZERO" not in resp, resp

    db.set_pending_action(user_id, "delete_investment", {"investment_name": "cdb"})
    monkeypatch.setattr(h_pending.db, "delete_investment",
                        lambda uid, n: (_ for _ in ()).throw(LookupError("INV_NOT_FOUND")))
    resp = h_pending.resolve_delete(user_id, confirmed=True)
    assert "de novo" not in resp.lower(), resp
    assert "INV_NOT_FOUND" not in resp and "cdb" in resp, resp


def test_erro_inesperado_em_caixinha_e_investimento_continua_mandando_tentar(user_id, monkeypatch):
    """Controle positivo do par acima: o caminho técnico NÃO pode perder o
    "tenta de novo". Sem ele, um conserto que recusasse tudo passaria."""
    for tipo, payload, fn in (
        ("delete_pocket", {"pocket_name": "viagem"}, "delete_pocket"),
        ("delete_investment", {"investment_name": "cdb"}, "delete_investment"),
    ):
        db.set_pending_action(user_id, tipo, payload)
        monkeypatch.setattr(h_pending.db, fn,
                            lambda uid, n: (_ for _ in ()).throw(RuntimeError("boom 77,50")))
        resp = h_pending.resolve_delete(user_id, confirmed=True)
        assert "de novo" in resp.lower(), f"{tipo}: erro técnico sem ação: {resp!r}"
        assert "77,50" not in resp and "boom" not in resp, resp
