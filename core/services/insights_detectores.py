"""Detectores de insights do PL-06 que nasceram depois do A1 (`db/insights.py` não cresce; contrato em
`core/services/insights_contrato.py`). Os imports do pacote `db` são tardios: `db/insights.py` importa este
módulo dentro de `compute_active_insights`, e o import no topo fecharia o ciclo."""
from __future__ import annotations

from typing import Any

from core.services.insights_contrato import (
    REAJUSTE_ALERTA, REAJUSTE_DIAS, RECURSO_POR_TIPO, comerciante_id, diferenca, dinheiro, montar_item, navegar, nome,
    periodo_dia, periodo_mes, sujeito,
)
from utils_date import day_tz, now_tz
from utils_text import fmt_brl

JANELA = 7     # dias antes do vencimento em que a conta entra (info até D-4)
ALERTA = 3     # de D-3 a D-1: warning; D-0: critical
N_VENCIDA = 7  # P1 do dono (2026-10-28): a vencida aparece de D+1 a D+7, critical
# P6 da B3: a tela do botão ("Ver gastos fixos") calcula outra coisa; a ressalva nunca sai do texto por tamanho.
NOTA_GASTOS_FIXOS = "A Previsão mensal de Gastos fixos considera só recorrências e pode mostrar outro valor."


def _textos(nm: str, due, dias: int, valor, variavel: bool) -> tuple[str, str]:
    v = "valor a confirmar" if valor is None else f"cerca de {fmt_brl(valor)}" if variavel else fmt_brl(valor)
    quando = ("vence hoje" if dias == 0 else "vence amanhã" if dias == 1 else f"vence em {dias} dias" if dias > 0
              else "venceu ontem" if dias == -1 else f"venceu há {-dias} dias")
    dia = due.strftime("%d/%m")
    inicio = (f"Vencimento hoje, {dia}." if dias == 0 else f"Vencimento em {dia}." if dias > 0
              else f"Venceu em {dia} e continua em aberto.")
    return f"{nm} {quando} ({v})", f"{inicio} Se já pagou, marque como paga em Contas a pagar."


def detect_bill_due(user_id: int) -> list[dict[str, Any]]:
    """Conta a pagar pendente (`bill_instances`, pela leitura da agenda e da previsão, `db.bills.ler_instancias`,
    que filtra `user_id` e casa o recorrente do mesmo usuário): info de D-7 a D-4, warning de D-3 a D-1, critical
    em D-0 e vencida (D+1 a D+N_VENCIDA, uma por recorrente: a mais recente). Paga ou apagada sai da leitura.
    Chave `bill_due:id:vencimento` (vencida: `id:vencida`), então a dispensa volta só quando a faixa piora (D4).
    Fora: faturas de cartão, gastos fixos automáticos e o envio. O Carteiro avisa a mesma conta no feed de agentes:
    a duplicidade no app é aceita (Q2d). Sem plano de gastos fixos (`recurring_expenses`, T1) não há aviso: na geração
    e de novo na leitura do cache (`insights_contrato.RECURSO_POR_TIPO`), para quem perde o plano dentro do TTL."""
    from core.services.plan_service import plan_gate_ok
    from db.bills import ler_instancias
    from db.connection import get_conn
    if not plan_gate_ok(user_id, RECURSO_POR_TIPO["bill_due"]):  # poupa a consulta; a leitura do cache confere de novo
        return []
    hoje = day_tz(now_tz())
    with get_conn() as conn, conn.cursor() as cur:
        contas = ler_instancias(cur, user_id, include_paid=False)
    out: list[dict[str, Any]] = []
    vencidas: set[int] = set()
    # Prazo de horas primeiro (P3): as que ainda vencem, da mais próxima à mais longe; depois as vencidas, da mais
    # recente à mais antiga (que é também a que ganha no recorrente); `id` desempata. A gravidade segue no sort global.
    for c in sorted(contas, key=lambda c: (c["due_date"] < hoje, abs((c["due_date"] - hoje).days), c["id"])):
        due, dias = c["due_date"], (c["due_date"] - hoje).days
        if not -N_VENCIDA <= dias <= JANELA:
            continue
        vencida = dias < 0
        if vencida and c["recurring_id"] is not None:
            if c["recurring_id"] in vencidas:
                continue
            vencidas.add(c["recurring_id"])
        conta = nome(c["name"], reserva="Conta")  # sem nome, ou só marcação
        nm = conta[:1].upper() + conta[1:]  # `capitalize()` estragaria "CEMIG"
        valor = dinheiro(c["amount"]) if c["amount"] and c["amount"] > 0 else None  # variável sem estimativa = 0
        title, message = _textos(nm, due, dias, valor, bool(c["variable_amount"]))
        out.append(montar_item(
            "bill_due", f"{c['id']}:vencida" if vencida else c["id"], periodo_dia(due),
            severity="critical" if dias <= 0 else "warning" if dias <= ALERTA else "info",
            icon="⚠️" if vencida else "🧾", title=title, message=message,
            action_label="Ver contas a pagar", action_view="fixed",
            referencia={"estatistica": "vencimento", "conta": conta, "vencimento": due,
                        "valor": valor, "variavel": bool(c["variable_amount"])},
            atual={"dias_para_vencer": dias}, diferenca=None, amostra=None,
            acoes=[navegar("Ver contas a pagar", "fixed", **({"conta_valor": valor} if valor else {}))]))
    return out


