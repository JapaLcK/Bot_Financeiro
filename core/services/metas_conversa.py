"""Metas em texto de conversa: a tool `list_metas` da IA e o card `metas` do chat do app.

Uma fonte de verdade para o NÚMERO: `api.v2.metas.listar` (regra em `db/metas.py` e
`db/metas_ritmo.py`); aqui só se escreve a frase. Dinheiro sai como texto pronto com "R$"
(nunca float): é o que o `ai_guard` aceita como evidência e o que o gpt-4o-mini não confunde
(`por_mes` × `ritmo.valor`, lição do #931). A cópia por motivo espelha
`webapp/src/dashboard/lib/metas-textos.ts` (§0.7: `tests/test_ai_chat_metas.py` compara as
chaves e a ordem), na voz da conversa. O nome da meta é texto do usuário: só vai como valor.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from core.services.piggy_agents import MESES_PT
from db.metas_ritmo import MOTIVOS_RITMO as SO_DO_RITMO, RITMO_DIAS_MINIMOS as DIAS_MINIMOS
from utils_text import fmt_brl

MAX_METAS = 10
ONDE_CRIAR = "Crie e edite suas metas no painel: pigbankai.com/painel (seção Metas). Pelo chat ainda não dá."
# Explicam o alvo da reserva, não o saldo da fonte: a frase do custo cobre.
DO_ALVO = ("custo_desconhecido", "plano_sem_custo_mensal")

# A ORDEM das chaves é a prioridade da frase quando há mais de um motivo (= o .ts).
FRASE_RITMO = {
    "plano_sem_investimentos": "Seu plano atual não inclui investimentos.",
    "sem_banco_conectado": "Conecte um banco pra eu acompanhar.",
    "fonte_indisponivel": "Não acho mais essa fonte. Ela pode ter sido desconectada: no painel você edita a meta e escolhe outra.",
    "conexao_pausada": "A conexão com o banco está pausada. Reconecte pra eu voltar a ler.",
    "nenhum_investimento": "Não encontrei investimentos nos seus bancos conectados.",
    "historico_incompleto": "Não consigo medir seu ritmo: um dos seus bancos não confirma a leitura dos investimentos. Reconectar o banco costuma resolver.",
    "historico_curto": lambda dias: (
        "Ainda estou juntando histórico dos seus bancos. Em alguns dias eu calculo seu ritmo." if dias is None
        else f"Ainda estou juntando histórico ({dias} de {DIAS_MINIMOS} dias). Volto a calcular seu ritmo a partir de {DIAS_MINIMOS} dias."),
    "sem_historico_da_fonte": "Pra saldo de conta eu ainda não meço ritmo. Só o que está investido tem histórico.",
    "saldo_ausente": "O banco não mandou o saldo de alguma posição.",
    "outra_moeda": "Parte do dinheiro está em outra moeda e ficou de fora.",
    "banco_desatualizado": "Seu banco não atualiza há um tempo, então o número pode estar velho.",
    "moeda_presumida": "Considerei em reais uma posição sem moeda informada.",
    "conta_fora_do_ultimo_sync": "Uma conta não veio na última atualização do banco.",
}
FRASE_CUSTO = {
    "plano_sem_custo_mensal": "Seu plano não calcula as contas fixas. O valor da reserva precisa ser definido no painel.",
    "sem_banco_conectado": "Conecte um banco pra eu calcular suas contas fixas.",
    "sem_recorrencias": "Ainda não achei contas fixas nos seus bancos.",
    "recorrencias_banco_nao_lidas": "Não consegui ler as cobranças do banco agora.",
    "recorrencia_valor_invalido": "Uma cobrança veio com um valor estranho do banco. Não dá pra calcular agora.",
}


def ler(uid: int) -> dict:
    """A resposta de `GET /api/v2/metas` (mesma função, mesmos gates de plano), com Decimal.
    Chave `dashboard_v2_enabled` NÃO é checada aqui: é `metas_ligadas`."""
    from api.v2.metas import listar  # tardio: o WhatsApp importa este módulo sem pagar o router
    return listar(uid=uid).model_dump()


def metas_ligadas(uid: int) -> bool:
    # ponytail: só quem tem a chave do /painel novo (P1-b). Quando ela abrir para todos isto vira
    # True; com criação de meta no app, `or` a pessoa já tem meta.
    from core.services.plan_service import dashboard_v2_enabled
    return dashboard_v2_enabled(uid)


def _d(x) -> Decimal | None:
    """Decimal da API, ou o float do bloco do card (round-trip do texto)."""
    return None if x is None else Decimal(str(x))


def _q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), ROUND_HALF_UP) + 0  # a tela arredonda assim (10.005 -> 10,01); "+ 0" tira o -0,00


def _brl(v: Decimal) -> str:
    return fmt_brl(_q(v))  # negativo sai "R$ -250,00": a forma que o `ai_guard` e `fmt_brl` usam


def _mes_ano(iso: str) -> str:
    return f"{MESES_PT[int(iso[5:7])]} de {iso[:4]}"


def _frase(mapa: dict, motivos, dias=None) -> str | None:
    k = next((k for k in mapa if k in motivos), None)
    if k is None:
        return None
    f = mapa[k]
    return f(dias) if callable(f) else f


def _resumo(m: dict, custo: dict) -> str:
    atual, alvo, falta, r = _d(m["atual"]), _d(m["alvo"]), _d(m["falta"]), m["ritmo"]
    sem_ritmo = _frase(FRASE_RITMO, r["motivos"], r["dias_de_historico"])
    p = []
    if m["tipo"] == "reserva":
        p.append("Reserva de emergência (%s %s de contas fixas)." % (m["meses"], "mês" if m["meses"] == 1 else "meses"))
    if atual is None:
        return " ".join([*p, "Ainda não sei quanto tem: " + (sem_ritmo or "ainda não consegui ler o saldo no banco.")])
    if alvo is None:  # reserva sem o tamanho
        return " ".join([*p, f"Tem {_brl(atual)}. Ainda não sei o tamanho da reserva: "
                         + (_frase(FRASE_CUSTO, custo["motivos"]) or "não consegui calcular suas contas fixas.")])
    if falta == 0:
        return " ".join([*p, f"Meta batida! {_brl(atual)} de {_brl(alvo)}."])
    p.append(f"{_brl(atual)} de {_brl(alvo)} ({m['percentual']}%), faltam {_brl(falta)}.")
    if m["prazo_passou"]:
        p.append(f"O prazo ({_mes_ano(m['data_alvo'])}) já passou.")
    elif m["data_alvo"]:
        por = _d(m["por_mes"])
        p.append(f"Pra chegar até {_mes_ano(m['data_alvo'])}: {_brl(por)} por mês." if por is not None
                 else f"Prazo: {_mes_ano(m['data_alvo'])}.")
    v = _d(r["valor"])
    if v is None:
        return " ".join([*p, *([sem_ritmo] if sem_ritmo else [])])
    v = _q(v)
    # o ritmo é o crescimento do SALDO da meta (não o que a pessoa guarda): o sentido vai em palavra,
    # o `ai_guard` não enxerga sinal
    t = ("O saldo da meta está parado" if v == 0
         else f"O saldo da meta está {'crescendo' if v > 0 else 'diminuindo'} {_brl(abs(v))} por mês")
    t += " (contando rendimento e variação)"
    if r["parcial"]:
        t += " (medição parcial)" if r["dias_de_historico"] is None else f" ({r['dias_de_historico']} dias de histórico)"
    if m["chega_em"]:
        fim = "" if not m["data_alvo"] else (", dentro do prazo" if m["chega_em"] <= m["data_alvo"][:7] else ", depois do prazo")
        t += f"; nesse ritmo, chega em {_mes_ano(m['chega_em'])}{fim}"
    elif v <= 0:
        t += ". Nesse ritmo, a meta não anda"
    return " ".join([*p, t + "."])


def _avisos(m: dict, resumo: str) -> list[str]:
    """Motivos que só acompanham o número (e os do ritmo), sem repetir o que o resumo já disse.
    Os que só o ritmo emite (`historico_curto`...) são a explicação do ritmo, que já está no resumo."""
    motivos = {*m["motivos"], *m["ritmo"]["motivos"]} - set(SO_DO_RITMO)
    fr = [_frase(FRASE_RITMO, [c]) for c in FRASE_RITMO if c in motivos]
    cru = [c for c in sorted(motivos) if c not in FRASE_RITMO and c not in DO_ALVO]  # código novo: sai cru
    return [f for f in (*fr, *cru) if f not in resumo]


def para_conversa(d: dict) -> dict:
    """`d` = `{"metas": [...], "custo_mensal": {...}}` (a resposta de `/metas`). Nunca float."""
    metas = d["metas"]
    itens = []
    for m in metas[:MAX_METAS]:
        resumo = _resumo(m, d["custo_mensal"])
        itens.append({"nome": m["nome"], "tipo": m["tipo"], "fonte": m["fonte"]["rotulo"],
                      "resumo": resumo, "avisos": _avisos(m, resumo)})
    sobra = len(metas) - MAX_METAS
    return {"metas": itens, "sem_metas": not metas, "mais": f"e mais {sobra} no painel" if sobra > 0 else None}
