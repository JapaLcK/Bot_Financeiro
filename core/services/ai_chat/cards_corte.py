"""O corte que o usuário pede no chat ("simula cortar 30% do mercado"): validação do formato (`validar_cortes`) e a
conta (`corte`), que o card `simulador` chama. A conta é a do POST `/simulador/habitos` (`habitos_da_snapshot`), sobre a
MESMA snapshot em que o nome da categoria é resolvido. Moram fora de `cards.py` (teto de 350 linhas)."""
from __future__ import annotations

from decimal import Decimal

from utils_text import valor_valido

from .cards import MAX_CATEGORIA, CardIndisponivel
from .cards_resumos import casar_categoria

MAX_CORTES = 10
MAX_NOMES = 15  # nomes da estimativa que a recusa devolve ao modelo
CAMPOS = {"categoria", "todas", "percentual", "valor"}


class PedidoInvalido(Exception):
    """O pedido de corte não pode ser calculado, e a falha NÃO é do card (o modelo corrige e tenta de novo): sem marca
    de falha no turno. `codigo` é o `error` da tool; `extra` entra na resposta."""

    def __init__(self, codigo: str, msg: str, extra: dict | None = None):
        super().__init__(msg)
        self.codigo, self.extra = codigo, extra or {}


def _invalido(msg: str) -> PedidoInvalido:
    return PedidoInvalido("corte_invalido", f"cortes: {msg}. Corrija e chame de novo.")


def validar_cortes(bruto) -> list[dict]:
    """A lista de `show_card.cortes` normalizada (`{categoria}` ou `{todas: True}`, mais `percentual` int ou `valor`
    Decimal), ou `PedidoInvalido`. Ausente, null e [] = sem corte. Chave com null conta como ausente (o modelo manda
    null em opcional)."""
    if bruto is None or bruto == []:
        return []
    if not isinstance(bruto, list) or len(bruto) > MAX_CORTES:
        raise _invalido(f"lista de até {MAX_CORTES} itens")
    saida = []
    for p in bruto:
        if not isinstance(p, dict) or set(p) - CAMPOS:
            raise _invalido("cada item é um objeto com categoria ou todas, e percentual ou valor")
        p = {k: v for k, v in p.items() if v is not None and not (k == "todas" and v is False)}
        cat, todas, pct, valor = (p.get(k) for k in ("categoria", "todas", "percentual", "valor"))
        if (cat is None) == (todas is None) or (todas is not None and todas is not True):
            raise _invalido("cada item tem categoria OU todas: true")
        if cat is not None and (not isinstance(cat, str) or not cat.strip() or len(cat.strip()) > MAX_CATEGORIA):
            raise _invalido(f"categoria: texto de até {MAX_CATEGORIA} caracteres")
        if (pct is None) == (valor is None):
            raise _invalido("cada item tem percentual OU valor")
        item = {"categoria": cat.strip()} if cat is not None else {"todas": True}
        if pct is not None:
            if type(pct) is not int or not 1 <= pct <= 100:
                raise _invalido("percentual: inteiro de 1 a 100")
            item["percentual"] = pct
        else:
            if type(valor) not in (int, float) or not valor_valido(valor) or Decimal(str(valor)).as_tuple().exponent < -2:
                raise _invalido("valor: reais por mês, maior que zero, com até 2 casas")
            item["valor"] = Decimal(str(valor))  # nunca Decimal(float)
        saida.append(item)
    if sum(1 for i in saida if i.get("todas")) > 1:
        raise _invalido("no máximo um item com todas")
    return saida


def _acima(nome: str, teto) -> PedidoInvalido:
    return PedidoInvalido("valor_acima_da_estimativa", "O valor pedido passa do gasto estimado: diga a estimativa "
                          "(despesa_mensal) e ofereça cortar tudo ou um valor menor. Não chame de novo sem a pessoa pedir.",
                          {"categoria": nome, "despesa_mensal": float(teto)})


def corte(uid: int, pedidos: list[dict]):
    """O `SimuladorHabitos` do corte (`pedidos` de `validar_cortes`), sempre sobre a estimativa do próprio `uid`."""
    from api.v2.simulador import FORA_DA_ESTIMATIVA, REPETIDAS, habitos_da_snapshot
    from core.services import previsao_v2  # pelo atributo: o teste espia `ler_snapshot`
    from core.services.decision_simulator import DIAS
    from core.services.previsao_variavel import repartir

    snap = previsao_v2.ler_snapshot(uid, DIAS)
    est = snap.estimativa_variavel
    if not est:
        raise CardIndisponivel("ainda não há histórico para estimar o gasto do dia a dia: diga isso, sem número.")
    cats = est["categorias"]
    proprios = {}  # chave -> pedido do item específico (ganha do `todas`)
    for p in pedidos:
        if p.get("todas"):
            continue
        c = casar_categoria(cats, p["categoria"], "nome")
        if c is None:
            raise PedidoInvalido(FORA_DA_ESTIMATIVA, "Nenhuma categoria do dia a dia casa com esse nome. Chame show_card "
                                 "simulador de novo com o nome de uma delas se for a que o usuário quis; senão diga em "
                                 "texto, sem número.", {"categorias": [str(x["nome"])[:60] for x in cats[:MAX_NOMES]]})
        if c["chave"] in proprios:
            raise PedidoInvalido(REPETIDAS, "A mesma categoria foi pedida duas vezes. Corrija e chame de novo.")
        proprios[c["chave"]] = (c, p)
    cortes, valores = {}, {}
    for k, (c, p) in proprios.items():
        if "percentual" in p:
            cortes[k] = p["percentual"]
        elif p["valor"] > c["despesa_mensal"]:
            raise _acima(c["nome"], c["despesa_mensal"])
        else:
            valores[k] = p["valor"]
    if todas := next((p for p in pedidos if p.get("todas")), None):
        resto = [c for c in cats if c["chave"] not in proprios]
        if "percentual" in todas:
            cortes |= {c["chave"]: todas["percentual"] for c in resto}
        elif not resto:  # assimetria de propósito: percentual é taxa por categoria (nada se perde); valor é montante a repartir e sumiria
            raise PedidoInvalido("corte_invalido", "todas as categorias já têm corte próprio: tire o item todas")
        else:
            if todas["valor"] > sum((c["despesa_mensal"] for c in resto), Decimal(0)):
                raise _acima("todas", sum((c["despesa_mensal"] for c in resto), Decimal(0)))
            valores |= repartir(est, {c["chave"] for c in resto}, todas["valor"])
    return habitos_da_snapshot(snap, cortes, valores)
