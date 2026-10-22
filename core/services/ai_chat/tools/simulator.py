"""
core/services/ai_chat/tools/simulator.py — simulador de decisão de compra (Pro).

Mesma validação da rota `POST /simulator/{user_id}`
(`core.services.decision_simulator.Simulacao`).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from pydantic import ValidationError

from utils_text import fmt_brl

from ._base import Tool


def _aviso_saldo(result: dict[str, Any]) -> str:
    """Saldo de partida incompleto ou não confirmado: os números parecem precisos e
    não são. Mesma distinção do card do dashboard (`_forecastBanksWarning` em
    `frontend/dashboard.js`, que é JS e não importa daqui). Vazio quando está ok."""
    if result["balance_source"] == "unavailable":
        return ("Não foi possível confirmar seu saldo consolidado agora: esta simulação pode "
                "não incluir o saldo dos seus bancos.")
    if result["banks_excluded"]:
        return ("Seus bancos conectados não estão somados nesta simulação: ela usa só o saldo "
                "da sua Carteira.")
    # Com o veredito ligado a `veredito_fala` já carrega os motivos; esta frase genérica a suavizaria.
    if result.get('motivos') and 'veredito' not in result['atual']:
        return 'Há dados ou compromissos a conferir. Os saldos são condicionais e não autorizam uma compra.'
    return ""


_EFEITOS = ("O pior dia e os dias abaixo da reserva incluem hoje após a compra "
            "e os 90 dias seguintes (91 datas). Mostre, para cada cenário, os DOIS efeitos: o saldo nos próximos 90 dias "
            "(saldo_final_90, pior_dia, dias_abaixo_da_reserva, delta_vs_atual) e o custo "
            "do contrato inteiro (total_pago, juros_totais, parcelas_fora_do_horizonte). "
            "total_pago = pago_na_compra (tudo o que sai no dia da compra, custos únicos "
            "inclusos) + todas as parcelas; NÃO inclui a despesa mensal nova, que é "
            "recorrente e entra à parte. No à vista, entrada é 0. Se um cenário vier com "
            "compra_fora_da_janela=true, avise que "
            "a compra está datada depois dos 90 dias, então o saldo da janela não muda: "
            "ali só vale o resumo do contrato.")
# Chave SIMULADOR_VEREDITO_ENABLED=0: a note de antes do veredito, sem mudar uma palavra.
NOTA_ANTIGA = ("Se aviso_saldo não vier vazio, REPITA esse texto na resposta ANTES dos "
               "números. " + _EFEITOS + " Não recomende nem aponte cenário vencedor: "
               "a decisão é do usuário.")
NOTA_VEREDITO = (
    "veredito_fala é o veredito do sistema: comece a resposta por ela, uma linha por linha, com as mesmas "
    "palavras, datas e valores; não a recalcule, não a resuma, não a suavize, não omita linha e não a contradiga. "
    "Depois dela, se aviso_saldo não vier vazio, repita esse texto. Depois, os números. " + _EFEITOS +
    " Nunca diga que cabe, que pode comprar, que dá, que é seguro ou que está liberado, nem quando a "
    "veredito_fala diz que não achou risco. Não recomende nenhum cenário além do que ela já diz e não aponte "
    "vencedor: com mais de um cenário, mostre as diferenças (pior_dia, total_pago, juros_totais) e deixe a "
    "decisão com o usuário. Nunca escreva o codigo de um bloqueio.")


def _dm(iso: str) -> str:
    return f"{date.fromisoformat(iso):%d/%m}"


def _limpo(nome: str, n: int = 0) -> str:
    """Nome vindo do modelo/usuário/banco numa linha só: controle, formato e separador de linha/parágrafo
    (\\n, \\r, U+0085, U+2028/9) viram espaço; senão uma quebra injetaria linha na `veredito_fala`, que o
    modelo repete palavra por palavra. Vazio -> "Cenário n" (n=0: sem rótulo, item)."""
    t = "".join(" " if unicodedata.category(c) in ("Zl", "Zp") or unicodedata.category(c)[0] == "C" else c
                for c in nome)
    return re.sub(r" +", " ", t).strip() or (f"Cenário {n}" if n else "")


def _nomes(nomes: list[str]) -> str:
    # Entre aspas o nome que traria " e " ou "," (senão "A e B" + "C, D" lê como um grupo de três). Nome repetido
    # não chega aqui: `Simulacao` recusa (normalizado), então cada nome é único.
    nomes = [f'"{n}"' if (re.search(r"\s+e\s+", n, re.I) or "," in n) and not (len(n) > 1 and n[0] == n[-1] == '"') else n
             for n in nomes]
    return nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " e " + nomes[-1]


def _fala(result: dict[str, Any]) -> list[str]:
    """O veredito do servidor em linhas prontas (o modelo só as repete). Ordem: atual em risco,
    risco, dados_*, sem risco; cenários com o mesmo veredito saem juntos (bloqueio uma vez só)."""
    from core.services.decision_simulator import DIAS
    resto = ", abaixo da sua reserva" if result["reserva_minima"] > 0 else ""
    atual = result["atual"]["veredito"]
    linhas = []
    if atual["estado"] == "risco_identificado":
        linhas.append(f"Mesmo sem a compra, contando só o que é certo, no dia {_dm(atual['data'])} "
                      f"seu saldo já fica em {fmt_brl(atual['valor'])}{resto}.")
    grupos: dict[str, list] = {}  # chave do veredito -> [veredito, nomes]
    for n, c in enumerate(result["cenarios"], 1):
        v = c["resumo"]["veredito"]
        fora = c["resumo"]["compra_fora_da_janela"]  # decide antes do estado: não houve avaliação a herdar
        chave = "fora" if fora else repr((v["estado"], v["data"], v["valor"], v["bloqueios"]))
        grupos.setdefault(chave, [v, fora, []])[2].append(_limpo(c["nome"], n))
    ordem = {"risco_identificado": 0, "sem_risco_identificado": 2}
    inconclusivo = False
    vistos: set[str] = set()  # bullets já emitidos: bloqueio comum a dois grupos sai uma vez só
    for v, fora, nomes in sorted(grupos.values(), key=lambda g: 2 if g[1] else ordem.get(g[0]["estado"], 1)):
        n = _nomes(nomes)
        if fora:  # só a linha: sem bloqueios nem fecho, que falariam de uma avaliação que não houve
            linhas.append(f"{n}: a compra cai depois dos próximos {DIAS} dias, então não avaliei o risco dela.")
        elif v["estado"] == "risco_identificado":
            linhas.append(f"{n}: não recomendo. Contando só o que é certo, no dia {_dm(v['data'])} "
                          f"seu saldo fica em {fmt_brl(v['valor'])}{resto}.")
        elif v["estado"] == "sem_risco_identificado":
            linhas.append(f"{n}: não achei risco nos compromissos que conheço para os próximos {DIAS} dias.")
        else:
            inconclusivo = True
            linhas.append(f"{n}: ainda não consigo dizer se recomendo.")
            for b in v["bloqueios"]:
                itens = "".join(f" {_limpo(i['nome'])} ({_dm(i['data'])})" + ("," if k < len(b["itens"]) - 1 else "")
                                for k, i in enumerate(b["itens"]))
                if (bullet := f"• {b['texto']}{itens}") not in vistos:
                    vistos.add(bullet)
                    linhas.append(bullet)
    if atual["premissa"]:
        linhas.append(atual["premissa"])
    if inconclusivo:
        linhas.append("Abaixo vai a conta com o que eu vejo hoje, mas ela ainda não é veredito.")
    return linhas


def _simulate_purchase(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Gate soft: abaixo do tier de `FEATURE_MIN_TIER_V2["simulator"]` (o mesmo da
    rota) devolve o convite, sem número."""
    from core.services.plan_service import plan_gate_ok
    if not plan_gate_ok(user_id, "simulator"):
        return {
            "error": "pro_required",
            "message": ("O simulador de compras faz parte do plano Pro. "
                        "Quer que eu te mostre como assinar?"),
        }
    from core.services.decision_simulator import Simulacao, simulate
    try:
        simulacao = Simulacao.model_validate(args)
    except ValidationError as exc:
        return {"error": "invalid_args",
                "errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]}
    result = simulate(user_id, simulacao, com_veredito=True)
    result["aviso_saldo"] = _aviso_saldo(result)
    # Pela presença, não por `ligado()`: a chave foi lida uma vez, em `simular`.
    if "veredito" in result["atual"]:
        result["veredito_fala"] = _fala(result)
        result["note"] = NOTA_VEREDITO
    else:
        result["note"] = NOTA_ANTIGA
    return result


_CENARIO = {
    "type": "object",
    "properties": {
        "nome": {"type": "string", "description": "Rótulo curto do cenário, ex: 'À vista', 'Entrada 36k'."},
        "preco": {"type": "number", "description": "Preço total do bem, em reais."},
        "entrada": {"type": "number", "description": "Entrada paga na data da compra. Só com parcelas > 1; omita quando for à vista."},
        "parcelas": {"type": "integer", "description": "Número de parcelas, de 2 a 420. OMITA para compra À VISTA (preço cheio na data da compra)."},
        "juros_mensal_pct": {"type": "number", "description": "Juros ao mês em %, ex: 1.49. 0 = parcelado sem juros."},
        "data_compra": {"type": "string", "description": "AAAA-MM-DD, hoje ou depois. Omitir = hoje."},
        "custos_unicos": {"type": "number", "description": "Custos de uma vez só na compra (documentação, frete)."},
        "despesa_mensal_nova": {"type": "number", "description": "Gasto mensal novo que a compra traz (seguro, manutenção)."},
    },
    "required": ["nome", "preco"],
}


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "simulate_purchase",
                "description": (
                    "Simula uma compra grande (feature Pro): à vista, parcelado sem juros ou "
                    "financiado com juros (tabela Price), comparando 1 a 3 cenários com o cenário "
                    "atual. Use quando o usuário pergunta 'e se eu comprar X?', 'compensa dar mais "
                    "entrada?', 'à vista ou parcelado?'. Não invente números: pergunte preço, entrada, "
                    "parcelas e juros que faltarem. Valores monetários devem ter no máximo duas "
                    "casas decimais; se vierem frações de centavo, peça o valor em centavos ao usuário. "
                    "A taxa de juros pode ter mais casas. Só o preço, sem parcelas, é à vista. Responda começando pela `veredito_fala` do resultado, quando vier (é o veredito do sistema: nunca diga que cabe), e depois os dois efeitos de cada "
                    "cenário (saldo em 90 dias e custo do contrato), sem apontar vencedor. Se "
                    "vier 'pro_required', ofereça o upgrade com jeitinho, não despeje número nenhum."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reserva_minima": {"type": "number", "description": "Opcional: saldo mínimo que o usuário quer manter; conta os dias abaixo dele."},
                        "cenarios": {"type": "array", "items": _CENARIO, "minItems": 1, "maxItems": 3},
                    },
                    "required": ["cenarios"],
                },
            },
        },
        is_write=False,
        execute=_simulate_purchase,
    ),
]
