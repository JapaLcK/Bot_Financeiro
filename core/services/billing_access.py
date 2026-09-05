"""
core/services/billing_access.py — a projeção de `plan_grants` em `auth_accounts`.

`auth_accounts.plan`/`plan_expires_at` continuam sendo o modelo de LEITURA de
todo o app (nenhum dos ~30 leitores muda). O que muda é quem os escreve: em vez
de cada ramo do webhook gravar o par na mão, os ramos passam a registrar um
GRANT e esta função projeta o estado a partir da lista de grants.

Plano: docs/plano_pix_anual_asaas.md §4.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db.connection import get_conn
from db.plan_grants import list_grants, users_com_grant_na_janela

# §4.1 — dois grants nossos encostados podem ter alguns segundos de folga entre
# `ends_at` de um e `starts_at` do outro. 120 s porque todo encadeamento que NÓS
# geramos é exato; buraco real (revogação antecipada, estorno do meio) é sempre
# de DIAS, e a tolerância não o emenda.
# ponytail: constante fixa, não env. Vira parâmetro no dia em que existir
# produtor de grants com data arredondada em dia.
GAP_TOLERANCIA = timedelta(seconds=120)


def _tier_do_stored(plan_stored: str) -> int:
    """Posição do valor legado da coluna `plan` na escada de tiers.

    Importa a tabela de core/services/plan_service em vez de reescrevê-la: ela é
    a fonte de verdade da escada (CLAUDE.md §0.7), e uma segunda cópia aqui
    seria a que ninguém lembra de atualizar quando um tier novo entrar.
    """
    from core.services.plan_limits import TIER_ORDER
    from core.services.plan_service import _STORED_PLAN_TO_TIER

    return TIER_ORDER.get(_STORED_PLAN_TO_TIER.get((plan_stored or "").lower(), "free"), 0)


def projetar_grants(grants: list[dict], agora: datetime) -> tuple[str, datetime | None]:
    """FUNÇÃO PURA: lista de grants + instante → o par (plan, plan_expires_at).

    Cobertura contígua a partir de AGORA:
      - sem nenhum grant vigente agora → ('free', None). Grant FUTURO não conta:
        acesso que ainda não começou não é acesso.
      - com vigente: a cobertura vai até o maior `ends_at` entre os vigentes, e
        depois estica pelos grants futuros ENQUANTO eles emendarem (dentro da
        GAP_TOLERANCIA). O primeiro buraco encerra a cobertura — é o que faz
        estorno do meio e revogação antecipada aparecerem na data.
      - o plano é o MAIOR tier entre os grants que cobrem agora (o downgrade
        agendado só vira tier quando o grant de tier maior deixa de ser vigente).
    """
    ativos = sorted(
        (g for g in grants if g["status"] == "active" and g["ends_at"] > agora),
        key=lambda g: g["starts_at"],
    )
    vigentes = [g for g in ativos if g["starts_at"] <= agora]
    if not vigentes:
        return "free", None

    cobertura = max(g["ends_at"] for g in vigentes)
    for g in ativos:
        if g["starts_at"] <= agora:
            continue
        if g["starts_at"] > cobertura + GAP_TOLERANCIA:
            break                                   # primeiro buraco
        cobertura = max(cobertura, g["ends_at"])

    plano = max(vigentes, key=lambda g: _tier_do_stored(g["plan_stored"]))["plan_stored"]
    return plano, cobertura


def _ler_conta(user_id: int) -> dict | None:
    """SELECT enxuto de propósito — NÃO passa por `get_auth_user`.

    Aquele traz PII cifrada, chama `decrypt_pii_optional` e registra em
    `pii_access_log`; a projeção roda em todo webhook de cobrança e a cada
    passada do loop de 60 s, então usá-lo duplicaria a auditoria de PII por
    evento de billing. Mesmo motivo do `get_onboarding_state` (db/reports.py).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select plan, plan_expires_at, last_payment_status, stripe_customer_id"
                "  from auth_accounts where user_id = %s",
                (int(user_id),),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def _e_reducao(plano_atual: str, expira_atual, plano_novo: str, expira_novo) -> bool:
    """A projeção nova TIRA acesso do que a conta tem hoje?

    Reduzir é virar `free` estando pago, ou encurtar a validade. Subir de tier,
    esticar a data ou repetir o mesmo valor não é redução e nunca precisa de
    confirmação — o caminho caro só existe para o que causa dano.
    """
    if plano_atual == "free":
        return False
    if plano_novo == "free":
        return True
    if not (expira_atual and expira_novo):
        return False
    # `- GAP_TOLERANCIA`: `current_period_end` do Stripe tem precisão de
    # SEGUNDOS, então o reparo grava um `ends_at` truncado e a comparação com o
    # `plan_expires_at` que estava em memória acusava uma "redução" de frações
    # de segundo. Medido: o reparo do 9c era classificado como redução e a conta
    # ficava sem escrita nenhuma. Diferença menor que a folga já usada para
    # emendar grants não é mudança de cobertura.
    return expira_novo < expira_atual - GAP_TOLERANCIA


