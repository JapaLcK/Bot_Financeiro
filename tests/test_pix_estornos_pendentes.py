"""`scripts/pix_estornos_pendentes.py` (#1437): o passo do deploy que grava o marcador que a versão
anterior não gravava e drena os estornos abertos. Banco real; o Asaas é falso.

"Recebido pela versão anterior" = `registrar_evento` sem drenar e o marcador apagado: é o estado que
o código de antes do #1437 deixava.

CONTROLE NEGATIVO: com o `--aplicar` sem gravar (`gravar` trocado por no-op), `test_aplicar_*` fica
vermelho (o `RECEIVED` velho concede). POSITIVO: a simulação não grava nada.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

import core.services.asaas as asaas
import core.services.pix_drain as dreno
from _dreno_pix_helpers import entregar, evento, nova_cobranca
from _billing_grants_helpers import conta
from core.services.pix_drain_effects import EXECUTORES
from db.connection import get_conn
from test_afiliado_area import _afiliado
from test_afiliado_pagantes import _indicado, _pix
from test_pix_dreno_estorno import _grants_ativos, externo  # noqa: F401
from test_pix_estorno_recebido import _comissoes_vivas, _quebra, marcador

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from pix_estornos_pendentes import main  # noqa: E402


@pytest.fixture()
def listagem(monkeypatch):
    """O `GET /v3/payments?status=REFUNDED` falso: `lista`, ou `erro` para o Asaas fora."""
    estado = {"lista": [], "erro": None}

    def _listar():
        if estado["erro"]:
            raise estado["erro"]
        return estado["lista"]

    monkeypatch.setattr(asaas, "listar_estornados", _listar)
    return estado


def _da_versao_anterior(monkeypatch, cobranca, processado=False):
    with monkeypatch.context() as m:
        m.setattr(dreno, "drenar_evento", lambda eid: None)
        eid = entregar("PAYMENT_REFUNDED", cobranca)
    with get_conn() as conn:
        conn.execute("delete from pix_estornos_recebidos where asaas_payment_id = %s", (cobranca["asaas_payment_id"],))
        if processado:
            conn.execute("update pix_webhook_events set processed_at = now() where event_id = %s", (eid,))
        conn.commit()
    return eid


def test_simulacao_conta_nao_grava_e_nao_imprime_dado(user_id, externo, monkeypatch, listagem, capsys):
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    estorno = _da_versao_anterior(monkeypatch, cobranca)
    assert main([]) == 1
    saida = capsys.readouterr().out
    assert "modo: simulacao" in saida and "resultado: pendente" in saida
    assert marcador(cobranca["asaas_payment_id"]) == [] and evento(estorno)["processed_at"] is None
    assert cobranca["asaas_payment_id"] not in saida and cobranca["external_reference"] not in saida
    dreno.drenar_evento(estorno)  # não deixa o evento para outro teste


def test_aplicar_grava_drena_e_o_received_velho_nao_concede(externo, monkeypatch, listagem, capsys):
    """O teste do dono sem a pausa (a pausa é o PR-0, #1574): compra com o `grant` pendente, estorno
    recebido pela versão anterior, script com o `revoke` ainda falhando, e a retentativa da compra."""
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    with monkeypatch.context() as m:
        m.setitem(EXECUTORES, "grant", _quebra)
        compra = entregar("PAYMENT_RECEIVED", cobranca)
    estorno = _da_versao_anterior(monkeypatch, cobranca)
    with monkeypatch.context() as m:
        m.setitem(EXECUTORES, "revoke", _quebra)
        assert main(["--aplicar"]) == 0  # P-R3b: o marcador gravado basta, o estorno segue aberto
    assert marcador(cobranca["asaas_payment_id"]) == ["nosso"] and evento(estorno)["attempts"] == 1
    dreno.drenar_evento(compra)
    entregar("PAYMENT_RECEIVED", cobranca)  # reentrega com `event_id` novo
    assert _grants_ativos(ind.uid) == [] and _comissoes_vivas(cobranca) == []
    assert (externo["ga4"], externo["capi"], externo["email"]) == (0, 0, 0)
    dreno.drenar_evento(estorno)
    assert evento(estorno)["processed_at"] is not None and _grants_ativos(ind.uid) == []
    capsys.readouterr()
    assert main([]) == 0 and "sem_marcador: 0" in capsys.readouterr().out


def test_estorno_ja_processado_e_o_da_listagem_do_asaas_ganham_marcador(user_id, externo, monkeypatch, listagem):
    """A versão anterior já tinha PROCESSADO o estorno (sem marcador); e o estorno de evento já
    purgado só aparece na listagem do Asaas. O mesmo id nas duas fontes conta uma vez."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    _da_versao_anterior(monkeypatch, cobranca, processado=True)
    purgado = f"pay_{uuid.uuid4().hex[:12]}"
    listagem["lista"] = [{"id": purgado, "externalReference": "ext-9", "status": "REFUNDED"},
                         {"id": cobranca["asaas_payment_id"], "status": "REFUNDED"}, {"id": None, "status": "REFUNDED"}]
    assert main(["--aplicar"]) == 0
    assert marcador(cobranca["asaas_payment_id"]) == ["nosso"] and marcador(purgado) == ["alheio"]


def test_asaas_fora_sai_1(user_id, externo, monkeypatch, listagem, capsys):
    listagem["erro"] = asaas.AsaasApiError("fora", status_code=None)
    assert main(["--aplicar"]) == 1
    assert "falha_asaas: 1" in capsys.readouterr().out


def test_listar_estornados_le_todas_as_paginas_e_recusa_forma_inesperada(monkeypatch):
    """A fonte 2: segue `hasMore` pelo `offset`; corpo sem `data`/`hasMore`, ou `hasMore` sem itens,
    levanta (lista incompleta não pode virar "não há estorno")."""
    paginas, pedidos = [], []

    def _request(metodo, path, *, contexto, params=None, **kw):
        pedidos.append((metodo, path, params))
        return paginas.pop(0)

    monkeypatch.setattr(asaas, "_request", _request)
    paginas[:] = [{"data": [{"id": "pay_a"}], "hasMore": True}, {"data": [{"id": "pay_b"}], "hasMore": False}]
    assert [p["id"] for p in asaas.listar_estornados(limite=1)] == ["pay_a", "pay_b"]
    assert pedidos == [("GET", "/v3/payments", {"status": "REFUNDED", "offset": 0, "limit": 1}),
                       ("GET", "/v3/payments", {"status": "REFUNDED", "offset": 1, "limit": 1})]
    for corpo in ({"data": []}, {"hasMore": False}, {"data": [], "hasMore": True}, ["pay_a"]):
        paginas[:] = [corpo]
        with pytest.raises(asaas.AsaasApiError):
            asaas.listar_estornados()


def test_linha_ilegivel_na_fila_conta_segura_o_codigo_e_nao_esconde_as_outras(
        user_id, externo, monkeypatch, listagem, capsys):
    """Uma linha que não decifra conta em `falha_fila` e segura o código 1, mas o estorno processado
    pela versão anterior, na linha ao lado, ganha o marcador. Resolvido como o cabeçalho do script
    manda (purgar só a linha corrompida), a simulação volta a 0."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    _da_versao_anterior(monkeypatch, cobranca, processado=True)
    ruim = f"evt_{uuid.uuid4().hex[:16]}"
    with get_conn() as conn:
        conn.execute("insert into pix_webhook_events (event_id, event_type, payload_enc, event_version, processed_at)"
                     " values (%s, 'PAYMENT_REFUNDED', 'v1:nao-e-fernet', 1, now())", (ruim,))
        conn.commit()
    assert main(["--aplicar"]) == 1
    saida = capsys.readouterr().out
    assert "falha_fila: 1" in saida and f"ilegivel na fila: {ruim} (RuntimeError)" in saida
    assert marcador(cobranca["asaas_payment_id"]) == ["nosso"]
    with get_conn() as conn:
        conn.execute("update pix_webhook_events set payload_enc = null, last_error = null, purged_at = now()"
                     " where event_id = %s", (ruim,))
        conn.commit()
    assert main([]) == 0


def test_o_mesmo_id_na_fila_e_no_asaas_conta_uma_vez(user_id, externo, monkeypatch, listagem, capsys):
    """Comparado com a mesma rodada sem a listagem: a contagem é do banco inteiro do teste."""
    def sem_marcador():
        assert main([]) == 1
        (linha,) = [x for x in capsys.readouterr().out.splitlines() if x.startswith("sem_marcador: ")]
        return int(linha.split(": ")[1])

    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    _da_versao_anterior(monkeypatch, cobranca, processado=True)
    so_a_fila = sem_marcador()
    listagem["lista"] = [{"id": cobranca["asaas_payment_id"], "status": "REFUNDED"}]
    assert so_a_fila >= 1 and sem_marcador() == so_a_fila
    assert main(["--aplicar"]) == 0


@pytest.mark.parametrize("item", [{"status": "PENDING"}, {}, "lixo"], ids=["pendente", "sem_status", "lixo"])
def test_item_da_listagem_que_nao_e_refunded_nao_vira_marcador(user_id, externo, monkeypatch, listagem, capsys, item):
    """O caso do Manager: se o Asaas ignorar o filtro `status=REFUNDED`, um pagamento PENDENTE viraria
    "estornado" para sempre e o cliente pagaria sem acesso. O item não grava, conta em
    `asaas_status_inesperado` e o código é 1; o pagamento dele concede normalmente."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    listagem["lista"] = [{"id": cobranca["asaas_payment_id"], **item} if isinstance(item, dict) else item]
    assert main(["--aplicar"]) == 1
    assert "asaas_status_inesperado: 1" in capsys.readouterr().out
    assert marcador(cobranca["asaas_payment_id"]) == []
    entregar("PAYMENT_RECEIVED", cobranca)
    assert len(_grants_ativos(user_id)) == 1
