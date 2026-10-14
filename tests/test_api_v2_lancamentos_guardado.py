"""Guardado (P8) em `GET /api/v2/lancamentos?guardado=1`: depósito de caixinha e aporte saem da
conta, saque e resgate entram, `guardado` e `interno` verdadeiros, fora de TODO total.
Guardado é OPT-IN: o `todos` deste arquivo manda `guardado=1`; o `todos_antigo` (o de
`test_api_v2_lancamentos.py`) não manda.

Gravado pelos escritores reais (`db.pocket_*`, `db.investment_*`), re-datado para o mês da
matriz de `test_resumo_mes_regra.py` (os escritores gravam "agora").

- positivo: a soma dos não internos segue sendo o Resumo; `resumo-do-mes` e `mes-detalhes`
  (entrou, saiu, categorias, dias, origens, fora_do_total) IDÊNTICOS com e sem as linhas; saídas
  guardado em BRL = `guardado.aportes` e entradas = `guardado.saques` (aporte em USD na mistura,
  corte do plano no meio do mês); `categoria=` nunca as devolve; B não vê as de A; /editar e
  /apagar = 409; keyset com limite=1.
- opt-in: SEM `guardado` a lista (itens, ordem e cursor de cada página) é a de ANTES das 4 linhas
  existirem, e `/api/app/lancamentos` (o app nativo monta o mesmo router; apps instalados não se
  atualizam com o backend) idem; com `guardado=1` as 4 aparecem. NEGATIVO: default True = vermelho.
- conciliado com o banco (`db.bank_movements._bind` liga a transação ao depósito/aporte): continua
  sem conta, instituição e motivo de moeda, e `conta=` não o devolve.
- NEGATIVO (conserto desligado por monkeypatch no SQL, em caso que estava verde): N1 sem
  `or guardado` no `interno` (a lista com `guardado=1`; `mes_detalhes` nem a lê); N2 resgate
  mapeado para saída; N3 `guardado` sempre falso (o filtro
  de categoria perde a exclusão); N4 o `left join lateral` da transação sem `not l.guardado`;
  N5 o `_DA_CONTA_SQL` sem `not l.guardado`.
"""
from __future__ import annotations

from decimal import Decimal

from psycopg.types.json import Jsonb

import db
from conftest import usuario_pagante
from core.services import plan_service
from db import lancamentos, mes_detalhes as md
from db.resumo_mes import resumo_do_mes
from tests._patrimonio_helpers import q, tx_banco
from tests.test_api_app_sessao import cliente, completo
from tests.test_api_v2_lancamentos import M, ok, resumo, soma
from tests.test_api_v2_lancamentos import todos as todos_antigo
from tests.test_api_v2_lancamentos_escrita import codigo, linha, post, saldo
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_bank_movements import _bank, _sync, _tx
from tests.test_resumo_mes_regra import ENTROU, INICIO, SAIU, _em, _lanc, semeia_a, semeia_b
from utils_date import now_tz

D = Decimal
IGUAIS = ("entrou", "saiu", "categorias", "dias", "origens", "fora_do_total")


def todos(quem, *args, **params) -> list[dict]:
    return todos_antigo(quem, *args, guardado=1, **params)


def guarda(uid: int, caixinha: str = "Viagem", banco: dict | None = None) -> dict[str, int]:
    """Depósito 120, saque 50, aporte 300, resgate 100 pelos escritores, nos dias 10 a 13 do
    mês da matriz; `criar_caixinha` e `create_investment` vão junto (têm de ficar fora)."""
    q("update accounts set balance = 5000 where user_id = %s returning user_id", (uid,))
    db.create_pocket(uid, caixinha)
    db.create_investment_db(uid, "CDB", rate=0.10, period="yearly", tax_profile="exempt_ir_iof")
    ids = {"deposito": db.pocket_deposit_from_account(uid, caixinha, 120, "dep", funding_source=banco)[0],
           "saque": db.pocket_withdraw_to_account(uid, caixinha, 50)[0],
           "aporte": db.investment_deposit_from_account(uid, "CDB", 300, "ap", funding_source=banco)[0],
           "resgate": db.investment_withdraw_to_account(uid, "CDB", 100)[0]}
    for dia, lid in zip((10, 11, 12, 13), ids.values()):
        q("update launches set criado_em = %s where id = %s returning id", (_em(dia), lid))
    q("""update launches set criado_em = %s where user_id = %s
         and tipo in ('criar_caixinha', 'create_investment') returning id""", (_em(9), uid))
    return ids