def _reparar_grant_pelo_stripe(user_id: int, conta: dict, grants: list[dict]) -> str:
    """Confirma no STRIPE uma redução que veio de VARREDURA (§4.1.1 B).

    Devolve 'reduz' | 'reparou' | 'nao_reduz' | 'indisponivel'.

    **A MATRIZ DO PLANO (§4.1.1 D), célula por célula.** Terceira rodada seguida
    em que o conserto abriu defeito neste trecho, e o `CLAUDE.md` §4 manda
    enumerar em vez de remendar. Esta tabela é o CONTRATO — o código implementa
    exatamente estas células, e teste que não casar com uma delas está errado ou
    é célula nova. Ordem de avaliação segue a tabela.

    | # | assinatura no Stripe                       | grant ATIVO                        | veredito     |
    |---|--------------------------------------------|------------------------------------|--------------|
    | 1 | conta sem `stripe_customer_id`             | qualquer                           | reduz        |
    | 2 | consulta falhou (rede/SDK/`StripeLookupError`) | qualquer                        | indisponivel |
    | 3 | nenhuma ativa, **ou `past_due`**           | qualquer                           | reduz        |
    | 4 | viva, sem `id` **ou** sem `current_period_end` | qualquer                        | indisponivel |
    | 5 | `active`/`trialing`                        | nenhum (inclusive `legacy` revogado) | nao_reduz  |
    | 6 | `active`/`trialing`                        | da `sub_id` **ou** `legacy`, `ends_at >= fim` | nao_reduz |
    | 7 | `active`/`trialing`                        | da `sub_id` **ou** `legacy`, `ends_at < fim`  | reparou   |

    As células 6 e 7 valem igual para o grant da `sub_id` e para o `legacy` —
    o que muda entre eles é só a ordem de escolha do alvo, não a regra.

    Duas invariantes que a matriz protege, e que valem para qualquer célula nova:

    **1. O reparo nunca ressuscita.** Só grant `status='active'` pode ser alvo.
    `revoked` é decisão registrada, não defasagem — e só um EVENTO a desfaz.
    `list_grants` devolve os revogados de propósito (a projeção precisa deles
    para separar "sem informação" de "venceu"), e o `upsert_grant` grava
    `status='active'` zerando `revoked_reason`: sem o filtro, o reparo reanimava
    o `legacy` que a supersessão matou, com o tier ANTERIOR à supersessão. Era
    o D4 voltando pela porta do reparo, e a guarda de versão não segurava
    (o reparo carimba `now()`, sempre maior).

    **2. O reparo nunca escolhe tier.** Só a DATA se move.

    Reparar significa ESTICAR o `ends_at` do grant até o período que o Stripe
    diz. `plan_stored` **não** se mexe — mas o motivo não é "é a mesma
    assinatura, então é o mesmo tier": isso é FALSO neste repo, porque
    `/billing/change-plan` troca o price por `SubscriptionSchedule` mantendo o
    mesmo `sub_id`. O motivo real é mais estreito: aqui não há como resolver
    price→plano sem duplicar o `_stored_plan_for_price` do monólito, e um
    reparo que chuta tier pode CONCEDER tier que ninguém comprou. O tier de
    registro continua sendo o que o último EVENTO escreveu, e o próximo
    `invoice.paid` o corrige — a data é o que não pode esperar.
    """
    customer = (conta.get("stripe_customer_id") or "").strip()
    if not customer:
        return "reduz"          # CÉLULA 1: sem relação com o gateway a confirmar

    # Reuso dos helpers do monólito (`CLAUDE.md` §0.1/§0.7): a escada
    # active > trialing > past_due e a leitura do `current_period_end` já vivem
    # lá, testadas. Import tardio porque o monólito importa ESTE módulo.
    # ponytail: se um segundo serviço precisar do mesmo, o upgrade é extrair
    # para `core/services/stripe_lookup.py` — hoje seria refatoração sem pedido.
    import stripe as _stripe
    from frontend.finance_bot_websocket_custom import (  # noqa: PLC0415
        StripeLookupError, _find_active_subscription, _sg, _sub_period_end_ts,
    )
    try:
        sub = _find_active_subscription(_stripe, customer)
    except StripeLookupError:
        return "indisponivel"                                          # CÉLULA 2
    except Exception:
        # Qualquer falha de rede/SDK é indisponibilidade, nunca "não tem".
        return "indisponivel"                                          # CÉLULA 2

    if sub is None:
        return "reduz"                                                 # CÉLULA 3

    # `past_due` também é CÉLULA 3, e sai aqui de propósito — antes de o `fim`
    # sequer ser calculado.
    #
    # A pergunta "o que fazer com `past_due`" NÃO existia antes deste PR: o
    # `invoice.payment_failed` só grava `last_payment_status='past_due'` e manda
    # e-mail, sem tocar em `plan`/`plan_expires_at`. O comportamento sempre foi
    # "acesso até o fim do período efetivamente pago, e depois acaba" — quem
    # criou a pergunta foi a varredura que este PR introduziu, e a resposta
    # certa é não mudar nada: `past_due` vencida rebaixa como qualquer outra.
    #
    # Ela não pode cair no reparo lá embaixo porque `_find_active_subscription`
    # a considera viva e o `current_period_end` dela é FUTURO — a Stripe fixa o
    # período ao CRIAR a fatura, não quando o pagamento entra. Esticar o grant
    # por esse `fim` daria acesso a um período que NINGUÉM PAGOU.
    if (_sg(sub, "status") or "").strip().lower() == "past_due":
        return "reduz"                                                 # CÉLULA 3

    sub_id = sub["id"] if not isinstance(sub, dict) else sub.get("id")
    ts = _sub_period_end_ts(sub)
    if not (sub_id and ts):
        return "indisponivel"   # CÉLULA 4: viva sem data legível nunca vira "não tem"
    fim = datetime.fromtimestamp(int(ts), tz=timezone.utc)

    # Alvo do reparo: grant ATIVO daquela assinatura; na falta dele, o `legacy`
    # ATIVO (a reconstrução do mesmo acesso de cartão feita pelo backfill).
    # `status == "active"` é a invariante 1 — ver docstring.
    #
    def _ativo(g) -> bool:
        return g["status"] == "active"

    alvo = next((g for g in grants
                 if _ativo(g) and g["source"] == "stripe"
                 and g["external_ref"] == str(sub_id)), None)
    if alvo is None:
        alvo = next((g for g in grants if _ativo(g) and g["source"] == "legacy"), None)

    if alvo is None:
        # CÉLULA 5 — nenhum grant ATIVO a esticar (inclusive `legacy` revogado).
        # Não reduz: quem tem assinatura viva não perde acesso por falta de linha
        # nossa. Alerta, porque é anomalia de materialização e precisa de gente.
        return "nao_reduz"

    if alvo["ends_at"] >= fim:
        # CÉLULA 6 — o grant ativo (da `sub_id` OU o `legacy`) já cobre o que o
        # Stripe promete. Não escreve; a redução não sai daqui.
        return "nao_reduz"

    from db.plan_grants import upsert_grant

    # CÉLULA 7 — estica SÓ o `ends_at`. `plan_stored` vai igual ao que já
    # estava (invariante 2): `/billing/change-plan` troca o price mantendo o
    # mesmo `sub_id`, então inferir plano do price aqui concederia tier que
    # ninguém comprou. O tier continua sendo o que o último EVENTO escreveu.
    upsert_grant(user_id, alvo["source"], alvo["external_ref"], alvo["plan_stored"],
                 alvo["starts_at"], fim,
                 int(datetime.now(timezone.utc).timestamp()), "reparo:varredura")
    return "reparou"


