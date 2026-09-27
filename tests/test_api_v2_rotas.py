"""Portão da /api/v2: segurança por construção, varrida rota a rota.

Toda rota do sub-app é `APIRoute`, depende de `usuario_atual`, tem
`response_model` e não recebe parâmetro de usuário (path, query, header, cookie
ou campo de corpo, em qualquer profundidade). E nada sob `/api/v2` é registrado
direto no monólito — lá os tratadores do envelope não valem e a rota escaparia
desta varredura.

Controle negativo: os apps sintéticos abaixo, um por regra, cada um tem de
aparecer em `violacoes`. Controle positivo: o app real dá `[]`.

O sub-app é achado por IDENTIDADE (todo `Mount` cujo `.app` é ele, em qualquer
path, tem de ser um só, em `/api/v2`); o resto sob `/api/v2` é comparado por
segmento, não por string.

CEGUEIRA DECLARADA: rota do pai com parâmetro que case `/api/v2/...`
(`/api/{v}/me`); o sub-app montado embrulhado (`Mount(p, Middleware(app_v2))`) ou
por `Host`, que a árvore não desce; e usuário lido à mão de `request.query_params`
— o último é o que `tests/test_api_v2_isolamento.py` cobre por comportamento.
"""
import re
import typing
from collections import Counter

import pytest
from fastapi import APIRouter, Cookie, Depends, FastAPI, Header
from pydantic import BaseModel

from api.v2 import app as app_v2
from api.v2.sessao import usuario_atual
from test_pix_rota_registrada import _andar_nas_rotas

PREFIXO = "/api/v2"
_PARAM_DE_USUARIO = re.compile(r"(?i)user|usuario|uid|owner|dono")


def _dependants(dependant):
    yield dependant
    for sub in dependant.dependencies:
        yield from _dependants(sub)


def _nomes_do_tipo(tipo, visto):
    """Nomes de campo de um modelo Pydantic, descendo em aninhados e em
    `list[...]`/`Optional[...]`."""
    if isinstance(tipo, type) and issubclass(tipo, BaseModel):
        if tipo in visto:
            return
        visto.add(tipo)
        for nome, campo in tipo.model_fields.items():
            yield nome
            if campo.alias:
                yield campo.alias
            yield from _nomes_do_tipo(campo.annotation, visto)
    for arg in typing.get_args(tipo):
        yield from _nomes_do_tipo(arg, visto)


def _nomes_de_parametro(route):
    for dep in _dependants(route.dependant):
        for campo in (dep.path_params + dep.query_params + dep.header_params
                      + dep.cookie_params + dep.body_params):
            yield campo.name
            yield campo.alias
            yield from _nomes_do_tipo(campo.field_info.annotation, set())


def violacoes(app_pai, app_v2) -> list[str]:
    from fastapi.routing import APIRoute

    achados = []
    rotas_v2 = list(_andar_nas_rotas(app_v2.routes, com_rota=True))
    for path, route in rotas_v2:
        if not isinstance(route, APIRoute):
            achados.append(f"{path}: {type(route).__name__}, não APIRoute")
            continue
        if not any(d.call is usuario_atual for d in _dependants(route.dependant)):
            achados.append(f"{path}: sem a dependência usuario_atual")
        if route.response_model is None:
            achados.append(f"{path}: sem response_model")
        suspeitos = sorted({n for n in _nomes_de_parametro(route)
                            if n and _PARAM_DE_USUARIO.search(n)})
        if suspeitos:
            achados.append(f"{path}: parâmetro de usuário {suspeitos}")

    # Por identidade: o sub-app montado de novo em outro path (`/outra-area`,
    # `/api/v2-shadow`) serve as mesmas rotas, e nenhum prefixo o acharia.
    montagens = [p for p, r in _andar_nas_rotas(app_pai.routes, com_rota=True)
                 if getattr(r, "app", None) is app_v2]
    if montagens != [PREFIXO]:
        achados.append(f"sub-app da v2 montado em {montagens}, esperado só [{PREFIXO!r}]")

    # O pai tem de ver exatamente o mount e as rotas dele, cada uma uma vez: rota
    # direta, router incluído ou mount a mais sob /api/v2 muda a contagem. Por
    # SEGMENTO: `/api/v2-x` não é /api/v2.
    esperado = Counter([PREFIXO] + [PREFIXO + p for p, _ in rotas_v2])
    visto = Counter(p for p in _andar_nas_rotas(app_pai.routes)
                    if p == PREFIXO or p.startswith(PREFIXO + "/"))
    if visto != esperado:
        achados.append(f"pai sob {PREFIXO} fora do mount: a mais {sorted(visto - esperado)}, "
                       f"faltando {sorted(esperado - visto)}")
    return achados