def detalhes(uid) -> dict:
    return md.ler(uid, INICIO.year, INICIO.month, now_tz())


def _resumo_db(uid) -> tuple[Decimal, Decimal]:
    """Sem HTTP: a rota exige a chave (`libera`), que também trava os escritores da semente."""
    r = resumo_do_mes(uid, INICIO.year, INICIO.month)
    return r["entrou"], r["saiu"]


def corta(monkeypatch, dia: int) -> None:
    corte = INICIO.replace(day=dia)
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda uid, now=None: corte)
    monkeypatch.setattr(md, "history_earliest_date", plan_service.history_earliest_date)


def mundo(libera_):
    """A com a matriz + os 4 movimentos; devolve (a, ids, itens por id, resumo e detalhes de ANTES)."""
    a = usuario_pagante()
    semeia_a(a)
    antes = (_resumo_db(a), detalhes(a))
    ids = guarda(a)
    libera_(a)
    return a, ids, {i["id"]: i for i in todos(a, mes=M)}, antes


# ── positivo ────────────────────────────────────────────────────────────────

def test_os_quatro_movimentos_na_lista_fora_de_todo_total(libera):
    a, ids, itens, (resumo_antes, det_antes) = mundo(libera)
    esperado = {"deposito": "saida", "aporte": "saida", "saque": "entrada", "resgate": "entrada"}
    for nome, tipo in esperado.items():
        i = itens[f"l{ids[nome]}"]
        bruto = q("select valor from launches where id = %s", (ids[nome],))["valor"]
        assert (i["tipo"], i["guardado"], i["interno"], i["pode"], i["origem"], i["motivos"]) == (
            tipo, True, True, [], "registro_antigo", []), nome
        assert i["conta_id"] is None and D(i["valor"]) == bruto > 0, nome
    assert {D(itens[f"l{ids[n]}"]["valor"]) for n in esperado} == {D(120), D(50), D(300), D(100)}
    assert not any(f"l{x['id']}" in itens for x in q(
        "select id from launches where user_id = %s and tipo in ('criar_caixinha','create_investment')",
        (a,), fetch=True))
    assert soma(list(itens.values())) == resumo(a, mes=M) == (ENTROU, SAIU) == resumo_antes
    assert all(not i["guardado"] for i in itens.values() if not i["interno"])
    # o Resumo e o Para onde vai não mudam com as linhas (o bloco `guardado` muda por desenho)
    depois = detalhes(a)
    assert {k: depois[k] for k in IGUAIS} == {k: det_antes[k] for k in IGUAIS}


def test_somas_guardado_batem_com_o_bloco_guardado_usd_e_corte(libera, monkeypatch):
    a = usuario_pagante()
    semeia_a(a)
    ids = guarda(a)
    usd = _lanc(a, "aporte_investimento", 77, 14, interno=True)
    q("update launches set currency = 'USD' where id = %s returning id", (usd,))
    libera(a)

    def confere():
        itens = [i for i in todos(a, mes=M) if i["guardado"]]
        brl = [i for i in itens if i["moeda"] == "BRL"]
        g = detalhes(a)["guardado"]
        assert sum(D(i["valor"]) for i in brl if i["tipo"] == "saida") == g["aportes"]
        assert sum(D(i["valor"]) for i in brl if i["tipo"] == "entrada") == g["saques"]
        return itens

    todas = confere()
    assert {i["moeda"] for i in todas} == {"BRL", "USD"} and f"l{usd}" in {i["id"] for i in todas}
    assert next(i for i in todas if i["id"] == f"l{usd}")["tipo"] == "saida"
    assert detalhes(a)["guardado"]["aportes"] == 200 + 120 + 300  # a semente, o depósito e o aporte
    corta(monkeypatch, 12)  # corte no meio do mês: fica o aporte (12), o resgate (13) e o USD (14)
    cortadas = confere()
    assert {i["id"] for i in cortadas} == {f"l{ids['aporte']}", f"l{ids['resgate']}", f"l{usd}"}
    assert detalhes(a)["guardado"]["aportes"] == 300 and detalhes(a)["guardado"]["saques"] == 100


