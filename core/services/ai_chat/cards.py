"""Os cards que o chat do app mostra (tool `show_card`): os widgets do painel, mais `periodo` e `caixinhas`, que não são widget (só do chat).

Cada leitor chama a MESMA função que o endpoint do widget chama (as de `api/v2`,
`api/nativo`, ou o corpo que a rota legada chama: `status_metas` (caixinhas), `resumo_cartoes`,
`compute_evolution`...), sempre com `uid` explícito, nunca vindo dos args do modelo.
O bloco é o JSON do endpoint; a visão do modelo sai do MESMO objeto, só com número
como número (o `ai_guard` não reconhece texto como evidência). O envelope
`{"ok": True, ...}` das 5 rotas legadas existe aqui e na rota: o teste de paridade
(`test_ai_chat_cards.py`) é quem os mantém iguais.
"""
from __future__ import annotations

from decimal import Decimal
from functools import cache
from typing import Any, Callable, NamedTuple

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from ._context import CARDS_DO_TURNO, FALHAS_DE_CARD_DO_TURNO, metas_do_turno

IDS = ("contas", "hero", "compromissos", "resumo", "categorias", "calendario", "simulador",
       "patrimonio", "wealth", "rendimento", "assinaturas", "metas", "caixinhas", "fatura", "parcelas",
       "renda", "piggy", "periodo")
MAX_MESES = 3
MAX_MESES_PERIODO = 12  # teto de toda janela da IA: card periodo, renda, tendência e as tools de data
MAX_POR_TURNO = 3


class CardIndisponivel(Exception):
    """O card ainda não pode ser mostrado (sem HTTP equivalente)."""


def _sem_gate(uid: int, a: dict) -> None:
    return None


class Card(NamedTuple):
    ler: Callable[[int, dict], list[tuple[str | None, dict]]]  # [(mes, {recurso: objeto})]
    visao: Callable[[dict], dict]                              # {recurso: dict} -> dict
    gate: Callable[[int, dict], None] = _sem_gate              # recusa ANTES de ler dado


def exigir_acesso_do_app(uid: int) -> None:
    """Levanta o que as rotas do app levantariam para esta conta (`usuario_do_app`)."""
    from starlette.requests import Request

    from api.nativo.app import exigir_onboarding
    from frontend.routes.shared import _enforce_subscription_gate, raise_if_account_scheduled_for_deletion
    # as mesmas duas chamadas de `usuario_assinante` (api/v2/sessao.py), que precisa do Request
    raise_if_account_scheduled_for_deletion(uid)
    _enforce_subscription_gate(Request({"type": "http", "path": "/ai/chat/stream", "headers": []}), uid)
    exigir_onboarding(uid)


def card_ainda_resolve(uid: int, alvo: str) -> bool:
    """O `show_card alvo` ainda pode aparecer neste turno? Não, se o turno gastou as vagas (`MAX_POR_TURNO`),
    se o alvo já está na tela, se já falhou (`FALHAS_DE_CARD_DO_TURNO`) ou se a conta não tem acesso ao app. A
    tool que recusaria mandando ao card só recusa quando isto vale: senão o número não sairia por caminho nenhum."""
    usados = CARDS_DO_TURNO.get()
    if usados is not None and (len(usados) >= MAX_POR_TURNO or alvo in usados):
        return False
    if alvo in (FALHAS_DE_CARD_DO_TURNO.get() or ()):
        return False
    try:
        exigir_acesso_do_app(uid)  # o MESMO gate do show_card (onboarding, plano)
    except HTTPException:
        return False
    return True


def _fio(o) -> Any:
    """O que o endpoint manda: o modelo em modo json, ou o dict da rota legada."""
    return o.model_dump(mode="json") if isinstance(o, BaseModel) else jsonable_encoder(o)


def _casa(p, j):
    # Falha alto, não desalinha: dict de chave não-string ou serializer que muda o tamanho da lista.
    if isinstance(p, dict):
        assert p.keys() == j.keys(), "visão e bloco com chaves diferentes"
        return {k: _casa(v, j[k]) for k, v in p.items()}
    if isinstance(p, list):
        assert len(p) == len(j), "visão e bloco com tamanhos diferentes"
        return [_casa(v, w) for v, w in zip(p, j)]
    return float(j) if isinstance(p, Decimal) else j


def _py(o) -> Any:
    """O mesmo objeto do bloco, com o `Decimal` virando número a partir do TEXTO que o
    bloco mostra (`Dinheiro` só arredonda em modo json: 10.005 é 10.01 nos dois)."""
    if not isinstance(o, BaseModel):
        return jsonable_encoder(o)
    return _casa(o.model_dump(mode="python"), o.model_dump(mode="json"))


