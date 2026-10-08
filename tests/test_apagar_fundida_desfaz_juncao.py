"""Apagar a linha fundida desfaz a junção na hora (PR 2b-2, P3 do dono).

Cenário `funde_a`: banco 1000 → 950, "gastei 50 no mercado" à mão, confirmado. Apagar o
manual por qualquer canal tem de devolver a transação do banco como sombra `banco` — o gasto
continua 50 (nem 0, nem 100) e o consolidado continua 950. Sem o desfazer, o gasto sumia: o
`on delete set null` soltava a transação sem recriar a sombra.

Controles por mutação (relato do PR): `_desfaz` desligado → vermelho nos quatro canais;
sem o `not escopo_conta_corrente` → vermelho em `test_apagar_tudo_nao_recria_a_sombra`;
sem o `ligado` no `_precisa_lock` → vermelho em `test_ligou_entre_o_preview_e_o_lock`
(até o undo comum passar a travar sempre: ver o docstring desse teste).
"""
from __future__ import annotations

import pytest

import db
import db.bank_movements as bank_mod
from conftest import usuario_pagante
from core.services.ai_chat.tools.launches import _delete_launch_execute
from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, saldo_bruto, sincroniza, soma_delta_conta, tx,
    uid_pro, ultimo_launch,
)
from tests.test_api_v2_lancamentos_escrita import post
from tests.test_api_v2_perfil import cliente
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_reconciliacao_desfazer import _of_tx, _sombras, funde_a
from tests.test_reconciliacao_resolver import _estado, _gasto_do_mes, _q, pendencia
from utils_date import today_tz

FRASE = "A transação do banco (R$ 50,00, MERCADO) continua na sua lista, porque ela aconteceu de verdade."


def _fundida(uid):
    conexao = funde_a(uid)
    x = _estado(_of_tx(uid))["imported_launch_id"]
    seq = _q("select user_seq from launches where id=%s", (x,))[0]["user_seq"]
    return conexao, x, seq


def _confere_desfeito(uid, conexao, x):
    of_tx = _of_tx(uid)
    assert not _q("select 1 from launches where id=%s", (x,)), "X ficou"
    assert _sombras(uid) == 1
    e = _estado(of_tx)
    assert e["imported_launch_id"] not in (None, x), e
    assert (e["match_launch_id"], e["reconciliation_status"]) == (None, "imported"), e
    assert _gasto_do_mes(uid) == 50.0
    assert consolidado(uid) == (950.0, 0.0)
    assert saldo_bruto(uid) == soma_delta_conta(uid)
    rep = db.import_open_finance_launches(uid, conexao)
    assert (rep["inserted"], rep["auto_merged"]) == (0, 0), rep
    assert _sombras(uid) == 1


# ── os quatro canais ─────────────────────────────────────────────────────────

def test_whatsapp_apaga_a_fundida_e_o_banco_volta(uid_pro, ia_fora):
    conexao, x, seq = _fundida(uid_pro)
    assert "sim" in manda(uid_pro, f"apagar #{seq}").lower()
    resp = manda(uid_pro, "sim")
    assert resp == f"✅ Lançamento *#{seq}* apagado. {FRASE}", resp  # o WhatsApp troca ** por *
    _confere_desfeito(uid_pro, conexao, x)


def test_whatsapp_em_lote_avisa_por_lancamento(uid_pro, ia_fora):
    conexao, x, seq = _fundida(uid_pro)
    manda(uid_pro, "gastei 7 na padaria em dinheiro")
    outro = _q("select user_seq from launches where id=%s", (ultimo_launch(uid_pro),))[0]["user_seq"]
    assert "confirma" in manda(uid_pro, f"apagar #{seq} e #{outro}").lower()
    resp = manda(uid_pro, "sim")
    assert resp.splitlines() == [f"✅ Apagados: *#{seq}*, *#{outro}*", f"*#{seq}*: {FRASE}"], resp
    _confere_desfeito(uid_pro, conexao, x)


def test_ia_apaga_a_fundida_e_o_banco_volta(uid_pro, ia_fora):
    conexao, x, seq = _fundida(uid_pro)
    assert _delete_launch_execute(uid_pro, {"launch_id": str(seq)}) == f"🗑️ Lançamento #{seq} apagado. {FRASE}"
    _confere_desfeito(uid_pro, conexao, x)


def test_app_apaga_a_fundida_e_devolve_o_aviso(uid_pro, ia_fora):
    conexao, x, _ = _fundida(uid_pro)
    c, h = cliente(uid_pro)
    r = c.delete(f"/launches/{uid_pro}/{x}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "launch_id": x, "aviso": FRASE}
    _confere_desfeito(uid_pro, conexao, x)


def test_v2_apaga_a_fundida_sem_aviso_na_resposta(uid_pro, ia_fora, libera):
    conexao, x, _ = _fundida(uid_pro)
    libera(uid_pro)
    r = post(uid_pro, "apagar", {"id": f"l{x}"})
    assert (r.status_code, r.json()) == (200, {"id": f"l{x}"}), r.text
    _confere_desfeito(uid_pro, conexao, x)


# ── onde NÃO desfaz (positivo: o resto segue como antes) ────────────────────

def test_carteira_pura_nao_tem_aviso(uid_pro, ia_fora):
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    assert db.delete_launch_and_rollback(uid_pro, ultimo_launch(uid_pro)) is None
    assert _q("select count(*) n from open_finance_transactions o join open_finance_accounts a "
              "on a.id=o.account_id join open_finance_connections c on c.id=a.connection_id "
              "where c.user_id=%s", (uid_pro,))[0]["n"] == 0
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro) == 0


