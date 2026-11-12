"""PigBank Insights, contrato v1 (PL-06): o item rastreável que os detectores (`db/insights.py` e
`core/services/insights_detectores.py`) emitem e que `/insights/{uid}/current` e o card `piggy` do chat leem.

Puro: sem banco e sem relógio (`calculado_em` é carimbado por quem gera, `ai_patterns.generate_ai_insights`).
Os números saem daqui em `Decimal` de 2 casas e viram número JSON uma vez, por
`comparacao_periodos.para_json` (o formato do Xerife e o que o `ai_guard` reconhece como evidência).
Dinheiro mora sob `valor` ou `*_valor` — a regex monetária do `ai_guard` não pega outros nomes.

O item guarda os campos legados (`key, type, icon, title, message, severity, action_*`, que o painel
antigo e o app leem) e acrescenta `explicacao`; `impacto` e `acoes` são do Pro e saem na LEITURA (`projetar`),
nunca na geração: o cache é um só para os dois planos (D2 do dono, 2026-10-22).
"""
from __future__ import annotations

import calendar
import hashlib
import re
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

VERSAO = 1
_FATURA = {"cartao": "fatura", "origem": "lançamentos + faturas de cartão"}
# tipo -> (família, fonte). Os nomes dos tipos são os que os detectores já usavam.
TIPOS: dict[str, tuple[str, dict[str, Any]]] = {
    "budget_warning": ("desvio", {"base": "orcamentos", **_FATURA}),
    "category_spike": ("desvio", {"base": "lancamentos", **_FATURA}),
    "salary_burn_fast": ("desvio", {"base": "lancamentos", **_FATURA}),
    "recurring_increase": ("assinatura", {"base": "gastos_fixos", "cartao": None, "origem": "seus gastos fixos"}),
    "of_bill_increase": ("assinatura", {"base": "open_finance", "cartao": None,
                                        "origem": "lançamentos do Open Finance"}),
    "goal_behind": ("meta", {"base": "caixinhas", "cartao": None, "origem": "depósitos e saques da caixinha"}),
    "bill_due": ("vencimento", {"base": "contas_a_pagar", "cartao": None, "origem": "suas contas a pagar"}),
    "month_projection": ("projecao", {"base": "previsao", "cartao": None,
                                      "origem": "saldo de hoje e o que está previsto até o fim do mês"}),
    "subscription_increase": ("assinatura", {"base": "open_finance", "cartao": None,
                                             "origem": "assinaturas detectadas pelo Open Finance"}),
    "positive_result": ("evolucao_positiva", {"base": "lancamentos", **_FATURA}),
}
SO_PRO = ("impacto", "acoes")
# Recurso de plano que o tipo exige além de `insights`, conferido NA LEITURA (`projetar`): o cache é um só para todos
# os planos, e quem perde o recurso dentro do TTL não pode seguir vendo o item (plano-b, B3).
RECURSO_POR_TIPO = {"bill_due": "recurring_expenses", "month_projection": "forecast",
                    "subscription_increase": "subscriptions"}
GRAVIDADE = {"info": 0, "warning": 1, "critical": 2}  # a ordem do feed e a régua do "piorou" (D4)
# Reajuste (`recurring_increase` e `subscription_increase`, Q5 e P1 da B5): entra no feed até `REAJUSTE_DIAS` dias
# depois da mudança de preço, e é warning a partir de `REAJUSTE_ALERTA`% de alta (info abaixo).
REAJUSTE_DIAS = 60
REAJUSTE_ALERTA = 10
LIMITE = 6
CHAVE_MAX = 160
_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
          "outubro", "novembro", "dezembro")


def dinheiro(v) -> Decimal:
    """float/Decimal -> Decimal de 2 casas, pelo mesmo arredondamento do `fmt_brl` do texto-modelo."""
    from core.reports.weekly import _dec
    return _dec(v)


# Dado pessoal que o usuário digita no nome de uma categoria ou estabelecimento: a chave do item vai ao
# `localStorage` e, na A2, à tabela, então nome assim vira hash (`sujeito`). Telefone só com DDI, com separador
# ou DDD + celular colado ("Boleto 1234567890" não é); `(?<!\d)`/`(?!\d)` em vez de `\b` pega "CPF12345678900x".
# ponytail: formatos usuais (ponto, espaço ou hífen); separador incomum ("123/456/789/00") passa.
_PII = (re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),                                   # e-mail
        re.compile(r"\+55[\s-]?\(?\d{2}\)?[\s-]?\d{4,5}[\s-]?\d{4}(?!\d)"),          # telefone com DDI
        re.compile(r"(?<!\d)\d(?:[ .-]?\d){12,18}(?!\d)"),                            # cartão
        re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)"),               # CNPJ
        re.compile(r"(?<!\d)\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}(?!\d)"),            # CPF
        re.compile(r"(?<!\d)\(?\d{2}\)?[\s-]?9?\d{4}[-\s]\d{4}(?!\d)"                 # telefone com separador
                   r"|(?<!\d)9\d{4}-\d{4}(?!\d)|(?<!\d)[1-9]{2}9\d{8}(?!\d)"))        # celular


