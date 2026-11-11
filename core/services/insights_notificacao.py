"""Aviso de insight por e-mail (PL-06 B6): quem está liberado, os textos e as constantes do direito.

Só a adesão mora aqui por enquanto; o envio é o PR 2. Os textos são únicos de propósito:
`GET /settings/{uid}/notifications`, o chat e o `/unsubscribe` os pedem daqui e as telas
não guardam cópia.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

logger = logging.getLogger(__name__)

# Hora local do envio diário. O laço do PR 2 lê a mesma constante, e o texto da adesão também.
HORA_ENVIO = 10

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
        'O link "Cancelar inscrição" de qualquer e-mail corta todos os e-mails opcionais do Piggy: dicas '
        "de uso, Insights financeiros, estes avisos e os e-mails dos agentes. E-mails de segurança e de "
        "cobrança continuam chegando."
    )


def liberado(user_id: int) -> bool:
    """`INSIGHT_EMAIL_USERS`, lida a cada chamada: vazia ou ausente = ninguém, ids separados por
    vírgula comparados como texto (`"01"` não é o 1). `*` = todos só com `AVISO_ABERTO_A_TODOS`.

    A mesma env esconde a tela e, no PR 2, segura o envio: ninguém adere a algo que não sai.
    """
    ids = {token.strip() for token in (os.getenv("INSIGHT_EMAIL_USERS") or "").split(",")}
    return ("*" in ids and AVISO_ABERTO_A_TODOS) or str(int(user_id)) in ids


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