def test_pago_pelo_banco_tambem_e_guardado(libera):
    a = usuario_pagante()
    _, banco = _bank(a)
    ids = guarda(a, banco=banco)
    libera(a)
    itens = {i["id"]: i for i in todos(a, mes=M)}
    for nome in ("deposito", "aporte"):
        i = itens[f"l{ids[nome]}"]
        assert (i["tipo"], i["guardado"], i["interno"], i["pode"]) == ("saida", True, True, [])
    assert itens[f"l{ids['resgate']}"]["tipo"] == "entrada" and itens[f"l{ids['resgate']}"]["guardado"]
    assert soma(list(itens.values())) == resumo(a, mes=M)


def _conciliado(uid, item, guardar, valor, moeda="BRL"):
    """Depósito/aporte pago pelo banco e CONCILIADO pelo caminho real (declara com `funding_source`,
    o extrato chega, `import_open_finance_launches`): `_bind` grava `imported_launch_id` na
    transação. Mais uma linha normal do banco na mesma conta. Devolve (conta, guardado, normal).
    A declaração só aceita conta BRL, então a USD é BRL conciliada e virada depois (limite)."""
    cid = db.save_pluggy_open_finance_item(uid, {"id": item, "connector": {"id": 612, "name": "Nubank"},
                                                 "status": "UPDATED"})["id"]
    _sync(cid, 1000)
    conta_id = q("select id from open_finance_accounts where connection_id = %s", (cid,))["id"]
    lid = guardar(uid, {"kind": "bank", "of_account_id": conta_id, "label": "Nubank"}, valor)
    _sync(cid, 1000 - valor, _tx(-valor, f"mov-{item}"))
    db.import_open_finance_launches(uid)
    status = q("select reconciliation_status s from open_finance_transactions where imported_launch_id = %s",
               (lid,))["s"]
    assert status == "bank_movement_confirmed"
    normal = _lanc(uid, "despesa", 41, 9, source="open_finance")
    q("update launches set external_id = %s, efeitos = %s where id = %s returning id",
      (f"TX-{item}", Jsonb({"delta_conta": 0}), normal))
    tx_banco(conta_id, f"TX-{item}", "-41", imported_launch_id=normal)
    if moeda != "BRL":
        q("update open_finance_accounts set currency = %s where id = %s returning id", (moeda, conta_id))
    q("update launches set criado_em = %s where id = %s returning id", (_em(10), lid))
    return conta_id, lid, normal


def _mundo_conciliado(libera_):
    def deposito(uid, banco, valor):
        db.create_pocket(uid, "Viagem")
        return db.pocket_deposit_from_account(uid, "Viagem", valor, "dep", funding_source=banco)[0]

    def aporte(uid, banco, valor):
        db.create_investment_db(uid, "CDB", rate=0.10, period="yearly", tax_profile="exempt_ir_iof")
        return db.investment_deposit_from_account(uid, "CDB", valor, "ap", funding_source=banco)[0]

    a = usuario_pagante()
    contas = {"deposito": _conciliado(a, f"mov-brl-{a}", deposito, 120),
              "aporte": _conciliado(a, f"mov-usd-{a}", aporte, 300, moeda="USD")}
    libera_(a)
    return a, contas