def _so(rec: str, *chaves: str):
    return lambda r: {k: r[rec][k] for k in chaves}


def _mes(uid: int, a: dict, **recursos: Callable) -> list[tuple[str | None, dict]]:
    return [(None, {n: f(mes=m, uid=uid) for n, f in recursos.items()}) for m in a["meses"] or [None]]


@cache
def registro() -> dict[str, Card]:
    # Imports aqui: o WhatsApp importa este módulo e não paga os routers.
    from api.nativo.mes_detalhes import mes_detalhes, periodo
    from api.nativo.patrimonio import patrimonio
    from api.nativo.rendimento import rendimento
    from api.v2.assinaturas import _assinante as gate_assinaturas, assinaturas
    from api.v2.contas import contas
    from api.v2.previsao import _assinante as gate_previsao, previsao
    from api.v2.resumo_mes import mes_pedido, resumo
    from core.ai_patterns import INSIGHTS_CACHE_TTL_SECONDS, _cache_kind, _get_cached
    from core.services.ai_chat.tools.launches import _tendencia
    from core.services import metas_conversa
    from core.services.metas_status import status_metas
    from core.services.plan_service import forecast_horizons_for, plan_gate_ok
    from db import compute_evolution
    from db.cards import list_installment_groups_detailed
    from db.cartoes_resumo import resumo_cartoes
    from fastapi import HTTPException
    from frontend.routes.shared import require_plan_feature

    # Os gates recusam ANTES de ler dado.
    def gate_mes(uid, a):
        for m in a["meses"]:
            mes_pedido(m)  # mês no futuro: o mesmo 422 do endpoint

    def gate_prev(uid, a):
        gate_previsao(uid)
        if int(a["dias"] or min(forecast_horizons_for(uid))) not in forecast_horizons_for(uid):
            raise HTTPException(403, {"error": "forecast_horizon_not_allowed"})

    def gate_simulador(uid, a):
        gate_mes(uid, a)
        if not plan_gate_ok(uid, "simulator"):  # a mesma regra da rota do simulador
            raise HTTPException(403, {"error": "pro_required", "feature": "simulator"})

    def gate_periodo(uid, a):
        mes_pedido(a["ate"])  # defensivo: quem chama já cortou o futuro

    def le_periodo(uid, a):
        ym = lambda m: (int(m[:4]), int(m[5:]))  # noqa: E731
        return [(None, {"periodo": periodo(ym(a["de"]), ym(a["ate"]), uid)})]

    def visao_periodo(r):
        # Sem `maiores` (tokens): transação citada vem de get_largest_expenses.
        p = r["periodo"]
        return {**p, "categorias": [{k: v for k, v in c.items() if k != "maiores"} for c in p["categorias"]]}

    def gate_renda(uid, a):
        require_plan_feature(uid, "financial_comparison")

    piggy_pendente = ("as dicas do Piggy ainda não estão prontas. "
                      "Diga que ainda não estão prontas e siga sem o card.")

    def gate_piggy(uid, a):
        require_plan_feature(uid, "insights")
        if _get_cached(uid, _cache_kind("insights"), INSIGHTS_CACHE_TTL_SECONDS) is None:
            raise CardIndisponivel(piggy_pendente)

    def gate_metas(uid, a):  # P1-b: as metas novas só para quem tem a chave do /painel; os demais, caixinhas
        if not metas_do_turno(uid):
            raise CardIndisponivel("metas_so_no_painel_novo. Use o card caixinhas.")

    def prev(uid, a):
        return [(None, {"previsao": previsao(dias=a["dias"], uid=uid)})]

    def assin(uid, a):
        return [(None, {"assinaturas": assinaturas(uid=uid)})]

    def renda(uid, a):
        n = max(1, min(int(a["quantidade_meses"] or 6), MAX_MESES_PERIODO))
        return [(None, {"renda": {"ok": True, "evolution": compute_evolution(uid, n), "months": n}})]

    def piggy(uid, a):
        # ponytail: SÓ o cache de 6 h. `generate_ai_insights` sem cache chama o LLM sem
        # timeout com a trava do turno na mão (o 2º turno do usuário receberia _OCUPADO).
        # O /insights do painel segue gerando. Gerar aqui exige timeout em `_call_llm`.
        itens = _get_cached(uid, _cache_kind("insights"), INSIGHTS_CACHE_TTL_SECONDS)
        if itens is None:
            raise CardIndisponivel(piggy_pendente)
        return [(None, {"insights": {"ok": True, "insights": itens}})]

    def historico(r):
        p = r["patrimonio"]
        return {**{k: p[k] for k in ("total", "partes", "motivos", "historico_desde")},
                "historico": p["historico"][-7:]}

    det = lambda *ks: _so("detalhes", *ks)  # noqa: E731
    todo = lambda rec: (lambda r: r[rec])  # noqa: E731
    return {
        "contas": Card(lambda uid, a: [(None, {"contas": contas(uid=uid)})], todo("contas")),
        "hero": Card(prev, lambda r: {k: v for k, v in r["previsao"].items()
                                      if k not in ("trajetoria", "compromissos")}, gate_prev),
        "compromissos": Card(prev, _so("previsao", "hoje", "dias", "compromissos", "motivos"), gate_prev),
        "resumo": Card(lambda uid, a: _mes(uid, a, resumo=resumo, detalhes=mes_detalhes),
                       lambda r: {"resumo": r["resumo"],
                                  "detalhes": {k: r["detalhes"][k] for k in ("guardado", "motivos")}}, gate_mes),
        "categorias": Card(lambda uid, a: _mes(uid, a, detalhes=mes_detalhes),
                           det("mes", "ate", "saiu", "categorias", "origens", "motivos"), gate_mes),
        "calendario": Card(lambda uid, a: _mes(uid, a, detalhes=mes_detalhes),
                           det("mes", "ate", "entrou", "saiu", "dias", "motivos"), gate_mes),
        "simulador": Card(lambda uid, a: _mes(uid, a, detalhes=mes_detalhes),
                          det("mes", "categorias", "motivos"), gate_simulador),
        "patrimonio": Card(lambda uid, a: [(None, {"patrimonio": patrimonio(uid=uid)})], historico),
        "wealth": Card(lambda uid, a: [(None, {"patrimonio": patrimonio(uid=uid)})],
                       _so("patrimonio", "total", "partes", "motivos")),
        "rendimento": Card(lambda uid, a: [(None, {"rendimento": rendimento(uid=uid)})], todo("rendimento")),
        "assinaturas": Card(assin, todo("assinaturas"), lambda uid, a: gate_assinaturas(uid)),
        # a visão é o MESMO texto da tool `list_metas`: dinheiro como texto pronto, nunca float. O leitor
        # devolve o dump JSON (dinheiro em string): sem o `_py`, que passaria o saldo por float
        "metas": Card(lambda uid, a: [(None, {"metas": metas_conversa.ler(uid)})],
                      lambda r: metas_conversa.para_conversa(r["metas"]), gate_metas),
        "caixinhas": Card(lambda uid, a: [(None, {"caixinhas": {"ok": True, "goals": status_metas(uid)}})],
                          todo("caixinhas")),
        "fatura": Card(lambda uid, a: [(None, {"cartoes": {"ok": True, "cards": resumo_cartoes(uid)}})],
                       todo("cartoes")),
        "parcelas": Card(lambda uid, a: [(None, {"parcelas": {
            "ok": True, "installments": list_installment_groups_detailed(uid, "urgency")}})], todo("parcelas")),
        # `tendencia` = a do get_spending_trend (oculta no app), das mesmas linhas do bloco. Como lá,
        # só entram os meses com movimento: o `compute_evolution` preenche os N meses com zero.
        "renda": Card(renda, lambda r: {**r["renda"], "tendencia": _tendencia(
            [m["expense"] for m in r["renda"]["evolution"] if m["income"] or m["expense"]])}, gate_renda),
        "piggy": Card(piggy, todo("insights"), gate_piggy),
        "periodo": Card(le_periodo, visao_periodo, gate_periodo),
    }


def montar(card: str, uid: int, a: dict) -> tuple[dict, dict]:
    """(bloco, visão do modelo) do card: acesso, gate, leitores, montagem. Levanta o que o
    endpoint levantaria (`HTTPException` dos gates, `RequestValidationError` do mês) ou o que
    a leitura levantar. Não emite nada ao app: quem chama manda a prévia e o bloco juntos,
    só depois de `montar` voltar."""
    c = registro()[card]
    exigir_acesso_do_app(uid)
    c.gate(uid, a)
    abas, visao = [], []
    for mes, objs in c.ler(uid, a):
        rec = {n: _fio(o) for n, o in objs.items()}
        mes = mes or next((r["mes"] for r in rec.values() if isinstance(r, dict) and "mes" in r), None)
        abas.append({"mes": mes, "recursos": rec})
        visao.append(({"mes": mes} if mes else {}) | c.visao({n: _py(o) for n, o in objs.items()}))
    return {"card": card, "abas": abas}, {"card": card, "abas": visao}
