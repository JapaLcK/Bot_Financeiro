#!/usr/bin/env python
"""Corrida do Open Finance entre DOIS PROCESSOS DE VERDADE (Onda 3, item 4).

Por que existe
--------------
A suíte prova a corrida sequencialmente, dentro de um processo só: ela chama o
`mark_sync_result` na ordem certa e confere o resultado. O que isso NÃO prova é
que a guarda de geração funciona quando os dois runs são processos separados —
ela lê `reconnected_at` do banco, e um teste single-process pode estar certo por
compartilhar memória. Este script põe dois interpretadores de verdade, cada um
com sua conexão, contra o mesmo banco.

O que ele mede, exatamente: **a guarda de geração entre processos**. Medido com
o `pg_advisory_lock` DESLIGADO (o `pluggy_item_lock` cedendo sempre), o script
ainda sai `OK` — porque o handshake `READY`/`GO` serializa os workers e o B
solta o lock antes de o A tentar pegá-lo, então **nunca há disputa**. Quem prova
o lock é o `tests/test_of_concurrency.py`; aqui não leia isso. O controle que
importa é o outro: desligando a relectura de geração
(`_sync_pluggy_item_confirmado`), o script sai `EXIT=1` com o espelho
contaminado.

O cenário (o apontamento P2 do Codex no #162)
---------------------------------------------
    A começa … R (reconexão) … B começa … B escreve+carimba … A escreve

Worker A leu a linha ANTES da reconexão e traz um snapshot da autorização
velha. Worker B começou depois dela, terminou primeiro, espelhou e carimbou um
`last_sync_at` legítimo. Se A, chegando por último, escrever, ele sobrescreve
contas/investimentos/status/health com dado pré-reconexão — e o carimbo de B
continua lá, então a tela diz "Atualizado" sobre espelho velho.

O que ele NÃO exercita
----------------------
A reconexão aqui chama `db.save_pluggy_open_finance_item` direto, e não a rota
`/pluggy/item`, que desde o `8025a11` grava sob o `pluggy_item_lock` e devolve
503 quando o lock não vem. Neste roteiro isso não muda desfecho — a reconexão
cai enquanto o worker A ainda está na LEITURA remota, com o lock livre — mas
quer dizer que o caminho de "reconexão esperando o lock" não é este script que
prova; é o `tests/test_of_concurrency.py`. Não leia daqui um verde sobre aquilo.

Como rodar
----------
    export DATABASE_URL="postgresql://localhost:5432/pigbank_ci_test"
    PYTHONPATH=. .venv/bin/python scripts/of_corrida_dois_processos.py

Sai 0 se o run de geração velha foi recusado sem escrever; 1 caso contrário.
Aponte o `DATABASE_URL` para um banco DESCARTÁVEL: o script cria um usuário e
uma conexão, e apaga os dois no fim.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.services.pluggy_sync as ps  # noqa: E402
import db  # noqa: E402
from db.connection import get_conn  # noqa: E402
from utils_date import _tz  # noqa: E402

ITEM_SAUDAVEL = {"status": "UPDATED", "executionStatus": "SUCCESS",
                 "statusDetail": {}}

# Definidos no `__main__` a partir de um sufixo por execução, e o driver passa o
# sufixo ao filho por argv. Global fixo fazia duas cópias do script se
# atropelarem: `uq_of_conn_provider_item` é única por ITEM (não por usuário), e
# a segunda cópia morria com `UniqueViolation` crua — vermelho falso.
ITEM = ""
READY = ""
GO = ""


def _conta(account_id: str, saldo: str):
    return {"id": account_id, "name": "Conta", "type": "BANK",
            "currencyCode": "BRL", "balance": saldo}


def _tx(tx_id: str):
    return {"id": tx_id, "description": "Mercado", "amount": "-50.00",
            "date": "2026-08-19T10:00:00.000-03:00"}


def _mocka_pluggy(contas, txs, *, antes_das_txs=None):
    """Substitui o mundo remoto NO PROCESSO ATUAL (cada worker faz o seu)."""
    ps.create_pluggy_api_key = lambda: "k"
    ps.get_pluggy_item = lambda i, k=None: {**ITEM_SAUDAVEL, "id": i}
    ps.list_pluggy_accounts = lambda i, k=None: list(contas)
    ps.list_pluggy_investments = lambda i, k=None: []

    def _txs(acc, k=None, **kw):
        if antes_das_txs:
            antes_das_txs()
        return list(txs)

    ps.list_pluggy_transactions = _txs


def _espelho(connection_id: int) -> set[str]:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select provider_account_id from open_finance_accounts "
                        "where connection_id=%s", (connection_id,))
            return {r["provider_account_id"] for r in (cur.fetchall() or [])}


def _linha() -> dict:
    rows = db.get_connections_by_item_id(ITEM)
    assert len(rows) == 1, rows
    return rows[0]


# ── worker A: o run de geração VELHA ─────────────────────────────────────────

def worker_a() -> int:
    """Lê a linha (reconnected_at ainda NULL), trava no meio da leitura remota
    esperando o `GO`, e só então tenta a fase de escrita."""
    def espera_o_b():
        open(READY, "w").close()
        limite = time.monotonic() + 60
        while not os.path.exists(GO):
            if time.monotonic() > limite:
                raise TimeoutError("worker B nunca liberou o GO")
            time.sleep(0.05)

    _mocka_pluggy([_conta("acc-VELHA", "1000.00")], [_tx("tx-VELHA")],
                  antes_das_txs=espera_o_b)
    resultado = ps.sync_pluggy_item(ITEM)
    print("RESULTADO_A " + json.dumps(
        {"ok": resultado.get("ok"), "reason": resultado.get("reason")}), flush=True)
    return 0


# ── driver: prepara, roda o worker B, e confere ──────────────────────────────

def driver() -> int:
    for f in (READY, GO):
        if os.path.exists(f):
            os.remove(f)

    from db.schema import init_db
    init_db()

    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    antes = datetime.now(_tz()) - timedelta(days=8)
    try:
        conexao = db.save_pluggy_open_finance_item(
            uid, {"id": ITEM, "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
        with get_conn() as c:
            with c.cursor() as cur:
                cur.execute("update open_finance_connections "
                            "set last_sync_at=%s, reconnected_at=null where id=%s",
                            (antes, conexao["id"]))
            c.commit()
        assert _linha()["reconnected_at"] is None

        # 1. worker A sobe e para no meio da leitura remota, com a linha lida
        #    ANTES da reconexão.
        filho = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "worker-a", SUFIXO],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            # PYTHONPATH SOBRESCRITO, não estendido: o filho tem que importar a
            # árvore deste worktree. Quem for interpor código no worker A (uma
            # sabotagem, por exemplo) precisa saber que o PYTHONPATH do operador
            # é descartado aqui.
            env={**os.environ, "PYTHONPATH": os.getcwd()})
        limite = time.monotonic() + 60
        while not os.path.exists(READY):
            if filho.poll() is not None:
                out, err = filho.communicate()
                print("worker A morreu antes do READY:\n" + err[-4000:])
                return 1
            if time.monotonic() > limite:
                filho.kill()
                print("worker A não chegou ao READY em 60s")
                return 1
            time.sleep(0.05)

        # 2. o usuário reconecta…
        db.save_pluggy_open_finance_item(
            uid, {"id": ITEM, "status": "UPDATED",
                  "connector": {"id": 612, "name": "Nubank"}})
        religado = _linha()["reconnected_at"]
        assert religado is not None

        # 3. …e o worker B (ESTE processo) sincroniza inteiro, sob a autorização
        #    nova. Ele pega e solta o advisory lock antes de A tentar.
        _mocka_pluggy([_conta("acc-NOVA", "2000.00")], [_tx("tx-NOVA")])
        res_b = ps.sync_pluggy_item(ITEM)
        assert res_b.get("ok") is True, res_b
        carimbo_b = _linha()["last_sync_at"]

        # 4. libera A para a fase de escrita.
        open(GO, "w").close()
        try:
            saida, erro = filho.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            # Sem o kill, o `finally` lá embaixo apaga usuário e conexão
            # ENQUANTO o worker A ainda pode escrever — e sobra linha órfã no
            # banco de teste, num script cujo argumento de venda é não deixar lixo.
            filho.kill()
            filho.communicate()
            print("worker A não terminou em 120s")
            return 1
        if filho.returncode != 0:
            print("worker A saiu != 0:\n" + erro[-4000:])
            return 1
        linha_a = next((l for l in saida.splitlines()
                        if l.startswith("RESULTADO_A ")), None)
        res_a = json.loads(linha_a.split(" ", 1)[1]) if linha_a else {}

        depois = _linha()
        espelho = _espelho(conexao["id"])
        ui = db.get_open_finance_snapshot(uid)["connections"][0]["ui"]

        print("\n── medido ──────────────────────────────────────────────")
        print(f"  reconexão em            {religado}")
        print(f"  carimbo do worker B     {carimbo_b}")
        print(f"  resultado do worker A   {res_a}")
        print(f"  espelho no fim          {sorted(espelho)}")
        print(f"  last_sync_at no fim     {depois['last_sync_at']}")
        print(f"  estado da tela          {ui['state']} / {ui['label']}")

        falhas = []
        if espelho != {"acc-NOVA"}:
            falhas.append(f"espelho contaminado pelo snapshot velho: {sorted(espelho)}")
        if depois["last_sync_at"] != carimbo_b:
            falhas.append("o carimbo legítimo do worker B não sobreviveu")
        if res_a.get("reason") != "stale_authorization":
            falhas.append(f"worker A não foi recusado: {res_a}")
        if ui["state"] != "updated":
            falhas.append(f"tela deveria estar verde (o espelho é o novo): {ui}")

        print("────────────────────────────────────────────────────────")
        for f in falhas:
            print("  FALHOU: " + f)
        print("  OK" if not falhas else "  REPRODUZIU O DEFEITO")
        return 1 if falhas else 0
    finally:
        _apaga_usuario(uid)
        for f in (READY, GO):
            if os.path.exists(f):
                os.remove(f)


def _apaga_usuario(uid: int) -> None:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                delete from open_finance_transactions where account_id in (
                    select a.id from open_finance_accounts a
                    join open_finance_connections c on c.id = a.connection_id
                    where c.user_id=%s)""", (uid,))
            for sql in ("delete from open_finance_accounts where connection_id in "
                        "(select id from open_finance_connections where user_id=%s)",
                        "delete from open_finance_investments where connection_id in "
                        "(select id from open_finance_connections where user_id=%s)",
                        "delete from open_finance_connections where user_id=%s",
                        "delete from launches where user_id=%s",
                        "delete from users where id=%s"):
                cur.execute(sql, (uid,))
        c.commit()


