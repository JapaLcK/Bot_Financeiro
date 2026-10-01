"""Catálogo do modal "Conectar banco": o conector direto some quando o gêmeo
Open Finance (mesmo nome normalizado, mesmo `type`) está na resposta.

A Caixa direta (219) pede QR no próprio celular e o usuário trava; a OF (619) é a
certa. Fixture = ids e pares do catálogo da Pluggy medido em 2026-09-30. Só os
nomes de 219/619 e dos 8 sem gêmeo são reais. O que cada parte mede da
normalização do nome:
  • 219/619 ("Caixa Economica Federal " × "Caixa Econômica Federal") e "Bancó W"
    medem acento e espaço no fim;
  • os outros 9 pares ("Banco Par N" × "banco par n") são nomes de marcação e só
    medem caixa — não são os nomes reais da Pluggy.
O `type` na chave só é exercitado com dois tipos visíveis (monkeypatch em
`_CONNECTABLE_TYPES`, que em produção é só PERSONAL_BANK).

Controles:
  • negativo — comentar o `continue` do filtro na rota → vermelho em
    `test_direto_com_gemeo_some` (o 219 volta);
  • positivo — `test_sem_gemeo_e_of_continuam`: os 8 diretos sem gêmeo e os 10 OF
    seguem na lista (um filtro que tirasse todo direto fica vermelho nele).
"""
from __future__ import annotations

import asyncio

import frontend.routes.open_finance as of_routes

PB = "PERSONAL_BANK"


def _c(id_, name, of, type_=PB, **extra):
    c = {"id": id_, "name": name, "type": type_, "oauth": of, **extra}
    if of is not None:
        c["isOpenFinance"] = of
    return c


GEMEOS = {217: 662, 260: 660, 209: 609, 219: 619, 216: 616,
          215: 823, 225: 824, 218: 618, 206: 606, 221: 621}
SEM_GEMEO = {279: "BB Previdência", 285: "Bradesco API", 250: "Cora",
             239: "Efí Bank", 290: "Ethereum networks", 247: "Itaú Cartões",
             200: "MeuPluggy", 291: "Wise"}

FIXTURE = [
    _c(219, "Caixa Economica Federal ", False),
    _c(619, "Caixa Econômica Federal", True),
    *[x for d, o in GEMEOS.items() if d != 219
      for x in (_c(d, f"Banco Par {d}", False), _c(o, f"banco par {d}", True))],
    *[_c(i, n, False) for i, n in SEM_GEMEO.items()],
]


def _ids(monkeypatch, catalogo):
    monkeypatch.setattr(of_routes, "list_pluggy_connectors", lambda *a, **k: catalogo)
    monkeypatch.setattr(of_routes.shared, "authorize_dashboard_access", lambda *a, **k: None)
    out = asyncio.run(of_routes.open_finance_connectors_route(None, 1))
    assert out["ok"] is True
    return [b["id"] for b in out["connectors"]]


def test_direto_com_gemeo_some(monkeypatch):
    ids = _ids(monkeypatch, FIXTURE)
    assert 219 not in ids
    assert not set(GEMEOS) & set(ids)


def test_sem_gemeo_e_of_continuam(monkeypatch):
    ids = _ids(monkeypatch, FIXTURE)
    assert set(ids) == set(GEMEOS.values()) | set(SEM_GEMEO)
    assert len(ids) == 18


def test_gemeo_de_outro_tipo_nao_tira_o_direto(monkeypatch):
    ids = _ids(monkeypatch, [_c(1, "Banco X", False),
                             _c(2, "Banco X", True, type_="BUSINESS_BANK")])
    assert ids == [1]  # o 2 sai pelo tipo (_CONNECTABLE_TYPES), não pelo gêmeo


def test_gemeo_de_outro_tipo_visivel_nao_tira_o_direto(monkeypatch):
    # Com os dois tipos visíveis, só o `type` na chave separa direto e OF.
    monkeypatch.setattr(of_routes, "_CONNECTABLE_TYPES", {PB, "BUSINESS_BANK"})
    ids = _ids(monkeypatch, [_c(1, "Banco X", False),
                             _c(2, "Banco X", True, type_="BUSINESS_BANK")])
    assert sorted(ids) == [1, 2]


def test_so_isopenfinance_true_conta_como_of(monkeypatch):
    # "true"/1 não são OF: não tiram o direto ao lado...
    for quase_of in ("true", 1):
        ids = _ids(monkeypatch, [_c(1, "Banco V", False), _c(2, "Banco V", quase_of)])
        assert sorted(ids) == [1, 2], quase_of
    # ...e, como None e ausente, valem como direto diante de um True.
    ids = _ids(monkeypatch, [_c(1, "Banco V", None, isOpenFinance=None),
                             _c(2, "Banco V", None), _c(3, "Banco V", "true"),
                             _c(4, "Banco V", 1), _c(5, "Banco V", True)])
    assert ids == [5]


def test_direto_business_sai_pelo_tipo_nao_pelo_gemeo(monkeypatch):
    # Sem gêmeo nenhum ele já some: não é efeito deste filtro.
    assert _ids(monkeypatch, [_c(1, "Banco Y", False, type_="BUSINESS_BANK")]) == []


def test_sem_chave_isopenfinance_aparece(monkeypatch):
    ids = _ids(monkeypatch, [_c(1, "Banco Z", None), _c(2, "Banco Z", None)])
    assert sorted(ids) == [1, 2]


def test_dois_diretos_somem_e_dois_of_ficam(monkeypatch):
    ids = _ids(monkeypatch, [_c(1, "Banco W", False), _c(2, "BANCO W", False),
                             _c(3, "Banco W", True), _c(4, "Bancó W", True)])
    assert sorted(ids) == [3, 4]