def tem_pii(texto: str | None) -> bool:
    """O texto tem e-mail, telefone, cartão, CNPJ ou CPF."""
    return any(p.search(texto or "") for p in _PII)


def nome(texto: str | None, reserva: str = "outros") -> str:
    """Nome do usuário (categoria, caixinha, estabelecimento, conta) em uma linha, sem marcação, teto 40.
    Vazio depois da limpeza vira `reserva`."""
    from core.reports.weekly import _categoria_em_uma_linha
    return _categoria_em_uma_linha(texto or "", 40, reserva)


def periodo_mes(d: date) -> dict[str, Any]:
    fim = d.replace(day=calendar.monthrange(d.year, d.month)[1])
    return {"inicio": d.replace(day=1), "fim": fim, "parcial": True, "rotulo": f"{_MESES[d.month - 1]} de {d.year}"}


def periodo_dia(d: date) -> dict[str, Any]:
    return {"inicio": d, "fim": d, "parcial": False, "rotulo": d.strftime("%d/%m/%Y")}


def diferenca(atual, referencia) -> dict[str, Any]:
    """`valor` das duas parcelas que o usuário lê (2 casas cada); `percentual` do valor cheio,
    arredondado uma vez (`weekly._pct`); base zero = None. A regra do Xerife."""
    from core.reports.weekly import _pct
    a, r = Decimal(str(atual)), Decimal(str(referencia))
    return {"valor": dinheiro(a) - dinheiro(r), "percentual": _pct(a - r, r, 1)}


def navegar(rotulo: str, destino: str, **valores) -> dict[str, Any]:
    return {"tipo": "navegar", "rotulo": rotulo, "destino": destino, "valores": valores}


def sujeito(cru: str | None, normalizado: str) -> str:
    """Sujeito textual da chave: `normalizado` (`cat_key`/`merchant_key`), ou o hash dele se o nome CRU tem dado
    pessoal (a chave vai ao `localStorage` e à tabela). Decide pelo cru: `merchant_key` já tira `@` e `.`.
    Estável: o mesmo nome dá sempre o mesmo hash."""
    if not tem_pii(cru):
        return normalizado
    return hashlib.sha1(normalizado.encode()).hexdigest()[:12]


def comerciante_id(chave_comerciante: str) -> str:
    """Identificador opaco do comerciante (o `merchant_key`, em hash): o reajuste da assinatura e a conta do OF do
    mesmo comerciante se reconhecem na leitura (`projetar`) sem levar o nome, que pode ter dado pessoal, ao cache."""
    return hashlib.sha1(chave_comerciante.encode()).hexdigest()[:12]


def chave(tipo: str, sujeito, periodo: str) -> str:
    """Estável, sem versão (bump de prompt não ressuscita dispensado) e com teto: sujeito longo vira hash.

    A chave é o FATO no PERÍODO; a gravidade dele não entra. Regra para a D4 ("volta se piorar"):
    - entra no sujeito o que muda o fato: meta `atrasada` × `vencida` (prazo passado é outro fato, que a
      severidade não separa: as duas podem ser `critical`);
    - fica de fora o que é piora do mesmo fato, que a A2 compara pela `severity`: orçamento "apertando"
      (warning) × "estourou" (critical), spike e ritmo de gasto (warning × critical), e o valor de uma conta
      do OF no mesmo mês;
    - o período é o mês (orçamento, spike, ritmo, meta, conta do OF pelo mês da cobrança) ou a data do
      reajuste (recorrente: reajuste novo é fato novo).
    - conta a pagar (`bill_due`): o período é o dia do vencimento; a faixa info/warning/critical é piora do mesmo
      fato e fica fora; `vencida` entra no sujeito (`id:vencida`), como na meta.
    - previsão do mês (`month_projection`): sujeito fixo `saldo`, período o mês; warning × critical é piora.
    - reajuste de assinatura (`subscription_increase`): sujeito o `merchant_key`, período `data do reajuste:valor
      anterior em centavos` (`chave_periodo`): dois serviços do mesmo comerciante no mesmo dia são dois fatos, e a
      correção ou relançamento da cobrança nova não muda a chave.
    - sobra do mês fechado (`positive_result`): sujeito fixo `mes`, período o mês fechado.
    O sujeito pode ter `:` (`merchant_key` não tira; a meta usa `id:estado`): a chave é opaca, comparada
    inteira. Nunca faça `split(":")` dela.
    Sujeito textual entra por `sujeito()`, que troca por hash o nome com dado pessoal.
    """
    c = f"{tipo}:{sujeito}:{periodo}"
    if len(c) <= CHAVE_MAX:
        return c
    return f"{tipo}:{hashlib.sha1(str(sujeito).encode()).hexdigest()[:12]}:{periodo}"