def _textos_mes(per: dict, fecha, saldo) -> tuple[str, str, str]:
    """(severidade, título, corpo) do `month_projection`: P3 (saldo de hoje < 0 = critical) e P6 da B3."""
    prev = (f"Previsão para {per['fim']:%d/%m}: {fmt_brl(-fecha)} no negativo, sem contar os gastos do dia a dia. "
            f"{NOTA_GASTOS_FIXOS}")
    if saldo < 0:
        return "critical", "Seu saldo está no vermelho", f"Seu saldo já está {fmt_brl(-saldo)} no negativo. {prev}"
    mes = per["rotulo"].split(" de ")[0]
    return "warning", f"{mes[:1].upper()}{mes[1:]} pode fechar no vermelho", prev


def detect_month_projection(user_id: int) -> list[dict[str, Any]]:
    """O mês corrente fecha negativo no pior caso do SIMULADOR (`veredito.py`), lido no último dia do mês e nunca no
    pior dia: a mesma snapshot, a mesma janela de conferência (receita fixa não conferida dos últimos
    `JANELA_CONFERENCIA_DIAS` dias conta a favor; antes dela, "já aconteceu"), sem a estimativa do dia a dia nem as
    saídas duvidosas (`cashflow.fechamento_pior_caso`).
    warning com o saldo de hoje >= 0, critical com ele < 0; fechamento >= 0 não avisa. Calado onde o simulador não
    conclui: banco conectado, fixo sem data, valor estimado, pedido pendente no chat. A Previsão mensal de Gastos fixos
    soma só recorrências e pode discordar: o texto avisa (`NOTA_GASTOS_FIXOS`). O chat (`check_cashflow`, sem a janela)
    pode se abster no mesmo caso (P7, issue #1326). Gate `forecast` (Plus) aqui e na leitura (`RECURSO_POR_TIPO`).
    Só para quem já informou o saldo (`carteira_qualidade.saldo_informado`, emenda 1): a Carteira nasce em 0, e o
    primeiro gasto de quem nunca a informou não é "saldo no vermelho"."""
    from core.services.cashflow import fechamento_pior_caso, premissa_da_janela
    from core.services.cashflow_snapshot import carregar
    from core.services.plan_service import plan_gate_ok
    from db.carteira_qualidade import saldo_informado
    from db.connection import get_conn
    if not plan_gate_ok(user_id, RECURSO_POR_TIPO["month_projection"]):  # poupa a snapshot; a leitura confere de novo
        return []
    with get_conn() as conn, conn.cursor() as cur:
        if not saldo_informado(cur, user_id):  # antes do `carregar`, que é a parte cara
            return []
    hoje = day_tz(now_tz())
    per = periodo_mes(hoje)
    snap = carregar(user_id, hoje, per["fim"])
    fecha = fechamento_pior_caso(snap, per["fim"])
    if fecha is None or fecha >= 0:
        return []
    saldo = dinheiro(snap.base["saldo"])
    severity, title, message = _textos_mes(per, fecha, saldo)
    return [montar_item(
        "month_projection", "saldo", per, severity=severity, icon="📉", title=title, message=message,
        action_label="Ver gastos fixos", action_view="fixed",
        referencia={"estatistica": "previsao_pior_caso", "data_alvo": per["fim"], "saldo_hoje_valor": saldo,
                    "premissa": premissa_da_janela(snap)},
        atual={"valor": fecha}, diferenca=None, amostra=None, impacto={"falta_valor": -fecha})]