def _vazamentos(a, contas) -> list:
    """O que o guardado conciliado pega da conta do banco (vazio = o contrato vale)."""
    achados = []
    itens = {i["id"]: i for i in todos(a, mes=M)}
    for nome, (conta_id, lid, normal) in contas.items():
        i = itens[f"l{lid}"]
        proprio = q("select coalesce(upper(currency), 'BRL') c from launches where id = %s", (lid,))["c"]
        if (i["conta_id"], i["instituicao"], i["motivos"], i["fundido"], i["moeda"]) != (None, None, [], False, proprio):
            achados.append((nome, "metadado", i["conta_id"], i["instituicao"], i["motivos"], i["moeda"]))
        daconta = [x["id"] for x in todos(a, mes=M, conta=conta_id)]
        if f"l{lid}" in daconta:
            achados.append((nome, "conta="))
        if f"l{normal}" not in daconta:  # controle positivo: a linha normal do banco segue vindo
            achados.append((nome, "normal sumiu", daconta))
    return achados


def test_guardado_conciliado_com_o_banco_nao_pega_conta_nem_moeda(libera):
    """`_bind` liga a transação do banco ao depósito/aporte; a lista não herda a conta dela."""
    a, contas = _mundo_conciliado(libera)
    assert _vazamentos(a, contas) == []
    itens = todos(a, mes=M)
    assert {(i["tipo"], i["guardado"], i["interno"], tuple(i["pode"])) for i in itens if i["guardado"]} == {
        ("saida", True, True, ())}
    assert soma(itens) == resumo(a, mes=M) == (0, 82)  # só as duas linhas normais do banco (41 + 41)


def test_filtro_de_categoria_nunca_devolve_guardado(libera):
    a = usuario_pagante()
    ids = guarda(a)
    sem = _lanc(a, "despesa", 11, 15)  # despesa sem categoria: continua vindo
    q("update launches set categoria = null where id = %s returning id", (sem,))
    libera(a)
    achados = {i["id"] for i in todos(a, mes=M, categoria="sem categoria")}
    assert f"l{sem}" in achados and not achados & {f"l{x}" for x in ids.values()}
    assert {f"l{x}" for x in ids.values()} <= {i["id"] for i in todos(a, mes=M)}
    assert any(i["guardado"] for i in todos(a, mes=M, q="viagem"))  # a descrição é o nome da caixinha


def test_isolamento_a_nao_ve_a_caixinha_de_b(libera):
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a(a)
    semeia_b(b)
    guarda(a)
    libera(a)  # só A: os escritores de B ainda rodam
    antes = ([i["id"] for i in todos(a, mes=M)], detalhes(a)["guardado"])
    ids_b = guarda(b, "ViagemDoB")
    libera(a, b)
    lista = todos(a, mes=M)
    assert [i["id"] for i in lista] == antes[0] and detalhes(a)["guardado"] == antes[1]
    assert "ViagemDoB" not in str(lista) and todos(a, mes=M, q="ViagemDoB") == []
    assert {f"l{x}" for x in ids_b.values()} <= {i["id"] for i in todos(b, mes=M)}  # B vê as suas


def test_editar_e_apagar_guardado_dao_409_sem_tocar_na_linha(libera):
    a = usuario_pagante()
    ids = guarda(a)
    libera(a)
    antes = ({n: linha(i) for n, i in ids.items()}, [(p["name"], p["balance"]) for p in db.list_pockets(a)])
    for lid in ids.values():
        assert codigo(post(a, "editar", {"id": f"l{lid}", "categoria": "lazer"})) == (409, "nao_editavel")
        assert codigo(post(a, "apagar", {"id": f"l{lid}"})) == (409, "nao_editavel")
    assert ({n: linha(i) for n, i in ids.items()},
            [(p["name"], p["balance"]) for p in db.list_pockets(a)]) == antes


