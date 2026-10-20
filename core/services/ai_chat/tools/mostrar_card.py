"""
core/services/ai_chat/tools/mostrar_card.py — `show_card`: mostra no app um card (os widgets do painel,
mais `periodo` e `caixinhas`, só do chat), com os mesmos números do widget (`cards.py`).

Fica FORA de `SCHEMAS` (WhatsApp e /ai/chat não a oferecem): só `SCHEMAS_APP`, com
emissor. `get_tool` a resolve sempre, para o modelo que a imite no histórico
compartilhado: sem emissor devolve só os dados, sem bloco.

Ordem: gate, leitores, montagem do bloco e da visão; só então `passo` (com `previa`) e
`bloco`, juntos. Recusa ou exceção antes disso não emite nada ao app.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from utils_date import now_tz

from .. import streaming
from .._context import CARDS_DO_TURNO, CATEGORIAS_NAO_ACHADAS_DO_TURNO, FALHAS_DE_CARD_DO_TURNO, JANELAS_DO_TURNO, metas_do_turno
from ..cards import IDS, MAX_CATEGORIA, MAX_MESES, MAX_MESES_PERIODO, MAX_POR_TURNO, TOOLS_DE_TEXTO, CardIndisponivel, montar
from ._base import Tool
from .launches import _AVISO_12, _AVISO_FIM

_MES = re.compile(r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$")  # = api.v2.resumo_mes.PADRAO_MES
_COM_MES = ("resumo", "categorias", "calendario", "simulador")
_COM_CATEGORIA = ("periodo", "categorias")
_MSG_FUTURO = ("O card mostra até o mês atual. Explique isso; para o que já está lançado no futuro "
               "(parcelas, datas futuras) use get_period_summary.")


def _erro(codigo: str, msg: str) -> dict[str, Any]:
    return {"error": codigo, "message": msg}


def _registra_falha(card: str) -> None:
    """O card não saiu por plano, onboarding ou leitura: fica no turno (`FALHAS_DE_CARD_DO_TURNO`)."""
    falhas = FALHAS_DE_CARD_DO_TURNO.get()
    if falhas is not None:
        falhas.append(card)


def _falha(card: str, codigo: str, msg: str) -> dict[str, Any]:
    _registra_falha(card)
    return _erro(codigo, msg)


def _indice(m: str) -> int:
    return int(m[:4]) * 12 + int(m[5:]) - 1


def _mes_de(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _periodo(args: dict[str, Any]):
    """((de, ate) já ajustados | None, indicador | None, erro | None). O que passa de 12 meses
    vira os 12 mais recentes (sem erro); `ate` no futuro vira o mês atual. O indicador leva o
    `pedido` original e quais cortes houve (`meses`, `fim`)."""
    de, ate = args.get("de"), args.get("ate")
    if not all(isinstance(m, str) and _MES.match(m) for m in (de, ate)) or de > ate:
        return None, None, _erro("periodo_invalido", "periodo exige de e ate em AAAA-MM, com de <= ate. "
                                 "Corrija e chame de novo.")
    agora = now_tz()
    atual = f"{agora.year:04d}-{agora.month:02d}"
    if de > atual:
        return None, None, _erro("mes_futuro", _MSG_FUTURO)
    fim = ate > atual
    ate = min(ate, atual)
    cortado = _indice(ate) - _indice(de) >= MAX_MESES_PERIODO
    if cortado:
        de = _mes_de(_indice(ate) - MAX_MESES_PERIODO + 1)
    pedido = {"de": args["de"], "ate": args["ate"]}
    return (de, ate), ({"pedido": pedido, "meses": cortado, "fim": fim} if cortado or fim else None), None


def _da_recusa(args: dict[str, Any]) -> dict[str, Any] | None:
    """A janela que a recusa do turno mandou ao `periodo` (`JANELAS_DO_TURNO`): a que o modelo repetiu em `de`/`ate` (traz
    os avisos dela) ou, se ele divergiu, a última CORTADA por 12 meses (a do modelo daria o card de outro período; o
    pedido legítimo no passado só é trocado num turno que já teve recusa cortada). None = os args do modelo."""
    janelas = (JANELAS_DO_TURNO.get() or [])[::-1]
    return (next((j for j in janelas if (j["de"], j["ate"]) == (args.get("de"), args.get("ate"))), None)
            or next((j for j in janelas if "limitado_a_12_meses" in j["extra"]), None))


def _show_card(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    card = args.get("card")
    if card not in IDS:
        return _erro("card_desconhecido", f"card deve ser um de: {', '.join(IDS)}")
    if card == "metas" and not metas_do_turno(user_id):  # sem a chave `metas` é o de sempre: as caixinhas
        card = "caixinhas"
    usados = CARDS_DO_TURNO.get()  # só o card que emitiu bloco gasta uma vaga
    if usados is not None and len(usados) >= MAX_POR_TURNO:
        return _erro("limite_de_cards", f"no máximo {MAX_POR_TURNO} cards por resposta; "
                     f"o que faltar vem das tools de texto ({TOOLS_DE_TEXTO}...)")

    if usados is not None and card in usados:
        como = ("Para comparar faixas, use compare_periods" if card == "periodo"
                else "Para comparar meses, o outro mês vem de get_top_categories (ou get_category_spend, de uma categoria) com start_date/end_date, em texto" if card == "categorias"
                else "Para comparar meses, use show_card periodo com de/ate, ou categorias com meses=[...] (esse card mostra 1 mês)"
                if card in _COM_MES else "Escreva a partir do resultado anterior")
        return _erro("card_repetido", f"o card {card} já foi mostrado nesta resposta. {como}")

    meses = args.get("meses")
    if meses is None:  # ausente ou null (o modelo manda null em opcional): o mês atual
        meses = []
    if (not isinstance(meses, list) or not all(isinstance(m, str) and _MES.match(m) for m in meses)
            or len(set(meses)) != len(meses)):
        return _erro("mes_invalido", "meses: lista de AAAA-MM sem repetição. Corrija e chame de novo.")
    if len(meses) > (MAX_MESES if card == "categorias" else 1):
        if card == "periodo":
            return _erro("meses_demais", "periodo não usa meses: passe só de e ate (AAAA-MM)")
        return _erro("meses_demais", f"categorias compara até {MAX_MESES} meses (uma aba por mês); para mais "
                     "meses use show_card periodo com de/ate (até 12). resumo/calendario/simulador mostram 1 mês")
    dias = args.get("dias")
    if dias is not None and str(dias) not in ("30", "60", "90"):
        return _erro("dias_invalido", "dias: 30, 60 ou 90. Corrija e chame de novo.")
    qtd = args.get("quantidade_meses")
    if qtd is not None and (type(qtd) is not int or qtd < 1):
        return _erro("quantidade_invalida", "quantidade_meses: inteiro de 1 a 12. Corrija e chame de novo.")
    categoria = args.get("categoria")
    if categoria is not None and (not isinstance(categoria, str) or len(categoria.strip()) > MAX_CATEGORIA):
        return _erro("categoria_invalida", f"categoria: texto de até {MAX_CATEGORIA} caracteres. Corrija e chame de novo.")
    categoria = (categoria or "").strip() or None if card in _COM_CATEGORIA else None
    limitado, fim_cortado, extra = False, False, {}
    if card == "renda" and qtd is not None and qtd > MAX_MESES_PERIODO:
        limitado, extra, qtd = True, {"quantidade_meses": qtd}, MAX_MESES_PERIODO
    de_ate = {}
    if card == "periodo":
        janela = _da_recusa(args)
        ajustado, pedido, erro = _periodo({**args, "de": janela["de"], "ate": janela["ate"]} if janela else args)
        if erro:
            return erro
        de_ate = dict(zip(("de", "ate"), ajustado))
        if janela:  # os cortes e avisos da recusa; o `pedido` é o da tool que recusou
            p = janela["extra"].get("pedido")
            limitado, fim_cortado = "limitado_a_12_meses" in janela["extra"], "fim_ajustado" in janela["extra"]
            extra = {"de": p["start_date"][:7], "ate": p["end_date"][:7]} if p else {}
        elif pedido:
            limitado, fim_cortado, extra = pedido["meses"], pedido["fim"], pedido["pedido"]

    try:
        bloco, visao = montar(card, user_id, {"meses": meses if card in _COM_MES else [],
                                              "dias": None if dias is None else str(dias),
                                              "quantidade_meses": qtd, **de_ate, "categoria": categoria})
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {}
        if e.status_code == 402:  # conta sem plano ativo: o app a recusa também
            return _falha(card, "sem_plano_ativo", "A conta não tem plano ativo. Oriente a escolher um plano "
                          "em pigbankai.com/precos, sem citar números.")
        if d.get("error") == "pro_required":
            return _falha(card, "pro_required", "Esse card faz parte de um plano superior. Ofereça o upgrade com jeitinho, sem números.")
        return _falha(card, d.get("error") or f"http_{e.status_code}",
                      "O card não está disponível agora. Explique isso e responda em texto, sem inventar números.")
    except CardIndisponivel as e:
        return _falha(card, "indisponivel", str(e))
    except RequestValidationError:
        return _erro("mes_futuro", _MSG_FUTURO)
    except Exception:  # RuntimeError, KeyError, o assert do `_casa`: o runner devolve `falha_interna` e loga; aqui só registra
        _registra_falha(card)
        raise
    # Só com o bloco pronto: prévia e bloco saem juntos, em sequência. Exceção antes daqui
    # (gate, leitor, serialização) não deixa o app com esqueleto sem bloco.
    if usados is not None:
        usados.append(card)
    if categoria and (nao := CATEGORIAS_NAO_ACHADAS_DO_TURNO.get()) is not None and any(
            a.get("categoria_pedida", {}).get("encontrada") is False for a in visao.get("abas", [])):
        nao.append(categoria)  # o `get_category_spend` dela devolve o dado, não manda a um 2º card
    streaming.passo("show_card", False, card)
    streaming.emitir({"tipo": "bloco", "bloco": bloco})
    if limitado:  # só na visão: o bloco é o JSON do endpoint
        visao = {**visao, "limitado_a_12_meses": True, "pedido": extra,
                 "aviso_limite": _AVISO_12}
    if fim_cortado:  # a mesma indicação (e o mesmo aviso) da tool: `_limita_periodo`
        visao = {**visao, "fim_ajustado": True, "pedido": extra, "aviso_fim": _AVISO_FIM}
    return visao


SCHEMA = {
    "type": "function",
    "function": {
        "name": "show_card",
        "description": (
            f"Mostra no app um card do painel com os números do usuário. Chame UMA vez por card, "
            f"no máximo {MAX_POR_TURNO} por resposta, e escreva os números a partir do resultado desta tool. "
            "Cards: contas (saldo total e por conta: 'meu saldo'), hero (previsão de saldo), compromissos (próximos "
            "pagamentos), resumo (SÓ o gasto do mês e o ritmo contra o mês passado, não o que entrou), categorias (gastos por categoria; aceita "
            "até 3 meses, uma aba por mês), calendario (gastos por dia), simulador (Pro: cortar gastos do dia a dia), patrimonio, "
            "wealth (distribuição do patrimônio), rendimento (quanto os investimentos renderam no mês e no total, por categoria e ativo), assinaturas, "
            "metas (caixinhas com meta), fatura (cartões e limite), parcelas, renda (o que ENTROU × saiu "
            "mês a mês, até 12 meses; se recusar por plano, o dado de UM mês sai em texto pelo get_period_summary), piggy (insights), periodo (2 a 12 meses inteiros, 'março a agosto', "
            "'este ano': entrou, saiu, saldo, barras por mês e categorias somadas; use de/ate). Erro de plano: ofereça o upgrade, sem números."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "card": {"type": "string", "enum": [i for i in IDS if i != "caixinhas"]},
                "meses": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_MESES,
                          "description": "AAAA-MM, só para resumo/categorias/calendario/simulador. Omita = mês atual."},
                "dias": {"type": "string", "enum": ["30", "60", "90"],
                         "description": "Horizonte, só para hero/compromissos."},
                "quantidade_meses": {"type": "integer", "minimum": 1,
                                     "description": "Só para renda. Até 12; acima disso vem cortado com aviso. Padrão 6."},
                "de": {"type": "string", "description": "AAAA-MM do 1º mês, só para periodo (obrigatório)."},
                "ate": {"type": "string", "description": "AAAA-MM do último mês, só para periodo (obrigatório)."},
                "categoria": {"type": "string", "description": "categoria: só para periodo/categorias — a categoria que o "
                              "usuário citou ('mercado'); o resultado traz o valor dela em categoria_pedida."},
            },
            "required": ["card"],
        },
    },
}


# Quem tem a chave do /painel novo (`metas_ligadas`): `metas` são as metas e `caixinhas` as caixinhas.
SCHEMA_METAS = copy.deepcopy(SCHEMA)
_f = SCHEMA_METAS["function"]
assert "metas (caixinhas com meta)" in _f["description"]
_f["description"] = _f["description"].replace(
    "metas (caixinhas com meta)", "metas (metas e reserva de emergência, sobre o dinheiro do banco), caixinhas (caixinhas e cofrinhos)")
_f["parameters"]["properties"]["card"]["enum"] = list(IDS)


TOOLS: list[Tool] = [Tool(schema=SCHEMA, is_write=False, execute=_show_card)]