def test_par_pendente_mantem_a_sombra_e_nao_avisa(uid_pro, ia_fora):
    _, of_tx, manual, sombra = pendencia(uid_pro)
    assert db.delete_launch_and_rollback(uid_pro, manual) is None
    assert _sombras(uid_pro) == 1
    assert _estado(of_tx) == {"imported_launch_id": sombra, "match_launch_id": None,
                              "reconciliation_status": "pending"}
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro)


def test_match_em_outro_lancamento_apaga_sem_desfazer(uid_pro, ia_fora):
    _, x, _ = _fundida(uid_pro)
    db.add_launch_and_update_balance(uid_pro, "despesa", 5, "outro", None)
    y = ultimo_launch(uid_pro)
    _q("update open_finance_transactions set match_launch_id=%s where id=%s returning id", (y, _of_tx(uid_pro)))
    assert db.delete_launch_and_rollback(uid_pro, x) is None
    assert _sombras(uid_pro) == 0
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro)


def test_conexao_pausada_recria_a_sombra_como_o_undo(uid_pro, ia_fora):
    conexao, x, _ = _fundida(uid_pro)
    _q("update open_finance_connections set status='PAUSED' where id=%s returning id", (conexao,))
    assert db.delete_launch_and_rollback(uid_pro, x) == FRASE
    assert _sombras(uid_pro) == 1
    assert _estado(_of_tx(uid_pro))["reconciliation_status"] == "imported"
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro)


def test_fusao_automatica_desfaz_igual(uid_pro, ia_fora):
    """`auto_merged` (OFX, recorrente, o import): mesma regra da confirmada."""
    conexao, x, _ = _fundida(uid_pro)
    _q("update open_finance_transactions set reconciliation_status='auto_merged' where id=%s returning id",
       (_of_tx(uid_pro),))
    assert db.delete_launch_and_rollback(uid_pro, x) == FRASE
    _confere_desfeito(uid_pro, conexao, x)


def test_apagar_tudo_nao_recria_a_sombra(uid_pro, ia_fora):
    _fundida(uid_pro)
    r = db.delete_all_launches_and_rollback(uid_pro)
    assert (r["deleted"], r["remaining"], r["kept_unsafe"], r["errors"]) == (1, 0, [], []), r
    assert _sombras(uid_pro) == 0


# ── concorrência sequencial e isolamento ─────────────────────────────────────

def test_ligou_entre_o_preview_e_o_lock(uid_pro, ia_fora, monkeypatch):
    """O import começa no meio do apagar e quer ligar X (`match` = X). Todo apagar comum já
    toma `_lock_user` antes da linha, então o import ESPERA o mutex: o apagar conclui sem
    recusa (antes: o recheck pós-lock via a ligação e recusava com `mudou_durante`, porque o
    apagar de carteira pura não tinha o mutex) e o import que sobra não acha X."""
    conexao = conecta_banco(uid_pro, "1000.00")
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    x = ultimo_launch(uid_pro)
    sincroniza(conexao, uid_pro, "950.00", [tx(uid_pro, "-50.00", today_tz(), "MERCADO")])
    antes = saldo_bruto(uid_pro)
    real = bank_mod.uses_bank_movement_lock
    rodou = []

    import threading
    from tests._espera_lock import _esperar_backend_travado
    saida = {}

    def importa_no_meio(*a, **kw):
        if not rodou:
            rodou.append(1)
            fio = threading.Thread(
                target=lambda: saida.__setitem__("import", db.import_open_finance_launches(uid_pro, conexao)))
            fio.start()
            saida["fio"] = fio
            assert _esperar_backend_travado(), "o import não ficou esperando o mutex"
        return real(*a, **kw)

    monkeypatch.setattr(bank_mod, "uses_bank_movement_lock", importa_no_meio)
    db.delete_launch_and_rollback(uid_pro, x)  # conclui sem recusa
    saida["fio"].join(30)
    assert not saida["fio"].is_alive() and rodou
    assert not _q("select 1 from launches where id=%s", (x,))
    assert _estado(_of_tx(uid_pro))["match_launch_id"] is None
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro)


def test_outro_usuario_nao_apaga_a_fundida(uid_pro, ia_fora):
    _, x, _ = _fundida(uid_pro)
    of_tx = _of_tx(uid_pro)
    antes = (_estado(of_tx), _sombras(uid_pro), consolidado(uid_pro), _gasto_do_mes(uid_pro))
    with pytest.raises(LookupError):
        db.delete_launch_and_rollback(usuario_pagante(), x)
    assert (_estado(of_tx), _sombras(uid_pro), consolidado(uid_pro), _gasto_do_mes(uid_pro)) == antes