def detect_subscription_increase(user_id: int) -> list[dict[str, Any]]:
    """Assinatura mensal da Pluggy (`listar_assinaturas`, `servicos`: P4) que subiu de preço nos últimos `REAJUSTE_DIAS`
    dias (Q5), depois de o preço antigo se repetir em duas cobranças seguidas (`reajustes_estaveis`, P2). Queda não
    avisa (P3); warning a partir de `REAJUSTE_ALERTA`% (P1). Chave `subscription_increase:{merchant_key}:{dia do
    reajuste}:{valor ANTERIOR em centavos}` (o sujeito é `sujeito(nome, chave)`: o `merchant_key`, ou o hash dele
    quando o nome tem dado pessoal): reajuste novo é fato novo; dois serviços do mesmo comerciante reajustados no
    mesmo dia com preços antigos diferentes (iCloud e Apple Music) são dois avisos, cada um dispensável (P6); e o
    preço antigo, já assentado, não muda quando a Pluggy corrige os centavos da cobrança nova ou a relança com outro
    id (P7). Limites aceitos: mesmo comerciante, mesmo dia e mesmo preço antigo viram um aviso só; corrigir uma
    cobrança ANTIGA muda a chave. A conta do OF (`of_bill_increase`) do MESMO fato (mesmo `comerciante_id` e mesmo
    valor novo) sai na LEITURA (`insights_contrato.projetar`), só quando este item sobreviveu ao gate. Se esta
    consulta falha, o detector entra em `falhas`, não há item e a conta do OF continua saindo (fail-open). Passada a
    janela, a conta do OF da mesma assinatura pode voltar (issue #1336). Gate `subscriptions` (Plus) aqui e na
    leitura."""
    from datetime import date, timedelta

    from core.reports.weekly import _pct
    from core.services.assinaturas import listar_assinaturas
    from core.services.plan_service import plan_gate_ok
    if not plan_gate_ok(user_id, RECURSO_POR_TIPO["subscription_increase"]):  # poupa as consultas; a leitura confere
        return []
    hoje = day_tz(now_tz())
    por_chave: dict[str, dict[str, Any]] = {}
    for a in listar_assinaturas(user_id, hoje)["reajustes_estaveis"]:  # só `servicos`, preço antigo estável
        anterior, novo = a["valor_anterior"], a["valor"]
        if a["status"] != "ativa" or novo <= anterior:
            continue
        quando = date.fromisoformat(a["reajuste_em"])
        if not hoje - timedelta(days=REAJUSTE_DIAS) <= quando <= hoje:
            continue
        novo_valor = dinheiro(novo)
        conta = nome(a["nome"], reserva="Assinatura")
        nm = conta[:1].upper() + conta[1:]
        it = montar_item(
            "subscription_increase", sujeito(a["nome"], a["chave"]), periodo_dia(quando),
            chave_periodo=f"{quando.isoformat()}:{int(dinheiro(anterior) * 100)}",  # pelo preço antigo (P6, P7)
            severity="warning" if (novo - anterior) * 100 >= REAJUSTE_ALERTA * anterior else "info",
            title=f"{nm} subiu {_pct(novo - anterior, anterior, 0)}%",
            message=(f"De {fmt_brl(anterior)} para {fmt_brl(novo)} por mês, cobrado assim desde {quando:%d/%m}. "
                     "Detectado pelo Open Finance."),
            icon="📈", action_label=None, action_view=None,  # o painel antigo não tem tela de assinaturas (D9)
            referencia={"estatistica": "valor_anterior", "assinatura": conta, "valor": dinheiro(anterior),
                        "frequencia": "monthly", "comerciante_id": comerciante_id(a["chave"])},
            atual={"valor": novo_valor, "data": quando}, diferenca=diferenca(novo, anterior),
            impacto={"por_ano_valor": dinheiro((novo - anterior) * 12)})  # mensal (`_mensal`): x12 é exato (D8)
        # Mesma chave = mesmo comerciante, dia e preço antigo (limite 1 aceito na P7): fica o primeiro na ordem das
        # cadeias.
        por_chave.setdefault(it["key"], it)
    return list(por_chave.values())


SOBRA_E = "Sobra é o que entrou menos o que saiu no mês, com o cartão pela fatura."  # P5 da B4: nunca "guardou"
SOBRA_DESDE_DIA = 5  # P7 da B4: o mês fechado só é avaliado a partir deste dia do mês seguinte (dado atrasado)
SOBRA_COBERTURA_DIAS = 7  # P8 da B4: algum lançamento nos primeiros dias do mês base (indício de cobertura)