def test_guardado_forjado_como_carteira_continua_sem_pode_e_sem_escrita(libera):
    """Defesa em profundidade: linha guardado com cara de Carteira (manual, `origem`, flag falsa,
    `delta_conta`) ou de banco (`source='open_finance'`, que sozinho daria `categoria`/`descricao`)
    não ganha `pode` na lista nem passa na escrita (editar/apagar), com saldo intacto."""
    a = usuario_pagante()
    q("update accounts set balance = 5000 where user_id = %s returning user_id", (a,))
    forjados = {}
    for source in ("manual", "open_finance"):
        for dia, (tipo, delta) in enumerate((("deposito_caixinha", -120), ("saque_caixinha", 50),
                                             ("aporte_investimento", -300), ("resgate_investimento", 100)), 10):
            forjados[source, tipo] = q("""insert into launches (user_id, tipo, valor, criado_em, is_internal_movement,
                    source, external_id, origem, efeitos) values (%s, %s, %s, %s, false, %s, %s, 'carteira', %s::jsonb)
                    returning id""", (a, tipo, abs(delta), _em(dia), source,
                                      f"forjado-{tipo}" if source == "open_finance" else None,
                                      f'{{"delta_conta": {delta}}}'))["id"]
    libera(a)
    itens = {i["id"]: i for i in todos(a, mes=M)}
    antes = ({t: linha(i) for t, i in forjados.items()}, saldo(a))
    for lid in forjados.values():
        assert itens[f"l{lid}"]["pode"] == [] and itens[f"l{lid}"]["guardado"] is True
        for corpo in ({"categoria": "lazer"}, {"descricao": "x"}, {"valor": "10"}):
            assert codigo(post(a, "editar", {"id": f"l{lid}", **corpo})) == (409, "nao_editavel")
        assert codigo(post(a, "apagar", {"id": f"l{lid}"})) == (409, "nao_editavel")
    assert ({t: linha(i) for t, i in forjados.items()}, saldo(a)) == antes


def test_keyset_com_limite_1_atravessa_o_mes_sem_repetir_nem_pular(libera):
    a, ids, itens, _ = mundo(libera)
    um_a_um = [i["id"] for i in todos(a, mes=M, limite=1)]
    assert um_a_um == [i["id"] for i in todos(a, mes=M, limite=100)]
    assert len(um_a_um) == len(set(um_a_um)) and {f"l{x}" for x in ids.values()} <= set(um_a_um)


# ── opt-in: sem o parâmetro a lista é a de antes ────────────────────────────────

def paginas(quem, **params) -> list[dict]:
    """Cada página como veio (itens, `proximo`), de 3 em 3, até o fim."""
    cursor, saida = None, []
    for _ in range(50):
        r = ok(quem, mes=M, limite=3, **params, **({"cursor": cursor} if cursor else {}))
        saida.append({"itens": r["itens"], "proximo": r["proximo"]})
        if not (cursor := r["proximo"]):
            return saida
    raise AssertionError("paginação não termina")


def test_sem_o_parametro_a_lista_e_identica_a_de_antes_do_guardado(libera, monkeypatch):
    a = usuario_pagante()
    semeia_a(a)
    libera(a)
    antes = paginas(a)  # a matriz, sem nenhuma das 4 linhas
    assert len(antes) > 1 and not any(i["guardado"] for p in antes for i in p["itens"])
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")  # a trava Q36 recusa os escritores da semente
    ids = guarda(a)
    libera(a)
    assert paginas(a) == antes  # mesmos itens, mesma ordem, mesmo cursor
    for tipo in ("entrada", "saida"):  # os filtros Entradas/Saídas do app também ficam sem guardado
        assert not any(i["guardado"] for p in paginas(a, tipo=tipo) for i in p["itens"])
    com = {i["id"] for p in paginas(a, guardado=1) for i in p["itens"]}  # controle positivo
    assert {f"l{x}" for x in ids.values()} <= com


