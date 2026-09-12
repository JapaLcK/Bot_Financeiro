"""Issue #321, a fatia que ela mandou para issue própria: NUL/surrogate na
RESPOSTA da API da Pluggy (fronteira de terceiro, não entrada de usuário).

O que muda em relação às outras fatias do #321: isto roda em BACKGROUND (sync,
refresh periódico). Não há 5xx nem laço de reenvio — o sintoma é linha que não
nasce. A política também muda: aqui é SANEAR (`limpa_para_pg`, o mesmo do #320),
porque o `raw` é blob forense e perder a conta inteira é pior que gravar
`U+FFFD` num campo.

ONDE O CONSERTO MORA: `core/services/pluggy.py`, no `return` de `_pluggy_get` e
no do `update_pluggy_item` — as duas portas de leitura da API. Não nos 8
`Jsonb(...)` de `db/open_finance.py`, porque a resposta também alimenta campos
`text` (`name` da instituição, `name` da conta, `description` da transação) que
os `Jsonb` deixariam de fora. MEDIDO abaixo pelo `name` da conta, não só pelo
`raw`.

CONTROLE NEGATIVO (§3): `test_negativo_*` troca o `limpa_para_pg` do módulo por
identidade e exige que a gravação ESTOURE — nos dois caminhos (o item, que vira
a conexão, e a conta/transação do espelho), porque são dois `execute` diferentes.

CONTROLE POSITIVO (§3): `test_positivo_*` — resposta limpa grava exatamente o
mesmo `jsonb` e o mesmo `name` de antes. Sem ele, um saneamento que destruísse
dado bom (apagar o campo, trocar acento) passaria nos negativos.
"""
import psycopg
import pytest

import core.services.pluggy as pluggy
import db
from core.services.pluggy_sync import normalize_pluggy_account, normalize_pluggy_transaction
from db.connection import get_conn

NUL = "\x00"
FFFD = "�"

ITEM_LIMPO = {"id": "item-limpo", "status": "UPDATED",
              "connector": {"id": 612, "name": "Nubank"}}
CONTA_LIMPA = {"id": "acc-1", "name": "Conta Corrente", "type": "BANK",
               "subtype": "CHECKING_ACCOUNT", "currency": "BRL", "balance": 10}
TX_LIMPA = {"id": "tx-1", "description": "Mercado", "amount": -12.5,
            "date": "2026-09-01T00:00:00.000Z", "category": "Food"}


def _envenena(dado: dict, campo: str) -> dict:
    return {**dado, campo: dado[campo] + NUL + "x"}


@pytest.fixture()
def pluggy_responde(monkeypatch):
    """Fixture que faz o `httpx.Client.get` devolver o que o teste mandar —
    o caminho real passa por `_pluggy_get`, que é onde o conserto mora."""
    corpo: dict = {}

    class _Resp:
        status_code = 200
        is_success = True

        def json(self):
            return corpo["payload"]

    def _fake_get(self, url, headers=None, params=None):
        return _Resp()

    monkeypatch.setattr(pluggy.httpx.Client, "get", _fake_get)
    return lambda payload: corpo.__setitem__("payload", payload)


def _conexao_do_item(user_id: int, item_bruto: dict) -> dict:
    """O caminho de produção: lê o item pela API e grava a conexão."""
    item = pluggy.get_pluggy_item(item_bruto["id"], api_key="k")
    return db.save_pluggy_open_finance_item(user_id, item)


def _espelha_conta(connection_id: int, conta_bruta: dict, tx_bruta: dict) -> None:
    """O outro caminho: /accounts + /v2/transactions → espelho."""
    conta = normalize_pluggy_account(conta_bruta)
    conta["transactions"] = [normalize_pluggy_transaction(tx_bruta)]
    db.save_open_finance_sync(connection_id, [conta])


def test_nome_de_instituicao_venenoso_vira_conexao_saneada(user_id, pluggy_responde):
    """`connector.name` é `text`, não `jsonb` — é o campo que os `Jsonb` não
    cobririam e o motivo de o conserto estar na porta da API."""
    item = _envenena(ITEM_LIMPO["connector"], "name")
    pluggy_responde({**ITEM_LIMPO, "connector": item})

    conexao = _conexao_do_item(user_id, ITEM_LIMPO)

    assert conexao["institution_name"] == "Nubank" + FFFD + "x"


