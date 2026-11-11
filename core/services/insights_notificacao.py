"""Aviso de insight por e-mail (PL-06 B6): quem está liberado, os textos, as constantes do direito e o envio.

Os textos são únicos de propósito: `GET /settings/{uid}/notifications`, o chat, o `/unsubscribe` e o
e-mail os pedem daqui e as telas não guardam cópia. O envio (`run_insight_emails_once`, §17 do plano B6):
rodada diária na hora `HORA_ENVIO` do fuso do app, com claim do dia; depois, até `FIM_ENVIO`, só as
chaves de e-mails montados que não saíram (novidade depois do corte fica para amanhã).
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone

from core.observability import log_system_event_sync
from core.pg_text import causa_segura
from core.services.insights_contrato import GRAVIDADE, LIMITE, chave, piorou
from utils_date import _tz

logger = logging.getLogger(__name__)

# Hora local do envio diário. O laço lê a mesma constante, e o texto da adesão também.
HORA_ENVIO = 10
FIM_ENVIO = 21    # a partir desta hora, nenhuma rodada; pendência que sobrar morre no claim de amanhã
INTERVALO_S = 900  # 15 min: com 1 h, a deriva do `sleep` depois do trabalho poderia pular a hora inteira
ASSUNTO = "O Piggy notou algo no seu mês"  # fixo: também é o `log_subject`, sem dado pessoal
RODAPE_CANCELAR = "Cancelar inscrição de todos os e-mails opcionais do Piggy"
# O alcance do link de descadastro: a linha de adesão e o corpo do e-mail dizem o mesmo, desta constante.
ALCANCE_CANCELAR = ('O link "Cancelar inscrição" de qualquer e-mail corta todos os e-mails opcionais do Piggy: dicas '
                    "de uso, Insights financeiros, estes avisos e os e-mails dos agentes. E-mails de segurança e de "
                    "cobrança continuam chegando.")

# Quanto tempo a adesão sobrevive a uma lacuna de direito (P9 (b) do dono). Só preserva a ADESÃO:
# o portão de envio (`db.reports.insight_email_permitido`) exige o direito real, sem margem.
# A renovação de cartão é gravada pela varredura DEPOIS do vencimento; sem margem, todo assinante
# perderia a adesão todo mês. O gatilho do banco recebe este valor do `init_db`.
MARGEM_DIREITO = timedelta(hours=1)

# Trava do app nativo (§2.12 do plano): enquanto falsa, `*` não libera ninguém; só ids listados.
# O app publicado retira a adesão em silêncio ao desligar "Insights financeiros" até o PR 3 sair.
# Virar é commit próprio, depois do laço do PR 2 e do build na loja, com autorização do dono;
# `tests/test_insights_adesao_direito.py` fica vermelho se ela virar antes.
AVISO_ABERTO_A_TODOS = False

_NOME_DO_FUSO = {"America/Sao_Paulo": "horário de Brasília"}

ORIENTACAO = ("Os Avisos do Piggy por e-mail não voltam sozinhos: para recebê-los, "
              "ligue de novo em Configurações → Notificações.")
# Adesão gravada, plano fora (carência): o servidor manda este texto e as duas telas o mostram.
AVISO_PAUSADO = "Pausado até o pagamento se resolver. Se o plano acabar, será preciso ligar de novo."
# O estado "cortado com adesão" do `/settings` (D2): a cópia no HTML é comparada com esta pelo teste.
_HORAS = int(MARGEM_DIREITO.total_seconds() // 3600)
AVISO_SEM_PLANO = (f"Se o pagamento restabelecer seu plano em até {_HORAS} hora{'s' if _HORAS > 1 else ''} após a "
                   "perda do acesso, os avisos continuam; depois disso, será preciso ligar de novo.")
AVISO_AO_DESLIGAR_INSIGHTS = ("Desligar Insights financeiros também desliga os Avisos do Piggy por e-mail. "
                              "Para voltar, ligue os dois de novo.")


def texto_adesao() -> str:
    """O texto da linha de adesão, com a hora e o fuso que o envio usa (lidos a cada chamada)."""
    from utils_date import tz_name

    fuso = tz_name()
    fuso = _NOME_DO_FUSO.get(fuso, fuso)
    return (
        "Opcional: você só recebe se ligar aqui. No máximo um e-mail por dia, programado para as "
        f"{HORA_ENVIO}h ({fuso}), somente quando houver novidade: orçamento estourando, gasto fora do "
        "normal, conta a vencer, mês que pode fechar no vermelho. Para parar só este aviso, desligue aqui. "
        + ALCANCE_CANCELAR
    )


def liberado(user_id: int) -> bool:
    """`INSIGHT_EMAIL_USERS`, lida a cada chamada: vazia ou ausente = ninguém, ids separados por
    vírgula comparados como texto (`"01"` não é o 1). `*` = todos só com `AVISO_ABERTO_A_TODOS`.

    A mesma env esconde a tela e segura o envio: ninguém adere a algo que não sai.
    """
    ids = _ids_liberados()
    return ("*" in ids and AVISO_ABERTO_A_TODOS) or str(int(user_id)) in ids


def _ids_liberados() -> set[str]:
    return {token.strip() for token in (os.getenv("INSIGHT_EMAIL_USERS") or "").split(",")} - {""}


def orientacao_aviso(user_id: int) -> str:
    """A orientação de religar, só para quem consegue ligar o interruptor agora. Erro de leitura: ""."""
    try:
        from core.services.plan_service import plan_gate_ok

        return ORIENTACAO if liberado(user_id) and plan_gate_ok(user_id, "insights") else ""
    except Exception as exc:  # noqa: BLE001 — a orientação é acessória; a resposta sai sem ela
        logger.warning("[insight-email] orientacao user=%s falhou causa=%s", user_id, type(exc).__name__,
                       extra={"user_id": user_id})
        return ""


def planos_com_insights() -> list[str]:
    """Os valores de `auth_accounts.plan` cujo tier tem `insights` (fonte: plan_service), para o gatilho."""
    from core.services.plan_limits import tier_at_least
    from core.services.plan_service import _STORED_PLAN_TO_TIER, FEATURE_MIN_TIER_V2

    return sorted(p for p, tier in _STORED_PLAN_TO_TIER.items()
                  if tier_at_least(tier, FEATURE_MIN_TIER_V2["insights"]))


# ── Envio (PR 2) ─────────────────────────────────────────────────────────────────────────────────────────

def carteiro_manda_email(user_id: int) -> bool:
    """O Carteiro vai falar das contas em D-3 e D-0 por e-mail: ativo, com e-mail ligado e com direito a agentes.
    Cobertura PRESUMIDA pela previsão, não entrega confirmada: se o e-mail dele falhar, a B6 não reenvia (limite, §20)."""
    from core.services import piggy_agents
    from core.services.plan_service import agent_kind_allowed, agents_ui_enabled
    from db.agents import get_agent

    if not piggy_agents.AGENTS_ENABLED:
        return False
    agente = get_agent(user_id, "carteiro")
    return bool(agente and agente["status"] == "active" and (agente.get("config") or {}).get("email_enabled", True)
                is not False and agents_ui_enabled(user_id) and agent_kind_allowed(user_id, "carteiro"))


def chaves_cobertas(user_id: int, dia, itens: list[dict]) -> set[str]:
    """`bill_due` que outro canal cobriu para este usuário hoje (§2.2): o WhatsApp pela evidência
    (`reminder_last_sent_on = dia`), o Carteiro pela previsão (D-3 e D-0, os dias em que ele fala)."""
    contas = [it for it in itens if it.get("type") == "bill_due"]
    if not contas:
        return set()
    from db.bills import contas_lembradas_em

    # A mesma construção do detector (`insights_detectores.detect_bill_due`): vencida tem sujeito próprio.
    cobertas = {chave("bill_due", f"{c['id']}:vencida" if c["due_date"] < dia else c["id"], c["due_date"].isoformat())
                for c in contas_lembradas_em(user_id, dia)}
    if carteiro_manda_email(user_id):
        cobertas |= {it["key"] for it in contas if it["explicacao"]["atual"]["dias_para_vencer"] in (3, 0)}
    return cobertas


def a_enviar(itens: list[dict], estados: dict[str, dict], cobertas: set[str]) -> tuple[list[dict], list[dict]]:
    """(novos, avisados): o que vai por e-mail e o que outro canal cobriu (§2.3). Só warning e critical, com
    linha em `insight_estado`; dispensado só se piorou (a régua do painel, com valor); já avisado só se a
    GRAVIDADE subiu (valor não conta, Q7 (b))."""
    novos: list[dict] = []
    avisados: list[dict] = []
    vistas: set[str] = set()
    for it in itens:
        e = estados.get(it.get("key"))
        if it.get("severity") not in ("warning", "critical") or e is None or it["key"] in vistas:
            continue
        vistas.add(it["key"])
        if e["dispensado_em"] is not None and not piorou(it, e["dispensado_severidade"], e["dispensado_valor"]):
            continue
        if e["notificado_em"] is not None and GRAVIDADE[it["severity"]] <= GRAVIDADE.get(e["notificado_severidade"], -1):
            continue
        (avisados if it["key"] in cobertas else novos).append(it)
    return novos, avisados


def compor(itens: list[dict]) -> tuple[str, str]:
    """(html, texto) do corpo: título e mensagem do feed, na ordem do feed, escapados no html. Nunca `impacto`/`acoes`."""
    from core.services.email_service import _public_base_url

    link = f"{_public_base_url()}/settings?view=notifications"
    corpo = "".join(f"<p><strong>{html.escape(it['title'])}</strong><br/>{html.escape(it['message'])}</p>"
                    for it in itens)
    rodape = (f'<p><a href="{html.escape(link)}">Gerenciar estes avisos</a></p>'
              f'<p class="sig">{html.escape(ALCANCE_CANCELAR)}</p>')
    texto = "\n\n".join(f"{it['title']}\n{it['message']}" for it in itens)
    return corpo + rodape, f"{texto}\n\nGerenciar estes avisos: {link}\n\n{ALCANCE_CANCELAR}"


def _rodada(local: datetime) -> str | None:
    if local.hour == HORA_ENVIO:
        return "diaria"
    if HORA_ENVIO < local.hour < FIM_ENVIO and local.minute < 15:
        return "pendentes"
    return None


def run_insight_emails_once(now: datetime | None = None) -> dict | None:
    """Uma volta do envio. Enumerar, filtrar e enviar ficam NESTA função (`tests/test_portao_lacos_proativos.py`).

    Por usuário, num `try`: `liberado`, `filtrar_por_acesso` e `insight_email_permitido` antes do claim (ou de
    pegar a pendência); insights frescos (`force`); dedupe por gravidade; o que o WhatsApp ou o Carteiro cobriu
    é marcado ANTES de tentar o e-mail, em todo caminho (§19); portão de novo antes de enviar. Falha antes de
    montar devolve o dia (ou a pendência); envio falho guarda as chaves. O que vai por e-mail só é marcado depois
    de sair: o pior caso é um dia de atraso ou um e-mail repetido (§2.4).
    """
    if not _ids_liberados():
        return None  # env vazia: nem consulta o banco
    local = (now or datetime.now(timezone.utc)).astimezone(_tz())
    rodada = _rodada(local)
    if rodada is None:
        return None

    from contextlib import suppress

    from core.ai_patterns import GeracaoIncompleta, generate_ai_insights
    from core.reports.reports_daily import filtrar_por_acesso
    from core.services.email_service import send_agent_report_email
    from db import insight_estado as est
    from db.privacy import get_user_email
    from db.reports import insight_email_permitido

    dia, diaria = local.date(), rodada == "diaria"
    conta = dict.fromkeys(("enviados", "sem_novidade", "cobertos", "bloqueados", "pendentes", "falhas"), 0)
    usuarios = est.list_users_for_insight_email(dia) if diaria else est.list_users_insight_email_pendente(dia)
    for uid in usuarios:
        try:
            if not (liberado(uid) and filtrar_por_acesso([uid]) and insight_email_permitido(uid)):
                continue  # nada consumido: o dia (ou a pendência) segue de quem ainda pode receber
            so = None
            if diaria:
                if not est.reivindicar_dia(uid, dia):
                    continue
            else:
                so = set(est.pegar_pendente(uid, dia))
                if not so:
                    continue
            try:
                # A lista inteira, sem o corte do feed: o dedupe vem antes do corte (§19, achado 3). Detector em
                # falha é falha da geração (achado 2): nada é enviado com a lista parcial.
                incompleta = None
                try:
                    itens = generate_ai_insights(uid, force=True, estrito=True, sem_corte=True)
                except GeracaoIncompleta as exc:
                    itens, incompleta = exc.itens, exc
                if so is not None:  # a pendência não incorpora fato posterior ao corte das 10h
                    itens = [it for it in itens if it.get("key") in so]
                estados = est.estado_de_envio(uid, [it["key"] for it in itens])
                novos, avisados = a_enviar(itens, estados, chaves_cobertas(uid, dia, itens))
                # O outro canal JÁ falou: a cobertura vale com ou sem e-mail, e a falha do Resend (ou de um
                # detector) não a desfaz (§19).
                if avisados:
                    est.marcar_notificados(uid, avisados)
                if incompleta is not None:
                    raise incompleta
                novos = novos[:LIMITE]  # os de fora não são marcados: saem num dia seguinte
            except Exception:
                with suppress(Exception):  # protegido: a causa que sobe é a da montagem
                    if so is None:
                        est.devolver_dia(uid, dia)
                    else:
                        est.guardar_pendente(uid, dia, sorted(so))
                raise
            conta["cobertos"] += len(avisados)
            if not novos:
                conta["sem_novidade"] += 1
                continue
            email = get_user_email(uid) if liberado(uid) and insight_email_permitido(uid) else None
            if not email:  # desistiu, cancelou, pediu exclusão ou perdeu o plano durante a volta
                conta["bloqueados"] += 1
                continue
            corpo, texto = compor(novos)
            chaves = [it["key"] for it in novos]
            try:
                ok = send_agent_report_email(email, uid, ASSUNTO, corpo, text_content=texto, log_subject=ASSUNTO,
                                             rotulo_cancelar=RODAPE_CANCELAR)
            except Exception:
                est.guardar_pendente(uid, dia, chaves)
                raise
            if not ok:
                est.guardar_pendente(uid, dia, chaves)
                conta["pendentes"] += 1
                continue
            est.marcar_notificados(uid, novos)
            conta["enviados"] += 1
        except Exception as exc:  # um usuário não segura a fila: sem `break`
            conta["falhas"] += 1
            logger.warning("[insight-email] user=%s falhou causa=%s", uid, causa_segura(exc), extra={"user_id": uid})
    if usuarios:
        log_system_event_sync("info", "insight_email_run", "Volta do aviso de insight por e-mail.",
                              source="insight_email", details={"rodada": rodada, **conta})
    return {"rodada": rodada, **conta}


async def run_insight_email_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_insight_emails_once)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("[insight-email] volta falhou causa=%s", causa_segura(exc))
        try:
            await asyncio.sleep(INTERVALO_S)
        except asyncio.CancelledError:
            break
