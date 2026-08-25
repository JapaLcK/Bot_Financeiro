"""
Duas respostas para a MESMA fila `multi_launch_values` não podem gravar o mesmo
item duas vezes.

O caminho é real pelo Discord: `adapters/discord/discord_bot.py:122` é um
`on_message` async sem lock, num processo separado do uvicorn. Duas mensagens
seguidas do mesmo usuário viram duas tarefas concorrentes. Antes do compare-and-swap (`db.advance_pending_action`)
as duas threads liam a mesma fila, as duas gravavam o item da FRENTE e o
segundo valor sumia sem uma palavra.

O `__main__` no fim deste arquivo é o controle negativo: roda as duas threads
20 vezes COM e SEM o CAS e imprime o placar. Ele não está na suíte de propósito
— a metade "sem CAS" é uma corrida, e corrida perdida vira teste vermelho por
motivo errado. O que a suíte guarda são os dois casos determinísticos abaixo.
"""
from __future__ import annotations

import pytest
import threading

import db
from core.handlers import launches


def _armar_fila(user_id: int) -> dict:
    """Deixa o usuário com a fila [aluguel, luz] e devolve o pending lido.

    Esse dict é o que as duas threads teriam em mãos: as duas leem a pendência
    antes de qualquer uma escrever.
    """
    launches.add(user_id, "recebi 100 de x e paguei o aluguel e paguei a luz", {})
    p = db.get_pending_action(user_id)
    assert [i["desc"] for i in p["payload"]["queue"]] == ["aluguel", "luz"]
    return p


def _registrados(user_id: int) -> set[tuple[str, float]]:
    """(alvo, valor) das despesas — a receita de armação fica de fora."""
    return {
        ((r.get("alvo") or "").strip(), float(r["valor"]))
        for r in db.list_launches(user_id, limit=20)
        if r["tipo"] == "despesa"
    }


def test_leitura_velha_nao_grava_o_mesmo_item_duas_vezes(user_id):
    """A segunda chamada usa o pending JÁ VELHO (o que a outra thread leu).

    Sem CAS ela grava "aluguel" de novo e a luz morre na fila. Com CAS ela
    perde a escrita, relê a fila encurtada e responde a luz. Determinístico:
    é o mesmo interleaving da corrida, só que serializado.
    """
    velho = _armar_fila(user_id)

    launches.resolve_multi_launch_value(user_id, "800", velho)
    launches.resolve_multi_launch_value(user_id, "200", velho)

    assert _registrados(user_id) == {("aluguel", 800.0), ("luz", 200.0)}
    p = db.get_pending_action(user_id)
    assert p is None or p["action_type"] != "multi_launch_values"


def test_duas_threads_simultaneas(user_id):
    """A corrida de verdade, com as duas threads soltas ao mesmo tempo.

    Qual thread pega qual item depende de quem vence o CAS — por isso a
    asserção é sobre o CONJUNTO: cada valor num item, nenhum item repetido.
    """
    velho = _armar_fila(user_id)

    largada = threading.Barrier(2)
    erros: list[BaseException] = []

    def responde(valor_txt: str):
        try:
            largada.wait(timeout=10)
            launches.resolve_multi_launch_value(user_id, valor_txt, velho)
        except BaseException as exc:  # noqa: BLE001 — o teste precisa ver
            erros.append(exc)

    ts = [threading.Thread(target=responde, args=(v,)) for v in ("800", "200")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)

    assert not erros, erros
    feitos = _registrados(user_id)
    assert len(feitos) == 2, feitos
    assert {alvo for alvo, _ in feitos} == {"aluguel", "luz"}, feitos
    assert {v for _, v in feitos} == {800.0, 200.0}, feitos


def test_teto_de_plano_devolve_o_item_pra_fila(user_id, monkeypatch):
    """O item sai da fila ANTES do registro — se o registro estoura, ele volta.

    `check_can_create_launch` levanta `PlanLimitExceeded` quando o usuário do
    Grátis bate o teto do mês. Sem devolver, o lançamento sumiria calado: a
    fila já tinha sido encurtada e ninguém mais perguntaria por ele.
    """
    _armar_fila(user_id)

    def estoura(*_a, **_kw):
        raise RuntimeError("teto do plano")

    monkeypatch.setattr("core.services.plan_service.check_can_create_launch", estoura)
    try:
        launches.resolve_multi_launch_value(user_id, "800", db.get_pending_action(user_id))
    except RuntimeError:
        pass
    else:
        raise AssertionError("a exceção deveria ter subido")

    p = db.get_pending_action(user_id)
    assert p["action_type"] == "multi_launch_values"
    assert [i["desc"] for i in p["payload"]["queue"]] == ["aluguel", "luz"]