if __name__ == "__main__":
    url = os.getenv("DATABASE_URL") or ""
    if not url:
        sys.exit("DATABASE_URL não está definido — aponte para um banco DESCARTÁVEL.")
    # Este script CRIA usuário e conexão e APAGA linhas no fim. Rodá-lo contra o
    # banco de produção por descuido é o tipo de acidente que não tem desfazer.
    # `urlsplit().path` e não `rsplit("/")`: o último segmento cru inclui a query
    # string, e `postgresql://…/pigbank_prod?application_name=test` passava a
    # guarda. Medido. Fronteiras de palavra pelos separadores, senão `latest`
    # passa por conter "test".
    banco = urllib.parse.urlsplit(url).path.lstrip("/")
    partes = set(re.split(r"[^a-z0-9]+", banco.lower()))
    if "test" not in partes and os.getenv("OF_CORRIDA_FORCE") != "1":
        sys.exit(f"recusando: {banco!r} não parece banco de teste. "
                 "Aponte para um descartável, ou OF_CORRIDA_FORCE=1 se souber o que faz.")
    ehWorker = sys.argv[1:2] == ["worker-a"]
    SUFIXO = sys.argv[2] if ehWorker else uuid.uuid4().hex[:8]
    ITEM = f"item-corrida-2p-{SUFIXO}"
    READY = f"/tmp/of_corrida_2p.{SUFIXO}.ready"
    GO = f"/tmp/of_corrida_2p.{SUFIXO}.go"
    sys.exit(worker_a() if ehWorker else driver())