def montar_item(tipo: str, sujeito, periodo: dict[str, Any], *, title: str, message: str, severity: str,
                icon: str, action_label: str | None, action_view: str | None, referencia: dict[str, Any],
                atual: dict[str, Any], diferenca: dict[str, Any] | None = None,
                amostra: dict[str, Any] | None = None, impacto: dict[str, Any] | None = None,
                acoes: list[dict[str, Any]] | None = None, chave_periodo: str | None = None) -> dict[str, Any]:
    """O item do contrato. `chave_periodo` sobrepõe o período da chave (a conta do OF mostra o dia da
    cobrança, mas o fato é do mês); sem ele: o mês (`AAAA-MM`) num período de mês, a data num período de um dia."""
    from core.services.comparacao_periodos import para_json
    familia, fonte = TIPOS[tipo]
    ini = periodo["inicio"]
    pk = chave_periodo or (ini.isoformat() if ini == periodo["fim"] else ini.strftime("%Y-%m"))
    item = {
        # Os tetos do sanitizador antigo: o título leva nome cru (merchant do OF, nome da meta).
        "key": chave(tipo, sujeito, pk), "type": tipo, "icon": icon[:4], "title": title[:140],
        "message": message[:240],
        "severity": severity, "action_label": action_label, "action_view": action_view,
        "explicacao": para_json({
            "versao": VERSAO, "tipo": tipo, "familia": familia, "detector": f"insights.{tipo}",
            "calculado_em": None, "periodo": periodo, "fonte": dict(fonte), "referencia": referencia,
            "atual": atual, "diferenca": diferenca, "amostra": amostra}),
    }
    if impacto:
        item["impacto"] = para_json(impacto)
    if acoes:
        item["acoes"] = para_json(acoes)
    return item


def medida(item: dict) -> Decimal | None:
    """A medida da piora além da severidade (DP2 do A2): só a conta do OF, que é sempre `warning`, pelo valor."""
    if item.get("type") != "of_bill_increase":
        return None
    try:
        v = Decimal(str(item["explicacao"]["atual"]["valor"]))
    except (KeyError, TypeError, ArithmeticError):  # roda na leitura, fora de try: item torto não vira 500
        return None
    return v if v.is_finite() else None


def piorou(item: dict, sev_dispensada: str, valor_dispensado: Decimal | None) -> bool:
    """D4: o dispensado volta se a severidade subiu ou, onde há medida, se ela subiu. Igual ou melhor = escondido."""
    if GRAVIDADE.get(item.get("severity"), -1) > GRAVIDADE.get(sev_dispensada, -1):
        return True
    m = medida(item)
    return m is not None and valor_dispensado is not None and m > valor_dispensado


def _fato(item: dict) -> tuple[str, int] | None:
    """(comerciante, valor novo em centavos): o reajuste e a conta do OF são o mesmo fato só se os dois batem."""
    e = item.get("explicacao")  # roda na leitura: item torto não vira 500
    if not isinstance(e, dict) or not isinstance(e.get("referencia"), dict) or not isinstance(e.get("atual"), dict):
        return None
    cid = e["referencia"].get("comerciante_id")
    try:
        valor = int((Decimal(str(e["atual"]["valor"])) * 100).to_integral_value())
    except (KeyError, TypeError, ValueError, ArithmeticError):  # ValueError: int(NaN)
        return None
    return (cid, valor) if cid else None


def projetar(itens: list[dict], *, acoes_ok: bool,
             dispensadas: Mapping[str, tuple[str, Decimal | None]] | None = None,
             recursos_ok: Mapping[str, bool] | None = None, limite: int | None = LIMITE) -> list[dict]:
    """O que o usuário recebe do cache, nesta ordem: sem o tipo cujo recurso (`RECURSO_POR_TIPO`) não está em
    `recursos_ok` (ausente = sem acesso); sem a conta do OF (`of_bill_increase`) do comerciante que tem reajuste de
    assinatura do MESMO fato entre os que sobraram, mesmo comerciante e mesmo valor novo (B5: um fato, um item; outro
    serviço do comerciante ou um aumento que o Recurring Payments ainda não viu saem os dois, nunca um aviso a menos;
    quem perdeu o recurso volta a ver a conta); sem o dispensado
    que não piorou (chave -> referência da dispensa, `db.insight_estado.dispensadas`), depois da absorção, para que
    dispensar o reajuste não ressuscite a conta; sem `impacto`/`acoes` fora do Pro; e só então cortado em `limite`
    (`None`: sem corte, para o envio por e-mail, que deduplica antes de cortar)."""
    d, r = dispensadas or {}, recursos_ok or {}
    out = [it for it in itens if it.get("type") not in RECURSO_POR_TIPO or r.get(RECURSO_POR_TIPO[it["type"]])]
    reajustados = {_fato(it) for it in out if it.get("type") == "subscription_increase"} - {None}
    out = [it for it in out if not (it.get("type") == "of_bill_increase" and _fato(it) in reajustados)]
    out = [it for it in out if it.get("key") not in d or piorou(it, *d[it["key"]])]
    if not acoes_ok:
        out = [{k: v for k, v in it.items() if k not in SO_PRO} for it in out]
    return out[:limite]
