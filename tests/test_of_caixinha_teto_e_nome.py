"""O auto-import de caixinhas OF não pode estragar o que já é do usuário.

Três defeitos provados em vermelho antes destes testes:
  1. adotava a caixinha manual de mesmo nome, sobrescrevia o saldo dela com o do
     banco e a tornava read-only (R$ 777 viraram R$ 300);
  2. inseria por SQL direto, sem o teto de caixinhas do plano — 10 caixinhas num
     Grátis de limite 1, e a criação manual seguinte estourava PlanLimitExceeded;
  3. resolvia colisão de nome com SELECT+INSERT (TOCTOU) e agora todas as
     posições disputam o MESMO nome base: a UniqueViolation abortava a transação
     e o import inteiro sumia, com o erro engolido em pluggy_sync.py.
"""
import db
from db import get_conn
from test_of_caixinha_autoimport import (
    NU_SALDOS, _nubank_raws, _pockets, _save, _seed_connection,
)


def test_import_nao_sequestra_caixinha_manual(user_id):
    """A caixinha do usuário com o MESMO nome não é adotada — nem em saldo, nem
    em controle. Adotar fazia o dinheiro que ele guardou sumir da tela."""
    db.create_pocket(user_id, "Reserva")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update pockets set balance=777 where user_id=%s and name='Reserva'",
                        (user_id,))
        conn.commit()
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx3", "name": "Reserva", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 300.0}])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 1           # entrou como caixinha NOVA

    pk = _pockets(user_id)
    minha = pk["Reserva"]
    assert float(minha["balance"]) == 777.0        # saldo do usuário intacto
    assert minha["of_investment_id"] is None       # continua dele, editável
    assert minha["source"] != "open_finance"
    do_banco = [p for p in pk.values() if p["source"] == "open_finance"]
    assert len(do_banco) == 1 and float(do_banco[0]["balance"]) == 300.0


def test_import_respeita_o_teto_de_caixinhas_do_plano(user_id, monkeypatch):
    """Decisão do dono: o import CONTA no teto do plano. O que não couber fica
    de fora, contado em `caixinhas_sem_vaga`."""
    import core.services.plan_service as ps
    monkeypatch.setattr(ps, "pockets_restantes", lambda uid: 3)
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, _nubank_raws())
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 3
    assert res["caixinhas_sem_vaga"] == 7
    pk = _pockets(user_id)
    assert len(pk) == 3
    # as 3 que entraram são as 3 PRIMEIRAS do banco (ordem de i.id), não as maiores
    assert sorted(float(p["balance"]) for p in pk.values()) == sorted(NU_SALDOS[:3])


def test_sem_teto_no_plano_entram_todas(user_id, monkeypatch):
    """Controle positivo do gate: tier sem teto (None) continua importando tudo —
    sem ele, um gate que recusa TUDO passaria no teste de cima."""
    import core.services.plan_service as ps
    monkeypatch.setattr(ps, "pockets_restantes", lambda uid: None)
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, _nubank_raws())
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 10 and res["caixinhas_sem_vaga"] == 0


def test_teto_do_plano_e_o_mesmo_da_criacao_manual(user_id, monkeypatch):
    """O gate do import lê a MESMA conta do `check_can_create_pocket` — se as
    duas divergirem, o import volta a estourar o limite por outro caminho."""
    from core.services.plan_limits import PlanLimitExceeded
    import core.services.plan_service as ps
    import pytest
    monkeypatch.setattr(ps, "get_user_limits", lambda uid: {"pockets_max": 1})
    assert ps.pockets_restantes(user_id) == 1
    ps.check_can_create_pocket(user_id)            # cabe: nenhuma ainda
    db.create_pocket(user_id, "Única")
    assert ps.pockets_restantes(user_id) == 0
    with pytest.raises(PlanLimitExceeded):
        ps.check_can_create_pocket(user_id)


def test_nome_ocupado_nao_derruba_o_import(user_id):
    """Colisão de nome não pode abortar a transação: `pockets_user_id_name_key`
    estourando levava o import INTEIRO junto (o chamador engole a exceção)."""
    db.create_pocket(user_id, "Caixinha Nubank")
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, _nubank_raws(NU_SALDOS[:2]))
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 2
    assert set(_pockets(user_id)) == {
        "Caixinha Nubank", "Caixinha Nubank 2", "Caixinha Nubank 3"}


def test_nome_ocupado_em_OUTRA_CAIXA_tambem_conta(user_id):
    """A colisão de nome é case-INSENSITIVE, o unique da tabela não é.

    `unique(user_id, name)` (db/schema.py:115) é case-sensitive, mas todo o resto
    do código de caixinha compara `lower(name)` — `db/pockets.py:344`,
    `db/accounts.py:1754`. Com só o `on conflict (user_id, name)`, o usuário que
    já tem "caixinha nubank" ganhava uma "Caixinha Nubank" do banco: duas
    caixinhas com o mesmo nome na tela, e o laço de 50 nunca via a colisão.
    """
    db.create_pocket(user_id, "caixinha nubank")     # minúsculo, como o usuário digitou
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, _nubank_raws(NU_SALDOS[:2]))
    res = db.sync_open_finance_caixinhas(conn_id, user_id)

    assert res["caixinhas_created"] == 2
    nomes = list(_pockets(user_id))
    assert len(nomes) == len({n.lower() for n in nomes}) == 3, nomes
    assert set(nomes) == {"caixinha nubank", "Caixinha Nubank 2", "Caixinha Nubank 3"}


def test_candidato_manual_sobrevive_ao_automatico_nao_reconhecer(user_id):
    """A tela de vínculo é a saída de quem o automático não reconhece: CDB de
    emissor de fora continua candidato. Sem este ramo o papel some da tela e o
    usuário fica sem como vincular na mão."""
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, [{"id": "cdb-inter", "name": "CDB Banco Inter",
                     "issuer": "BANCO INTER S.A.", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 4000.0}])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 0           # automático NÃO toca nele
    nomes = {c["name"] for c in db.list_caixinha_candidates(user_id)}
    assert "CDB Banco Inter" in nomes              # mas a tela oferece


def test_dois_syncs_concorrentes_nao_perdem_o_import(user_id):
    """Duas conexões do MESMO banco sincronizando ao mesmo tempo disputam o
    mesmo nome base. Com SELECT+INSERT (TOCTOU) a UniqueViolation abortava a
    transação de um dos dois e o import dele sumia inteiro — medido 6/6 vermelho
    com o `on conflict do nothing` desligado, 5/5 verde com ele.
    """
    import threading

    a = _seed_connection(user_id, institution="Nubank", item="cx-a")
    b = _seed_connection(user_id, institution="Nubank", item="cx-b")
    _save(a, _nubank_raws(NU_SALDOS[:5]))
    _save(b, [{**r, "id": f"b{i}"} for i, r in enumerate(_nubank_raws(NU_SALDOS[5:]))])

    out = {}

    def roda(chave, connection_id):
        try:
            out[chave] = db.sync_open_finance_caixinhas(connection_id, user_id)
        except Exception as exc:          # noqa: BLE001 — é o que queremos medir
            out[chave] = exc

    threads = [threading.Thread(target=roda, args=(k, c)) for k, c in (("a", a), ("b", b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for chave in ("a", "b"):
        assert not isinstance(out[chave], Exception), out[chave]
    nomes = [p["name"] for p in db.list_pockets(user_id, accrue=False)]
    assert len(nomes) == 10 == len(set(nomes))
