#!/usr/bin/env python3
"""Avisa por e-mail quem PERDE o acesso quando a regra de plano entrar no ar.

O corte acontece no MERGE do PR que liga a regra (o Railway faz deploy da
`main`), então este aviso tem de ser mergeado E EXECUTADO antes daquele. Ele é
o que garante que ninguém é cortado sem ter sido avisado — hoje toda a base
Grátis legada passa no gate de escolha de plano, porque `db/schema.py` carimbou
`plan_selected_at = now()` em todas as contas existentes numa migration.

**A população é quem tem acesso HOJE e não terá depois.** Em quatro termos,
todos no `where` de `listar_contas_do_aviso`:

  1. `plan_selected_at is not null` — quem já está barrado pela escolha de
     plano (`plan_service.needs_plan_selection`) não perde nada no corte;
  2. o par (`plan`, `plan_expires_at`) NÃO é plano pago vigente — com
     `plan_expires_at is null` valendo VITALÍCIO, que é o caso que um
     `plan_expires_at > now()` ingênuo poria na lista inteirinha;
  3. o relógio de inadimplência NÃO está na carência de `DUNNING_GRACE_DAYS`;
  4. tem e-mail.

Os termos 2 e 3 são a NEGAÇÃO de `plan_service.tem_direito_hoje`, e a
concordância entre as duas escritas é medida por
`tests/test_aviso_fim_do_gratis.py` — mexeu num lado, o teste fica vermelho.

**A população tem DOIS coortes e a copy do e-mail muda entre eles**: quem está
no Grátis/sem plano, e quem tem assinatura VIVA na Stripe com cartão recusado
além da carência (esse já recebeu falha de pagamento e lembrete — chamá-lo de
"plano Grátis" seria falso). Quem separa é `db.dunning.ciclo_de_atraso_aberto`,
lido no ponto do envio.

**`engagement_opt_out` NÃO entra**, e a razão é uma promessa: quem desliga os
e-mails do Piggy desliga dicas e insights (a coluna é derivada de
`tip_email_opt_out AND insight_email_opt_out`, `db/reports.py`), e o bot lhe
responde que "os emails de segurança continuam normais"
(`core/intent_router.py`). Ninguém consentiu em abrir mão do aviso de fim de
serviço — este e-mail é TRANSACIONAL, como o de falha de pagamento e o de
cancelamento, e por isso também não leva link de descadastro.

**Re-executável sem reenviar, em execuções SEQUENCIAIS**, porque ele roda duas
vezes: na abertura da janela de aviso e na véspera do merge, para pegar quem
venceu no meio. A dedupe é `system_event_logs` via `recent_event_exists`, o
mesmo padrão do lembrete de pagamento — e não coluna nova, que depois
precisaria ser zerada. Quatro limites declarados dela:

  • **duas execuções SIMULTÂNEAS duplicam.** É check-then-act (consulta,
    envia, grava) sem lock e sem unique — não rode duas ao mesmo tempo;
  • `system_event_logs` é purgável: um "Limpar" no painel de admin faz o aviso
    sair repetido (mesmo custo que o lembrete de pagamento já aceita);
  • a dedupe é por `event_type`, NÃO por `(event_type, corte)`. Uma segunda
    rodada com `--corte` diferente **não corrige a data de quem já recebeu** —
    para isso não existe caminho automático, seria e-mail de errata à mão;
  • ela **falha ABERTA nas duas pontas**: `recent_event_exists` devolve False
    em qualquer erro (contrato dele: "melhor mandar duplicado que perder") e
    `log_system_event_sync` é silencioso. Um soluço de banco na segunda rodada
    reenvia para quem já recebeu.

**DRY-RUN MEDE A POPULAÇÃO, NÃO O ENVIO** — não é ensaio geral. Ele não chama
`_email_de` (nenhuma decriptação), não consulta o coorte e não fala com o
Resend, então `PII_ENCRYPTION_KEY` errada, chave de e-mail inválida ou domínio
suspenso são INVISÍVEIS no ensaio e viram `falhas` em massa no `--apply`.

**`falhas > 0`: re-rodar é seguro.** Falha não grava dedupe, então a rodada
seguinte tenta de novo só quem falhou. Mas `send_email` não tem retry nem
backoff: um 429 do Resend numa base grande vira falha em massa de uma vez —
espere, confira o painel do Resend, e só então re-rode.

**Custo: o laço é O(N × custo por candidato) com constante alta**, e cresce com
o quadrado da base no pior caso — `auth_accounts` não tem índice em `user_id`,
então a revalidação de cada candidato é um seq scan, e cada `recent_event_exists`
abre uma conexão nova (`psycopg.connect`, sem pool). Nunca medido em escala:
para base grande, espere minutos, não segundos.

**Varredura de base, e o isolamento por usuário é por LINHA**: a query não tem
`user_id` (é a população inteira, por natureza), então nenhum valor atravessa
de uma linha para outra — o endereço de cada aviso é decifrado no ponto do
envio, a partir da revalidação daquele `user_id`, e nada do lote anterior
sobrevive à iteração.

**`--corte` é recusado fora da faixa [hoje, hoje + MAX_DIAS_ATE_O_CORTE]**, e a
guarda é sobre o VALOR que vai para a copy, não sobre a GRAFIA: `20260924` e
`2026-W39-4` são aceitos porque `date.fromisoformat` resolve os dois para
2026-09-24, que é uma data de corte plausível. O que ela pega é o ano digitado
errado (`9999-12-31`) e o corte no passado — o erro que a dedupe impede de
consertar re-executando.

Uso:
    python -m scripts.aviso_fim_do_gratis --corte 2026-09-24            # dry-run
    python -m scripts.aviso_fim_do_gratis --corte 2026-09-24 --apply    # envia
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone

from core.crypto import PiiAccessContext, decrypt_pii_optional
from core.observability import log_system_event_sync, recent_event_exists
from core.services.billing_dunning import (
    DUNNING_GRACE_DAYS,
    PAST_DUE_PAYMENT_STATUSES,
)
from core.services.email_service import send_free_plan_sunset_email
from core.services.plan_service import _STORED_PLAN_TO_TIER
from db.connection import get_conn
from db.dunning import ciclo_de_atraso_aberto

EVENTO = "free_plan_sunset_notice_sent"

# Teto de distância do corte. Não é regra de produto: é a guarda contra o ano
# digitado errado (`9999-12-31` passava). A janela de aviso real é de dias a
# poucas semanas — se algum dia um corte legítimo precisar de mais, o número
# sobe aqui, à vista de quem revisa.
MAX_DIAS_ATE_O_CORTE = 90

# Janela da dedupe. Tem de ser MAIOR que o intervalo entre as duas execuções
# (abertura da janela de aviso × véspera do merge), senão a segunda reenvia
# para quem já recebeu na primeira.
AVISO_DEDUPE_DAYS = 90.0

# Os valores de `auth_accounts.plan` que são PAGOS, derivados da fonte única
# (§0.7): tudo que `_STORED_PLAN_TO_TIER` não mapeia para 'free'. Uma lista
# literal aqui divergiria no dia em que a escada ganhar um degrau.
PLANOS_PAGOS = sorted(p for p, t in _STORED_PLAN_TO_TIER.items() if t != "free")


def listar_contas_do_aviso(user_id: int | None = None) -> list[dict]:
    """As contas que perdem acesso no corte. Com `user_id`, revalida UMA.

    UMA função para os dois usos de propósito: o funil e a revalidação do ponto
    de envio fazem a MESMA pergunta, e duas cópias do `where` são duas versões
    da mesma regra esperando para divergir (§0.7). O lote é um snapshot e o
    envio leva minutos — quem assinar no meio dele não pode receber "seu acesso
    acaba", que é justamente a mensagem errada para cliente pagante.
    """
    filtro = " and user_id = %s" if user_id is not None else ""
    params: list = [PLANOS_PAGOS, list(PAST_DUE_PAYMENT_STATUSES), DUNNING_GRACE_DAYS]
    if user_id is not None:
        params.append(int(user_id))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select user_id, email, email_enc
                from auth_accounts
                where plan_selected_at is not null
                  and email is not null and email <> ''
                  and not (lower(coalesce(plan, '')) = any(%s)
                           and (plan_expires_at is null
                                or plan_expires_at > now()))
                  and not (past_due_since is not null
                           and lower(coalesce(last_payment_status, '')) = any(%s)
                           and past_due_since > now() - make_interval(days => %s))
                  {filtro}
                order by user_id
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall() or []]


def _email_de(user_id: int, linha: dict) -> str | None:
    """Endereço do aviso, da linha que a revalidação acabou de ler.

    `email_enc` manda; o claro é o caso legado. Uma decriptação por e-mail
    ENVIADO, e não por candidato — mesma escolha de
    `core.services.payment_reminder._resolver_email`, pelas mesmas duas razões:
    minimizar PII decifrada e deixar a trilha de auditoria verdadeira. Não
    reusa aquela função porque o `purpose` dela é o do lembrete de pagamento, e
    `purpose` errado é trilha de auditoria mentindo.
    """
    if linha.get("email_enc"):
        return decrypt_pii_optional(
            linha["email_enc"],
            ctx=PiiAccessContext(
                purpose="send_free_plan_sunset_email",
                actor="system:script",
                subject_user_id=user_id,
                field="email",
            ),
        )
    return linha.get("email")


def _corte_para_email(corte: date) -> datetime:
    """MEIO-DIA UTC, não meia-noite: `email_service._fmt_brl_date` converte para
    BRT (-3) antes de formatar, e 00:00Z vira o DIA ANTERIOR na data do corte."""
    return datetime.combine(corte, time(12), tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corte", required=True,
                    help="data do corte, AAAA-MM-DD (entra na copy do e-mail)")
    ap.add_argument("--apply", action="store_true",
                    help="envia de verdade (sem isso, só reporta — dry-run)")
    args = ap.parse_args()
    # A ÚNICA entrada humana do script, e ela vai direto para a copy de um
    # envio irreversível para a base inteira — a dedupe de AVISO_DEDUPE_DAYS
    # impede consertar re-executando. `date.fromisoformat` aceita muita coisa
    # que não é uma data de corte plausível: `2020-01-01` (passado), `20260924`,
    # `2026-W39-4` (semana ISO), `0001-01-01`, `9999-12-31`. A guarda é uma
    # FAIXA, não um validador: hoje ou depois, e no máximo
    # MAX_DIAS_ATE_O_CORTE à frente — o que pega o ano digitado errado.
    # A confirmação interativa fica de fora de propósito: o dry-run é o padrão
    # e `--apply` já é o gesto deliberado.
    corte_dia = date.fromisoformat(args.corte)
    hoje = date.today()
    if not hoje <= corte_dia <= hoje + timedelta(days=MAX_DIAS_ATE_O_CORTE):
        ap.error(f"--corte {args.corte} fora da faixa: use de hoje ({hoje}) até "
                 f"{hoje + timedelta(days=MAX_DIAS_ATE_O_CORTE)}.")
    corte = _corte_para_email(corte_dia)

    candidatos = listar_contas_do_aviso()
    enviados = ja_avisados = desistidos = falhas = 0
    for linha in candidatos:
        uid = int(linha["user_id"])
        if recent_event_exists(EVENTO, uid, AVISO_DEDUPE_DAYS):
            ja_avisados += 1
            continue
        if not args.apply:
            print(f"  user {uid}: avisaria")
            continue
        # `try` por LINHA, e ele COMEÇA na revalidação: ela é uma chamada de
        # rede como as outras, e fora do `try` uma conta ruim abandonava os
        # candidatos SEGUINTES (medido — o lote inteiro morria na primeira).
        # As outras duas dependências externas do laço (`recent_event_exists`,
        # `log_system_event_sync`) já tratam a exceção por dentro.
        try:
            # Revalida IMEDIATAMENTE antes do envio, e usa o endereço DESTA
            # leitura: o snapshot pode ter envelhecido (assinatura nova,
            # carência reaberta, e-mail trocado — endereço removido da conta
            # pode já não ser da pessoa). Falha de leitura NÃO manda: aqui o
            # silêncio é o erro recuperável, a rodada seguinte tenta de novo.
            atual = listar_contas_do_aviso(user_id=uid)
            if not atual:
                print(f"  user {uid}: pulado (deixou de perder acesso durante o lote)")
                desistidos += 1
                continue
            email = _email_de(uid, atual[0])
            # Qual dos DOIS coortes da população é esta conta? Leitura fresca,
            # no ponto do envio, e o predicado é o que já existe
            # (`db.dunning.ciclo_de_atraso_aberto`) em vez de uma segunda
            # cópia dele aqui (§0.1).
            if not email or not send_free_plan_sunset_email(
                email, corte, cobranca_pendente=ciclo_de_atraso_aberto(uid),
            ):
                falhas += 1
                continue
        except Exception as exc:
            print(f"  user {uid}: FALHOU ({exc})")
            falhas += 1
            continue
        enviados += 1
        log_system_event_sync(
            "info", EVENTO,
            "Aviso de fim do plano Gratis enviado.",
            source="scripts.aviso_fim_do_gratis",
            user_id=uid,
            details={"corte": args.corte},
        )

    print(f"\n{len(candidatos)} conta(s) na população do aviso; "
          f"{enviados} enviado(s), {ja_avisados} já avisado(s) na rodada "
          f"anterior, {desistidos} que deixaram de perder acesso durante o "
          f"lote, {falhas} falha(s).")
    if not args.apply:
        print("Dry-run: nenhum e-mail saiu. Rode de novo com --apply.")


if __name__ == "__main__":
    main()