# ── Controle negativo (fora da suíte) ────────────────────────────────────────
# `python3 tests/test_multi_launch_concurrency.py` → placar com e sem o CAS.
if __name__ == "__main__":
    import os
    import sys
    import uuid

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from db.connection import get_conn

    RODADAS = 20

    def _sem_cas(user_id, action_type, old_payload, new_payload, minutes=10):
        """O que a `main` fazia: escreve sem conferir o que estava lá."""
        if new_payload is None:
            db.clear_pending_action(user_id)
        else:
            db.set_pending_action(user_id, action_type, new_payload)
        return True

    def _limpa(uid):
        with get_conn() as conn:
            with conn.cursor() as cur:
                for t in ("launches", "pending_actions", "user_category_rules"):
                    cur.execute(f"delete from {t} where user_id = %s", (uid,))
                cur.execute("delete from users where id = %s", (uid,))
            conn.commit()

    def placar(com_cas: bool) -> int:
        original = db.advance_pending_action
        if not com_cas:
            db.advance_pending_action = _sem_cas  # launches.db é o mesmo módulo
        ok = 0
        try:
            for _ in range(RODADAS):
                uid = int(uuid.uuid4().int % 10_000_000_000)
                db.ensure_user(uid)
                try:
                    velho = _armar_fila(uid)
                    largada = threading.Barrier(2)

                    def responde(v):
                        try:
                            largada.wait(timeout=10)
                            launches.resolve_multi_launch_value(uid, v, velho)
                        except Exception:
                            pass

                    ts = [threading.Thread(target=responde, args=(v,))
                          for v in ("800", "200")]
                    for t in ts:
                        t.start()
                    for t in ts:
                        t.join(timeout=30)
                    feitos = _registrados(uid)
                    if ({a for a, _ in feitos} == {"aluguel", "luz"}
                            and {v for _, v in feitos} == {800.0, 200.0}):
                        ok += 1
                finally:
                    _limpa(uid)
        finally:
            db.advance_pending_action = original
        return ok

    print(f"SEM  CAS: {placar(False)}/{RODADAS} rodadas corretas")
    print(f"COM  CAS: {placar(True)}/{RODADAS} rodadas corretas")


def test_item_que_estourou_volta_pra_fila_mesmo_com_outra_thread_avancando(user_id, monkeypatch):
    """P2 do Codex, pelo caminho real: `resolve_multi_launch_value` com exceção.

    Cena: fila [aluguel, luz]. A thread A reivindica 'aluguel'; ANTES de ela
    registrar, a thread B avança a fila (registra 'luz'). Aí o registro de A
    estoura (teto de plano). O payload que A tinha em mãos não existe mais.

    Código antigo: tentava restaurar o estado velho, o CAS falhava, e 'aluguel'
    sumia. Código novo: relê e prepende na fila que existir.
    """
    pending = _armar_fila(user_id)

    def estoura_e_avanca_por_baixo(*a, **k):
        # simula a thread B tendo avançado a fila enquanto A trabalhava
        db.clear_pending_action(user_id)
        raise RuntimeError("teto de plano")

    monkeypatch.setattr(launches, "add_from_entities", estoura_e_avanca_por_baixo)

    with pytest.raises(RuntimeError):
        launches.resolve_multi_launch_value(user_id, "800", pending)

    fila = (db.get_pending_action(user_id) or {}).get("payload", {}).get("queue", [])
    assert [i["desc"] for i in fila] == ["aluguel"], (
        f"o item que estourou sumiu — fila ficou {fila}")