def recompute_entitlement(user_id: int, *, origem: str = "evento") -> dict | None:
    """Reprojeta `auth_accounts` a partir dos grants. Devolve o par escrito, ou
    None quando decidiu NÃO escrever.

    Três saídas antecipadas, e cada uma protege um caso que o modelo de grants
    não sabe representar:

    1. `grandfathered` — vitalício por decisão, não tem `ends_at` a inventar.
    2. plano pago com `plan_expires_at is null` — vitalício DE FATO, mesmo sem o
       status. Sem esta saída, o `None` viraria "venceu" e a conta cairia.
    3. **usuário sem NENHUM grant** — ausência de grant é DESCONHECIMENTO, não
       ausência de direito. É a propriedade que torna este PR reversível: o
       código novo nunca destrói os valores de que o código antigo depende.
       Quem TEM grants e todos venceram é rebaixado normalmente — aí há
       informação, e a guarda não pode virar "nunca rebaixa".

    `origem` decide quem pode REDUZIR acesso (§4.1.1 B):

    • `"evento"` — o webhook é a autoridade e chegou agora; reduz direto.
    • `"varredura"` — não há autoridade nenhuma, só o relógio. Antes de tirar
      acesso, CONSULTA O STRIPE: sem assinatura ativa reduz; assinatura viva
      mais longa que o grant repara o grant e mantém o acesso; Stripe fora do ar
      não escreve nada, loga e alerta.
    """
    conta = _ler_conta(user_id)
    if not conta:
        return None

    if (conta.get("last_payment_status") or "").lower() == "grandfathered":
        return None

    plano_atual = (conta.get("plan") or "free").lower()
    expira_atual = conta.get("plan_expires_at")
    if plano_atual != "free" and expira_atual is None:
        return None

    agora = datetime.now(timezone.utc)
    grants = list_grants(user_id)
    if not grants:
        pago_e_vigente = plano_atual != "free" and expira_atual is not None and expira_atual > agora
        if pago_e_vigente:
            _alertar_sem_grants(user_id, plano_atual, expira_atual)
        return None

    plano, expira = projetar_grants(grants, agora)

    if origem == "varredura" and _e_reducao(plano_atual, expira_atual, plano, expira):
        veredito = _reparar_grant_pelo_stripe(user_id, conta, grants)
        if veredito in ("indisponivel", "nao_reduz"):
            _alertar_reducao_nao_confirmada(user_id, plano_atual, veredito)
            return None                      # falha na direção reparável
        if veredito == "reparou":
            grants = list_grants(user_id)
            plano, expira = projetar_grants(grants, agora)
            if _e_reducao(plano_atual, expira_atual, plano, expira):
                # o reparo não resolveu: não reduz por conta própria
                _alertar_reducao_nao_confirmada(user_id, plano_atual, "reparo_insuficiente")
                return None

    from db import set_payment_status, update_user_plan

    update_user_plan(user_id, plano, expira)

    # Grant Pix vigente agora manda no status: a assinatura do cartão pode estar
    # 'canceled' e o acesso continuar pago pelo Pix. Sem grant Pix o status do
    # Stripe é preservado — quem manda nele continua sendo o webhook do cartão.
    if any(g["source"] == "pix" and g["status"] == "active"
           and g["starts_at"] <= agora < g["ends_at"] for g in grants):
        set_payment_status(user_id, "active")

    return {"plan": plano, "plan_expires_at": expira}


