"""Plano Família, PR 4: o membro libera/revoga `agregados` e `lancamentos` e vê quem leu; o titular lê isso de um membro.

O usuário vem sempre da sessão; `ref` é family_members.id. Toda leitura do titular passa por
`core.services.family_reads.ler_do_membro` (o único ponto que autoriza e grava o log) e sai por LISTA BRANCA de
campos, nunca `**dados`: sem descrição nos agregados, sem `mensagem`/conta/cartão/instituição/origem/id nos lançamentos.
Respostas do titular com `no-store`. Fora do schema OpenAPI, como `familia.py`.
A página de lançamentos descarta os itens internos depois da consulta: pode vir menor que `limite`, até vazia com `proximo`.
`gastos.ler_mes` e `get_budgets_status_for_month` semeiam/garantem o usuário do MEMBRO (idempotente, como no /painel dele).
"""
import base64
import hashlib
import hmac
import json
import os
from decimal import Decimal
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from api.v2.familia import _erro
from api.v2.lancamentos import _TETO, Parcela, _recusa
from api.v2.orcamento_domestico import _d
from api.v2.orcamentos_categoria import Orcamento, TotalOrcamentos
from api.v2.resumo_mes import PADRAO_MES, mes_pedido
from api.v2.sessao import usuario_atual, usuario_logado
from core.services.family_reads import ler_do_membro
from db import budgets, gastos, lancamentos, lancamentos_leitura, resumo_mes
from db import family as fam
from db import family_consents as fc
from db import family_views as views
from db.family_consents import Escopo
from db.connection import cat_visual

router = APIRouter(include_in_schema=False)
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_ROTULO_CURSOR = b"pigbank:familia:cursor:v1"


class EscopoIn(BaseModel):
    escopo: Escopo


class Permissoes(BaseModel):
    permissoes: list[Escopo]


class Leitura(BaseModel):
    quando: str
    quem: str
    escopo: Escopo
    mes: str


class Historico(BaseModel):
    itens: list[Leitura]
    proximo: str | None


class CategoriaMembro(BaseModel):
    chave: str
    nome: str
    emoji: str
    icone: str
    cor: str
    valor: Decimal
    quantidade: int


class OrcamentosMembro(BaseModel):
    itens: list[Orcamento]
    total: TotalOrcamentos


class Agregados(BaseModel):
    mes: str
    entrou: Decimal
    saiu: Decimal
    categorias: list[CategoriaMembro]
    orcamentos: OrcamentosMembro | None
    historico_desde: str | None
    motivos: list[Literal["inicio_do_historico"]]


class LancamentoMembro(BaseModel):
    data: str
    tipo: Literal["entrada", "saida"]
    valor: Decimal
    moeda: str
    descricao: str | None
    categoria: str | None
    parcela: Parcela | None


class LancamentosMembro(BaseModel):
    mes: str
    itens: list[LancamentoMembro]
    proximo: str | None


def _cipher_cursor() -> Fernet:  # mesmo molde de core/services/retomada.py: chave por HMAC de JWT_SECRET, rótulo próprio
    chave = hmac.new(os.environ["JWT_SECRET"].strip().encode(), _ROTULO_CURSOR, hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(chave))


def _abre_cursor(token: str, contexto: list) -> str:
    """O cursor cru traz instante, tabela e id internos: o titular só vê um token amarrado a titular+membro+mês."""
    try:
        *amarra, cru = json.loads(_cipher_cursor().decrypt(token.encode()))
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        raise _recusa("cursor", "Cursor inválido.") from None
    if amarra != contexto or not isinstance(cru, str):
        raise _recusa("cursor", "Cursor inválido.")
    return cru


def _permissoes(uid: int) -> Permissoes:
    return Permissoes(permissoes=[fc.ESCOPO_API[e] for e in fc.member_scopes(uid)])


@router.post("/familia/permissoes", response_model=Permissoes)
def conceder(corpo: EscopoIn, uid: int = Depends(usuario_logado)) -> Permissoes:
    try:
        fc.grant_consent(uid, fc.ESCOPO_BANCO[corpo.escopo])
    except fam.FamilyError as exc:
        raise _erro(exc) from None
    return _permissoes(uid)


@router.delete("/familia/permissoes/{escopo}", response_model=Permissoes)
def revogar(escopo: Escopo, uid: int = Depends(usuario_logado)) -> Permissoes:
    try:
        fc.revoke_consent(uid, fc.ESCOPO_BANCO[escopo])
    except fam.FamilyError as exc:
        raise _erro(exc) from None
    return _permissoes(uid)