def detect_positive_result(user_id: int) -> list[dict[str, Any]]:
    """O mês que acabou de fechar (no fuso do app) teve sobra positiva e maior que a do mês anterior (Q4 da B4). A sobra
    é o "Entrou − Saiu" do Resumo do mês, lido por `comparacao_periodos.comparar_mes` (mesma fonte, sem soma nova);
    não é poupança: dinheiro posto na caixinha continua contando como sobra, e o texto nunca diz "guardou". Dados
    suficientes (P1 c): o plano enxerga os dois meses inteiros (`inicio_do_historico`), o mês anterior tem lançamento
    (`sem_base`), o histórico do usuário começa até o dia 1 do mês anterior (`previsao_variavel._inicio_do_historico`)
    e o Resumo não avisa número possivelmente incompleto (`motivos`). Reduzem o risco de dado faltando, sem garantir
    completude: conexão do banco que parou (PAUSED ou DELETED, `_TERMINAL`) a partir do dia 1 do mês anterior, no fuso
    do app (P6 com a P10 e a P11: o `updated_at` da conexão terminal é usado como data APROXIMADA da parada: nunca fica
    antes da parada real; desde a issue #1371 a varredura de plano não seleciona DELETED e a pausa preserva o estado
    terminal e o `updated_at` dele (`tests/test_of_trial_expiry.py`); se ainda assim ficar depois da parada, o efeito é
    calar o aviso por mais tempo, nunca celebrar sem motivo; o banco que parou antes do período comparado não cala), só
    a partir do dia `SOBRA_DESDE_DIA` do mês seguinte (P7) e algum lançamento nos primeiros `SOBRA_COBERTURA_DIAS` dias
    do mês base (P8). Fica no feed o resto do mês seguinte (P2), como `info`, sem `impacto` nem `acoes` (P3), por último
    no laço (Q4b). O Repórter chama de sobra outra conta (P4, issue #1360). Gate: o de `insights`; `comparar_mes`
    exige `financial_comparison`, o mesmo tier."""
    from datetime import timedelta

    from core.services.comparacao_periodos import MESES, comparar_mes
    from core.services.previsao_variavel import _inicio_do_historico
    from db.connection import get_conn
    from db.open_finance_state import _TERMINAL
    agora = now_tz()
    if day_tz(agora).day < SOBRA_DESDE_DIA:  # P7: antes disso o mês fechado ainda pode receber lançamento atrasado
        return []
    ini = (day_tz(agora).replace(day=1) - timedelta(days=1)).replace(day=1)  # 1º dia do mês fechado
    c = comparar_mes(user_id, ini.year, ini.month, agora)
    if c is None or c["motivo"] in ("inicio_do_historico", "sem_base") or c["motivos"]:
        return []
    sobra, ant = c["resultado"]["total_atual"], c["resultado"]["total_anterior"]
    if not (sobra > 0 and sobra > ant):
        return []
    ini_ant = c["anterior"]["inicio"]
    with get_conn() as conn, conn.cursor() as cur:
        primeiro = _inicio_do_historico(cur, user_id)
        no_comeco = _inicio_do_historico(cur, user_id, ini_ant)
        # P6/P10/P11: data APROXIMADA da parada, o `updated_at` da conexão terminal. Os escritores do terminal
        # (pausa por plano e webhook `item/deleted`) gravam `now()`, e sync não toca linha terminal: nunca fica antes
        # da parada; a varredura não seleciona DELETED e a pausa não regrava terminal (#1371). Se errar, só cala a mais.
        # A reconexão pelo widget reativa a linha (status remoto, nunca PAUSED/DELETED), que sai do conjunto lido.
        cur.execute("select max(updated_at) as d from open_finance_connections"
                    f" where user_id = %s and upper(coalesce(status,'')) in {_TERMINAL}", (user_id,))
        parou_em = cur.fetchone()["d"]
    parou_no_periodo = parou_em is not None and day_tz(parou_em) >= ini_ant  # o dia 1 do mês anterior, inclusive
    if primeiro is None or primeiro > ini_ant:  # o mês anterior não está inteiro no histórico do usuário
        return []
    if no_comeco is None or no_comeco > ini_ant + timedelta(days=SOBRA_COBERTURA_DIAS - 1) or parou_no_periodo:
        return []
    nome_ant = MESES[ini_ant.month - 1]
    base = (f"{fmt_brl(sobra - ant)} a mais que {nome_ant}." if ant > 0 else
            f"{nome_ant.capitalize()} tinha fechado zerado." if ant == 0 else
            f"{nome_ant.capitalize()} tinha fechado com {fmt_brl(-ant)} no negativo.")
    return [montar_item(
        "positive_result", "mes", {**periodo_mes(ini), "parcial": False}, severity="info", icon="🎉",
        title=f"{MESES[ini.month - 1].capitalize()} fechou com {fmt_brl(sobra)} de sobra", message=f"{base} {SOBRA_E}",
        action_label="Ver análises", action_view="analytics",
        referencia={"estatistica": "mes_anterior_inteiro", "valor": ant,
                    "periodo": {**periodo_mes(ini_ant), "parcial": False}},
        atual={"valor": sobra}, diferenca=diferenca(sobra, ant), amostra=None)]