def test_app_nativo_so_recebe_guardado_se_pedir(monkeypatch):
    """`/api/app` monta o mesmo router: o app instalado não conhece `guardado` e não pode recebê-lo."""
    a = completo(usuario_pagante())
    semeia_a(a)
    ids = guarda(a)
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")
    guardados = {f"l{x}" for x in ids.values()}

    def do_app(**params):
        r = cliente(a).get("/api/app/lancamentos", params={"mes": M, "limite": 100, **params})
        assert r.status_code == 200, r.text
        return {i["id"] for i in r.json()["itens"]}
    assert not guardados & do_app() and not guardados & do_app(tipo="entrada") and not guardados & do_app(tipo="saida")
    assert guardados <= do_app(guardado=1)  # controle positivo


# ── NEGATIVOS: o conserto desligado, em caso que estava verde ───────────────────

def _desliga(monkeypatch, velho: str, novo: str) -> None:
    assert velho in lancamentos._PERNA_LANCAMENTOS
    monkeypatch.setattr(lancamentos, "_PERNA_LANCAMENTOS", lancamentos._PERNA_LANCAMENTOS.replace(velho, novo))


def test_n1_interno_nao_forcado_quebra_a_soma_e_as_categorias(libera, monkeypatch):
    """Linha `deposito_caixinha` com a flag falsa: sem `or guardado`, entra na soma da lista."""
    a = usuario_pagante()
    semeia_a(a)
    falsa = _lanc(a, "deposito_caixinha", 40, 16, interno=False)
    libera(a)
    base = detalhes(a)
    assert soma(todos(a, mes=M)) == resumo(a, mes=M) == (ENTROU, SAIU)  # com o conserto: verde
    assert {i["id"]: i for i in todos(a, mes=M)}[f"l{falsa}"]["interno"] is True
    _desliga(monkeypatch, "l.is_internal_movement or l.guardado as interno", "l.is_internal_movement as interno")
    assert soma(todos(a, mes=M))[1] != SAIU
    assert detalhes(a)["categorias"] == base["categorias"]  # `mes_detalhes` lê sem guardado: nem a forjada entra


def test_n2_resgate_como_saida_quebra_entradas_igual_saques(libera, monkeypatch):
    a, ids, _, _ = mundo(libera)

    def entradas():
        return sum(D(i["valor"]) for i in todos(a, mes=M) if i["guardado"] and i["tipo"] == "entrada")
    assert entradas() == detalhes(a)["guardado"]["saques"] == 150  # com o conserto: verde
    _desliga(monkeypatch, f" or l.tipo in {lancamentos._RESGATAR}", "")
    assert entradas() != detalhes(a)["guardado"]["saques"]


def test_n3_sem_guardado_o_filtro_de_categoria_devolve_o_deposito(libera, monkeypatch):
    a = usuario_pagante()
    ids = guarda(a)
    libera(a)
    assert todos(a, mes=M, categoria="sem categoria") == []  # com o conserto: verde
    _desliga(monkeypatch, "l.guardado, l.valor", "false as guardado, l.valor")
    assert f"l{ids['deposito']}" in {i["id"] for i in todos(a, mes=M, categoria="sem categoria")}


def test_n4_lateral_sem_not_guardado_vaza_conta_instituicao_e_moeda(libera, monkeypatch):
    a, contas = _mundo_conciliado(libera)
    assert _vazamentos(a, contas) == []  # com o conserto: verde
    _desliga(monkeypatch, "and not l.guardado order by", "order by")
    assert {v[1] for v in _vazamentos(a, contas)} == {"metadado"}  # o `conta=` segue limpo: o N5 o guarda


def test_n5_da_conta_sem_not_guardado_devolve_o_guardado_em_conta(libera, monkeypatch):
    a, contas = _mundo_conciliado(libera)
    assert _vazamentos(a, contas) == []  # com o conserto: verde
    assert " and not l.guardado and " in lancamentos._DA_CONTA_SQL
    monkeypatch.setattr(lancamentos, "_DA_CONTA_SQL", lancamentos._DA_CONTA_SQL.replace(" and not l.guardado", ""))
    assert {v[1] for v in _vazamentos(a, contas)} == {"conta="}