class Saida(BaseModel):
    ok: bool


class Filho(BaseModel):
    owner_id: int


class Corpo(BaseModel):
    nome: str
    itens: list[Filho]


def _sub_app(registra):
    """Sub-app com a rota passando por `include_router` — a forma real, e a que
    `app.routes` não enxerga em fastapi 0.141.1."""
    router = APIRouter()
    registra(router)
    sub = FastAPI()
    sub.include_router(router)
    return sub


def _pai(sub):
    pai = FastAPI()
    pai.mount(PREFIXO, sub)
    return pai


def _sem_dependencia(r):
    @r.get("/x", response_model=Saida)
    def _x():  # pragma: no cover - nunca chamada
        return Saida(ok=True)


def _user_id_na_query(r):
    @r.get("/x", response_model=Saida)
    def _x(user_id: int, uid: int = Depends(usuario_atual)):  # pragma: no cover
        return Saida(ok=True)


def _sem_response_model(r):
    @r.get("/x")
    def _x(uid: int = Depends(usuario_atual)):  # pragma: no cover
        return {}


def _usuario_no_header(r):
    @r.get("/x", response_model=Saida)
    def _x(x_user: str = Header(), uid: int = Depends(usuario_atual)):  # pragma: no cover
        return Saida(ok=True)


def _dono_no_cookie(r):
    @r.get("/x", response_model=Saida)
    def _x(dono: str = Cookie(), uid: int = Depends(usuario_atual)):  # pragma: no cover
        return Saida(ok=True)


def _usuario_aninhado_no_corpo(r):
    @r.post("/x", response_model=Saida)
    def _x(corpo: Corpo, uid: int = Depends(usuario_atual)):  # pragma: no cover
        return Saida(ok=True)


def _rota_no_pai():
    sub = _sub_app(lambda r: None)
    pai = _pai(sub)
    direto = APIRouter()

    @direto.get(PREFIXO + "/escondida", response_model=Saida)
    def _x(uid: int = Depends(usuario_atual)):  # pragma: no cover
        return Saida(ok=True)

    pai.include_router(direto)
    return pai, sub


@pytest.mark.parametrize("registra, esperado", [
    (_sem_dependencia, "sem a dependência usuario_atual"),
    (_user_id_na_query, "parâmetro de usuário ['user_id']"),
    (_sem_response_model, "sem response_model"),
    (_usuario_no_header, "parâmetro de usuário"),
    (_dono_no_cookie, "parâmetro de usuário ['dono']"),
    (_usuario_aninhado_no_corpo, "parâmetro de usuário ['owner_id']"),
])
def test_controle_negativo_cada_regra_reprova_o_sub_app_sintetico(registra, esperado):
    sub = _sub_app(registra)
    achados = violacoes(_pai(sub), sub)
    assert any(esperado in a for a in achados), achados


def test_controle_negativo_rota_sob_api_v2_direto_no_pai():
    pai, sub = _rota_no_pai()
    achados = violacoes(pai, sub)
    assert any("/api/v2/escondida" in a for a in achados), achados


@pytest.mark.parametrize("outro", ["/outra-area", "/api/v2-shadow"])
def test_controle_negativo_sub_app_montado_de_novo(outro):
    sub = _sub_app(lambda r: None)
    pai = _pai(sub)
    pai.mount(outro, sub)
    achados = violacoes(pai, sub)
    assert (f"sub-app da v2 montado em ['{PREFIXO}', '{outro}'], "
            f"esperado só ['{PREFIXO}']") in achados, achados
    # Pelo motivo certo: `/api/v2-shadow` não é /api/v2 por segmento.
    assert not any(a.startswith("pai sob") for a in achados), achados


def test_app_real_nao_tem_violacao():
    import frontend.finance_bot_websocket_custom as dashboard

    rotas = [p for p, _ in _andar_nas_rotas(app_v2.routes, com_rota=True)]
    assert "/me" in rotas, "a varredura não achou o /me — o walk quebrou"
    assert violacoes(dashboard.app, app_v2) == []