def test_duas_devolucoes_simultaneas_com_fila_vazia_nao_perdem_item(user_id):
    """P2 (2ª rodada do Codex): dois itens que estouram juntos, fila já vazia.

    Cena: os dois últimos itens são reivindicados por tarefas diferentes e
    AMBOS os registros estouram (ex.: os dois batem o teto de plano). As duas
    devoluções veem "não há pendência" e cada uma quer criar a sua. Com upsert
    incondicional a última apaga a primeira e um item some.

    Precisa de threads de verdade com barreira: em sequência a segunda já
    encontraria a fila criada e nunca passaria pelo ramo que tem o defeito.
    """
    db.clear_pending_action(user_id)

    largada = threading.Barrier(2)
    erros: list[BaseException] = []

    def devolve(desc: str):
        try:
            largada.wait(timeout=10)
            launches._devolve_head(user_id, {"desc": desc, "tipo": "despesa"}, "whatsapp")
        except BaseException as exc:  # noqa: BLE001 — o teste precisa ver
            erros.append(exc)

    ts = [threading.Thread(target=devolve, args=(d,)) for d in ("aluguel", "luz")]
    for th in ts:
        th.start()
    for th in ts:
        th.join(timeout=30)

    assert not erros, erros
    fila = (db.get_pending_action(user_id) or {}).get("payload", {}).get("queue", [])
    nomes = sorted(i["desc"] for i in fila)
    assert nomes == ["aluguel", "luz"], f"item perdido — fila ficou {fila}"


def test_cinco_respostas_concorrentes_nenhuma_e_descartada(user_id):
    """P2 (3ª rodada do Codex): teto fixo de 4 tentativas descartava valor.

    Com 5 respostas concorrendo, uma tarefa podia perder o CAS quatro vezes
    seguidas (uma para cada vencedora), esgotar o laço e devolver None — o
    valor que o usuário digitou sumia sem uma palavra. O `on_message` do
    Discord não limita a duas.

    Cinco itens na fila, cinco respostas soltas ao mesmo tempo: os cinco
    valores têm que virar cinco lançamentos.
    """
    launches.add(user_id, "recebi 1 de x e paguei o aluguel e paguei a luz "
                          "e paguei a agua e paguei o gas e paguei o wifi", {})
    velho = db.get_pending_action(user_id)
    assert len(velho["payload"]["queue"]) == 5, velho["payload"]["queue"]

    largada = threading.Barrier(5)
    erros: list[BaseException] = []

    def responde(valor_txt: str):
        try:
            largada.wait(timeout=15)
            launches.resolve_multi_launch_value(user_id, valor_txt, velho)
        except BaseException as exc:  # noqa: BLE001
            erros.append(exc)

    valores = ("100", "200", "300", "400", "500")
    ts = [threading.Thread(target=responde, args=(v,)) for v in valores]
    for th in ts:
        th.start()
    for th in ts:
        th.join(timeout=60)

    assert not erros, erros
    feitos = _registrados(user_id)
    assert {v for _, v in feitos} == {100.0, 200.0, 300.0, 400.0, 500.0}, (
        f"valor descartado em silêncio — gravados: {sorted(v for _, v in feitos)}")


def test_falha_depois_do_commit_nao_devolve_item_nem_duplica(user_id, monkeypatch):
    """P1 (4ª rodada do Codex): exceção APÓS o lançamento já gravado.

    `add_launch_and_update_balance` commita e só depois roda o acessório
    (aprender a regra, armar ofertas). Se o acessório estoura, quem chamou não
    distingue "não gravou" de "gravou e falhou no acessório" — e a devolução
    põe o item de volta, fazendo a próxima resposta registrar o MESMO gasto e
    dobrar o saldo.

    Depois da correção o acessório não sobe exceção: o lançamento fica, a fila
    avança, e o saldo muda uma vez só.
    """
    velho = _armar_fila(user_id)

    def estoura(*a, **k):
        raise RuntimeError("upsert da regra caiu")

    monkeypatch.setattr(launches, "learn_from_inference", estoura)

    resp = launches.resolve_multi_launch_value(user_id, "800", velho)

    feitos = [(a, v) for a, v in _registrados(user_id) if a == "aluguel"]
    assert len(feitos) == 1 and feitos[0][1] == 800.0, feitos
    assert resp, "o usuário tem que receber a confirmação, não um erro"

    fila = (db.get_pending_action(user_id) or {}).get("payload", {}).get("queue", [])
    assert [i["desc"] for i in fila] == ["luz"], (
        f"o item já gravado foi devolvido pra fila — duplicaria: {fila}")