def _alertar_reducao_nao_confirmada(user_id: int, plano: str, motivo: str) -> None:
    """Varredura quis rebaixar e o Stripe não confirmou. Não escreve, avisa.

    Errar aqui na direção "não reduz" é reparável: o acesso segue e a próxima
    passada tenta de novo. Errar na direção "reduz" tira o produto de quem
    pagou por causa de um timeout de rede.
    """
    _observar(user_id, "projecao_reducao_nao_confirmada",
              f"Reducao de acesso nao confirmada no Stripe (plano {plano}, motivo {motivo}).",
              f"Conta {user_id} ({plano}): varredura quis rebaixar, Stripe nao confirmou "
              f"({motivo}).")


def _observar(user_id: int, evento: str, mensagem: str, alerta: str) -> None:
    """Loga sempre; alerta no máximo 1x/dia por usuário e evento.

    Falha silenciosa: observabilidade nunca derruba cobrança.
    """
    try:
        from core.observability import log_system_event_sync, recent_event_exists

        log_system_event_sync("warning", evento, mensagem,
                              source="billing", user_id=int(user_id))
        if not recent_event_exists(f"{evento}_alertado", int(user_id), within_days=1.0):
            from core.services.admin_notify import _send

            _send(alerta)
            log_system_event_sync("info", f"{evento}_alertado", "Alerta enviado.",
                                  source="billing", user_id=int(user_id))
    except Exception as exc:                                  # pragma: no cover
        print(f"[billing_access] observabilidade falhou user={user_id}: {exc}")