def test_conta_e_transacao_venenosas_nascem_saneadas(user_id, pluggy_responde):
    pluggy_responde(ITEM_LIMPO)
    conexao = _conexao_do_item(user_id, ITEM_LIMPO)

    pluggy_responde({"results": [_envenena(CONTA_LIMPA, "name")]})
    conta = pluggy.list_pluggy_accounts("item-limpo", "k")[0]
    pluggy_responde({"results": [_envenena(TX_LIMPA, "description")]})
    tx = pluggy.list_pluggy_transactions("acc-1", "k")[0]

    _espelha_conta(conexao["id"], conta, tx)

    with get_conn() as c, c.cursor() as cur:
        cur.execute("select name, raw from open_finance_accounts where connection_id=%s",
                    (conexao["id"],))
        linha = cur.fetchone()
        cur.execute("""select t.description from open_finance_transactions t
                       join open_finance_accounts a on a.id = t.account_id
                       where a.connection_id=%s""", (conexao["id"],))
        descricao = cur.fetchone()["description"]

    assert linha is not None, "a conta não nasceu"
    assert linha["name"] == "Conta Corrente" + FFFD + "x"
    assert linha["raw"]["name"] == "Conta Corrente" + FFFD + "x", "o jsonb bruto não foi saneado"
    assert descricao == "Mercado" + FFFD + "x"


def test_negativo_sem_saneamento_a_conexao_nao_nasce(user_id, pluggy_responde, monkeypatch):
    """Desliga o conserto no módulo em que ele mora: a gravação estoura e a
    linha não existe — que é exatamente o sintoma silencioso da issue."""
    monkeypatch.setattr(pluggy, "limpa_para_pg", lambda valor: valor)
    pluggy_responde({**ITEM_LIMPO, "connector": _envenena(ITEM_LIMPO["connector"], "name")})

    with pytest.raises(psycopg.Error):
        _conexao_do_item(user_id, ITEM_LIMPO)

    assert db.count_open_finance_connections(user_id) == 0


def test_negativo_sem_saneamento_a_conta_nao_nasce(user_id, pluggy_responde, monkeypatch):
    """O par do de cima: conexão e espelho são `execute` diferentes, e um
    negativo só não discriminaria o outro."""
    pluggy_responde(ITEM_LIMPO)
    conexao = _conexao_do_item(user_id, ITEM_LIMPO)

    monkeypatch.setattr(pluggy, "limpa_para_pg", lambda valor: valor)
    pluggy_responde({"results": [_envenena(CONTA_LIMPA, "name")]})
    conta = pluggy.list_pluggy_accounts("item-limpo", "k")[0]
    pluggy_responde({"results": [TX_LIMPA]})
    tx = pluggy.list_pluggy_transactions("acc-1", "k")[0]

    with pytest.raises(psycopg.Error):
        _espelha_conta(conexao["id"], conta, tx)

    with get_conn() as c, c.cursor() as cur:
        cur.execute("select count(*) as n from open_finance_accounts where connection_id=%s",
                    (conexao["id"],))
        assert cur.fetchone()["n"] == 0


def test_positivo_resposta_limpa_grava_igual(user_id, pluggy_responde):
    """O saneamento não pode tocar em dado bom — inclusive acento, que é o que
    um `encode('ascii')` apressado estragaria."""
    conta_com_acento = {**CONTA_LIMPA, "name": "Conta Corrênte ção"}
    pluggy_responde(ITEM_LIMPO)
    conexao = _conexao_do_item(user_id, ITEM_LIMPO)

    pluggy_responde({"results": [conta_com_acento]})
    conta = pluggy.list_pluggy_accounts("item-limpo", "k")[0]
    pluggy_responde({"results": [TX_LIMPA]})
    tx = pluggy.list_pluggy_transactions("acc-1", "k")[0]
    _espelha_conta(conexao["id"], conta, tx)

    assert conexao["institution_name"] == "Nubank"
    with get_conn() as c, c.cursor() as cur:
        cur.execute("select name, raw from open_finance_accounts where connection_id=%s",
                    (conexao["id"],))
        linha = cur.fetchone()

    assert linha["name"] == "Conta Corrênte ção"
    assert linha["raw"] == conta_com_acento, "o jsonb mudou com a resposta limpa"
