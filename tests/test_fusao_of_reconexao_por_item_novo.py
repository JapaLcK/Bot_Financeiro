"""A correção da fusão sobrevive a uma reconexão que cria um ITEM NOVO.

O APONTAMENTO (Codex, PR #443): `MERGED_WALLET_DELTA_SQL` casava a transação
pelo `id` da linha de `open_finance_accounts`. `BANK_ACCOUNTS_SQL` deduplica por
`provider_account_id` e fica só com a linha da conexão MAIS NOVA — então, quando
uma reconexão cria uma linha nova para a mesma conta, a transação fundida,
presa à linha ANTIGA, saía do recorte e o gasto voltava a contar duas vezes.

ALCANÇÁVEL, com evidência (não dedução):
- o upsert da conexão é por `(user_id, provider, provider_item_id)`
  (`save_pluggy_open_finance_item`): o MESMO item reaproveita a linha, um item
  NOVO cria outra. O próprio comentário de `BANK_ACCOUNTS_SQL` documenta que
  "reconectar o banco cria uma nova connection_id com a MESMA conta" — é a razão
  de existir do `DISTINCT ON`;
- item novo com a conta antiga viva: a varredura de trial/limite
  (`enforce_of_bank_limits`) apaga o item na Pluggy e marca a conexão `PAUSED`
  SEM apagar linha; quem assina de novo conecta o banco e ganha outro item. A
  adoção de item órfão (#313) é a segunda porta. Nenhum fluxo apaga a conexão
  antiga ao conectar de novo — só o `disconnect` explícito;
- o reimport na conexão nova NÃO refunde: `_find_manual_candidates` exclui o
  manual já vinculado a QUALQUER transação (`not exists … imported_launch_id`),
  inclusive à da conexão antiga. A transação nova vira sombra de delta 0.

PREMISSA NÃO MEDIDA contra o provedor: que a Pluggy devolve o MESMO
`provider_account_id` num item novo. É a premissa que o `DISTINCT ON` já assume.

Controle negativo: voltar a `a.id = t.account_id` deixa `test_reconectar…`
vermelho. Positivo: evaporação continua (as duas conexões pausadas) e dois
usuários com o mesmo `provider_account_id` não vazam.
"""
from __future__ import annotations

from decimal import Decimal

import db
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    consolidado, ia_fora, manda, tx, uid_pro,
)


def _item(uid: int, item_id: str, conta: str, saldo: str, transacoes=()) -> int:
    """Uma conexão = um item da Pluggy. `conta` é o `provider_account_id`."""
    item = db.save_pluggy_open_finance_item(uid, {
        "id": item_id, "connector": {"id": 612, "name": "Nubank"}, "status": "UPDATED",
    })
    db.save_open_finance_sync(item["id"], [{
        "provider_account_id": conta, "name": "Nubank Conta",
        "type": "BANK", "subtype": "CHECKING_ACCOUNT", "currency": "BRL",
        "balance": Decimal(saldo), "raw": {}, "transactions": list(transacoes),
    }])
    return item["id"]


def _funde_um_real_no_item_a(uid: int) -> int:
    hoje = today_tz()
    conta = f"acc-real-{uid}"
    a = _item(uid, f"item-A-{uid}", conta, "114.88")
    manda(uid, "Gastei 1 real com a barbara")
    _item(uid, f"item-A-{uid}", conta, "113.88",
          [tx(uid, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    assert db.import_open_finance_launches(uid, a)["auto_merged"] == 1
    assert consolidado(uid) == (113.88, 0.0)
    return a


def test_reconectar_por_item_novo_nao_volta_a_contar_duas_vezes(uid_pro, ia_fora):
    """O caminho de produto: trial vence (conexão A PAUSED, linhas ficam), o
    usuário assina de novo e conecta o MESMO banco — item B, mesma conta."""
    hoje = today_tz()
    a = _funde_um_real_no_item_a(uid_pro)

    db.pause_open_finance_connection(a)
    b = _item(uid_pro, f"item-B-{uid_pro}", f"acc-real-{uid_pro}", "113.88",
              [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    db.import_open_finance_launches(uid_pro, b)

    # sem o conserto: (112.88, -1.0) — o mesmo real contado duas vezes, igual à main
    assert consolidado(uid_pro) == (113.88, 0.0)


def test_item_novo_com_o_antigo_ainda_ativo_tambem(uid_pro, ia_fora):
    """A segunda porta (adoção de item órfão, #313): A continua ativa e B chega.
    O `DISTINCT ON` fica com B; a transação fundida está presa a A."""
    hoje = today_tz()
    _funde_um_real_no_item_a(uid_pro)

    b = _item(uid_pro, f"item-B-{uid_pro}", f"acc-real-{uid_pro}", "113.88",
              [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    db.import_open_finance_launches(uid_pro, b)

    assert consolidado(uid_pro) == (113.88, 0.0)


# ── POSITIVO: a identidade não pode impedir a evaporação ────────────────────

def test_as_duas_conexoes_pausadas_a_correcao_evapora(uid_pro, ia_fora):
    """Casar por identidade não pode manter a correção viva com o banco fora do
    recorte: pausadas as duas, o gasto volta a ser contado pela Carteira."""
    hoje = today_tz()
    a = _funde_um_real_no_item_a(uid_pro)
    db.pause_open_finance_connection(a)
    b = _item(uid_pro, f"item-B-{uid_pro}", f"acc-real-{uid_pro}", "113.88",
              [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    db.import_open_finance_launches(uid_pro, b)

    db.pause_open_finance_connection(b)

    assert consolidado(uid_pro) == (-1.0, -1.0)


# ── ISOLAMENTO: a porta nova que a identidade abre ─────────────────────────

def test_mesmo_provider_account_id_em_dois_usuarios_nao_vaza(uid_pro, ia_fora):
    """Casar por `provider_account_id` é casar por um identificador do
    PROVEDOR, não nosso. Dois usuários com o mesmo (improvável, mas é a porta
    que a identidade abre): a fusão de um não pode corrigir a Carteira do outro,
    nem nos dois sentidos (CLAUDE.md §0).

    O QUE ESTE TESTE MEDE, sem exagero: a PROPRIEDADE "não vaza", da query
    inteira. Ele NÃO é controle negativo do `tc.user_id` sozinho — medido: sem
    esse filtro ele continua verde, porque o `l.user_id` já barra. Fica vermelho
    se os dois filtros sumirem ou se a query for reescrita sem o isolamento."""
    import uuid
    from tests.conftest import promote_to_pro

    outro = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(outro)
    promote_to_pro(outro)

    conta_compartilhada = f"acc-colide-{uid_pro}"
    hoje = today_tz()

    # o dono funde 1 real numa conta com o id colidente
    a = _item(uid_pro, f"item-colide-A-{uid_pro}", conta_compartilhada, "114.88")
    manda(uid_pro, "Gastei 1 real com a barbara")
    _item(uid_pro, f"item-colide-A-{uid_pro}", conta_compartilhada, "113.88",
          [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    assert db.import_open_finance_launches(uid_pro, a)["auto_merged"] == 1

    # o outro usuário: banco com o MESMO provider_account_id, Carteira intocada
    _item(outro, f"item-colide-B-{outro}", conta_compartilhada, "500.00")

    assert consolidado(uid_pro) == (113.88, 0.0), "a fusão do dono mudou"
    assert consolidado(outro) == (500.0, 0.0), \
        "a correção do dono vazou para a Carteira do outro usuário"
