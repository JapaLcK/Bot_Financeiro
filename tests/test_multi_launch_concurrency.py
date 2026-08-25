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