@router.get("/familia/historico", response_model=Historico)
def historico(antes: str | None = Query(None, max_length=40), limite: int = Query(50, ge=1),
              uid: int = Depends(usuario_logado)) -> Historico:
    if antes is not None and not views._REF.fullmatch(antes):
        raise _recusa("antes", "Cursor inválido.")
    limite = min(limite, _TETO)
    linhas = fc.access_log(uid, int(antes) if antes else None, limite + 1)
    nomes: dict[int, str] = {}
    itens = []
    for r in linhas[:limite]:
        escopo, mes = r["recurso"].split(":")
        quem = nomes.setdefault(r["viewer_user_id"], views._primeiro_nome(r["viewer_user_id"]))
        itens.append(Leitura(quando=r["at"].isoformat(), quem=quem, escopo=escopo, mes=mes))
    return Historico(itens=itens, proximo=str(linhas[limite - 1]["id"]) if len(linhas) > limite else None)


def _le(uid: int, ref: str, escopo: Escopo, mes: str, ler, response: Response):
    response.headers.update(_NO_STORE)
    try:
        return ler_do_membro(uid, ref, escopo, mes, ler)
    except fam.FamilyError as exc:
        if exc.args[0] == "month_out_of_range":
            raise _recusa("mes", "Mês anterior à entrada na família.") from None
        erro = _erro(exc)  # o HTTPException descarta os headers do Response injetado: leva os próprios
        erro.headers = dict(_NO_STORE)
        raise erro from None


@router.get("/familia/membros/{ref}/agregados", response_model=Agregados)
def agregados(ref: str, response: Response, mes: str | None = Query(None, pattern=PADRAO_MES),
              uid: int = Depends(usuario_atual)) -> Agregados:
    ano, m, agora = mes_pedido(mes)
    mes_s = f"{ano:04d}-{m:02d}"

    def ler(membro: int) -> Agregados:
        r = resumo_mes.resumo_do_mes(membro, ano, m, agora)
        g = gastos.ler_mes(membro, ano, m, agora)
        orc = None
        if (ano, m) == (agora.year, agora.month):  # orçamento não tem versão: só vale para o mês corrente
            s = budgets.get_budgets_status_for_month(membro, mes_s)
            t = s["totals"]
            orc = OrcamentosMembro(
                itens=[Orcamento(categoria=b["categoria"], chave=b["cat_key"], **cat_visual(b["emoji"], b["color"]),
                                 teto=_d(b["budget"]), gasto=_d(b["spent"]), restante=_d(b["remaining"]),
                                 usado_pct=_d(b["pct"]), status=b["status"]) for b in s["budgets"]],
                total=TotalOrcamentos(teto=_d(t["budget"]), gasto=_d(t["spent"]), restante=_d(t["remaining"]),
                                      usado_pct=_d(t["pct"]), em_risco=t["at_risk"]))
        return Agregados(
            mes=mes_s, entrou=r["entrou"], saiu=r["saiu"],
            categorias=[CategoriaMembro(**{k: c[k] for k in CategoriaMembro.model_fields}) for c in g["categorias"]],
            orcamentos=orc, historico_desde=g["historico_desde"],
            motivos=["inicio_do_historico"] if "inicio_do_historico" in r["motivos"] else [])

    return _le(uid, ref, "agregados", mes_s, ler, response)


@router.get("/familia/membros/{ref}/lancamentos", response_model=LancamentosMembro)
def lancamentos_do_membro(ref: str, response: Response, mes: str | None = Query(None, pattern=PADRAO_MES),
                          cursor: str | None = Query(None, max_length=600), limite: int = Query(50, ge=1),
                          uid: int = Depends(usuario_atual)) -> LancamentosMembro:
    ano, m, agora = mes_pedido(mes)
    mes_s = f"{ano:04d}-{m:02d}"

    def ler(membro: int) -> LancamentosMembro:
        contexto = [uid, membro, mes_s]  # a decifragem falha antes da consulta e, como `ler` roda antes do log, sem log
        cru = _abre_cursor(cursor, contexto) if cursor else None
        try:
            d = lancamentos_leitura.pagina(membro, ano, m, agora, cursor=cru, limite=min(limite, _TETO))
        except lancamentos.CursorInvalido as e:
            raise _recusa("cursor", str(e)) from None
        return LancamentosMembro(
            mes=mes_s,
            proximo=_cipher_cursor().encrypt(json.dumps([*contexto, d["proximo"]]).encode()).decode() if d["proximo"] else None,
            itens=[LancamentoMembro(**{k: i[k] for k in LancamentoMembro.model_fields}) for i in d["itens"]
                   if not i["interno"]])

    return _le(uid, ref, "lancamentos", mes_s, ler, response)