def _alertar_sem_grants(user_id: int, plano: str, expira) -> None:
    """Conta paga e vigente sem um grant sequer: ou o resync não rodou, ou algo
    apagou os grants. Loga sempre e alerta no máximo 1x/dia por usuário.

    Falha silenciosa: observabilidade nunca derruba o fluxo de cobrança.
    """
    try:
        from core.observability import log_system_event_sync, recent_event_exists

        log_system_event_sync(
            "warning",
            "projecao_sem_grants",
            "Conta paga e vigente sem nenhum grant; projecao nao escreveu nada.",
            source="billing",
            user_id=int(user_id),
            details={"plan": plano, "plan_expires_at": expira.isoformat() if expira else None},
        )
        if not recent_event_exists("projecao_sem_grants_alertado", int(user_id), within_days=1.0):
            from core.services.admin_notify import _send

            _send(f"Conta {user_id} paga ({plano}) e vigente sem nenhum plan_grant.")
            log_system_event_sync(
                "info", "projecao_sem_grants_alertado", "Alerta enviado.",
                source="billing", user_id=int(user_id),
            )
    except Exception as exc:                                  # pragma: no cover
        print(f"[billing_access] alerta projecao_sem_grants falhou user={user_id}: {exc}")


def reprojetar_grants_recentes(desde: datetime | None) -> int:
    """Reprojeta quem teve grant COMEÇANDO ou TERMINANDO desde `desde` (§4.3).

    São as duas únicas transições de acesso que acontecem sem nenhum evento
    externo — grant futuro que vira vigente e grant vigente que vence. Sem isto,
    um downgrade agendado só apareceria no próximo webhook do usuário.

    `desde=None` reprojeta TODOS os usuários com grant ativo: é a varredura
    diária, e é auto-curativa (não depende de o processo ter estado no ar).

    Devolve quantos usuários foram reprojetados.
    """
    uids = users_com_grant_na_janela(desde)
    for uid in uids:
        try:
            # `origem="varredura"`: aqui não há evento nenhum, só o relógio —
            # nenhuma redução de acesso sai daqui sem o Stripe confirmar.
            recompute_entitlement(uid, origem="varredura")
        except Exception as exc:                              # pragma: no cover
            print(f"[billing_access] reprojecao falhou user={uid}: {exc}")
    return len(uids)
