# core/handlers/pending.py
"""
Resolve ações pendentes: confirmações de delete, lançamentos de mídia e esclarecimentos.
"""
from __future__ import annotations
import logging

import db
from utils_text import fmt_brl

logger = logging.getLogger(__name__)


def _log_falha(op: str, user_id: int, e: Exception, *,
               nivel: int = logging.ERROR, **extra) -> None:
    """Causa no log, nunca na mensagem: `str(e)` do psycopg pode trazer o valor
    e a descrição da linha (`DETAIL: Key (…)=(…)`). Nome do tipo + sqlstate já
    separam conexão (08006), deadlock (40P01), permissão (42501) e bug de
    código. Sem `exc_info` pelo mesmo motivo.

    Helper ÚNICO das três portas (esta, `core/handlers/credit.py` e
    `core/services/ai_chat/tools/launches.py`): duas cópias com níveis
    diferentes faziam a MESMA condição contar como erro numa porta e não na
    outra. O nível importa fora do log — `_DashboardHandler`
    (`core/observability.py`) espelha WARNING e ERROR em `system_event_logs`
    com `level=levelname.lower()`, e `core/admin_dashboard.py` conta
    `backend_errors_24h WHERE level='error'`.

    `nivel` segue a MESMA distinção dos `except` daqui, não outra:
      - condição de domínio ESPERADA (`LaunchNoEffects`,
        `InvestmentLotHasWithdrawal`) → `logging.WARNING`. Inflar o contador de
        erros do admin com aporte que teve resgate é ruído, não incidente.
      - falha técnica/inesperada (`except Exception`, `ValueError` sem código
        conhecido) → `logging.ERROR`, que é o default: quem esquecer de
        classificar erra para o lado barulhento, não para o lado silencioso.

    `nivel` é keyword-only por isso não colide com `**extra`; um campo extra
    chamado "nivel" seria engolido (nenhum call site usa).
    """
    logger.log(
        nivel,
        "%s: falha user_id=%s%s causa=%s sqlstate=%s",
        op, user_id, "".join(f" {k}={v}" for k, v in extra.items()),
        type(e).__name__, getattr(e, "sqlstate", None),
    )


def resolve_delete(user_id: int, confirmed: bool) -> str | None:
    """
    Verifica se existe uma pending_action para o usuário e a resolve.
    Trata: deletes de lançamento/caixinha/investimento e confirmação de lançamento via mídia.
    Retorna mensagem de resposta, ou None se não havia pending reconhecido.

    Todo consumo é CONDICIONAL (`db.consume_pending_action`): apaga a pendência
    que foi lida, não "o que estiver lá". Perder o compare-and-swap significa
    que outra tarefa (Discord, ou a outra plataforma do mesmo usuário) já
    trocou a linha — e aí o "sim" do usuário não é mais deste pedido. Nos
    caminhos destrutivos o CAS é o porteiro: quem perde NÃO apaga nada e sai
    como se não houvesse pendência, que é a verdade.
    """
    pending = db.get_pending_action(user_id)
    if not pending:
        return None

    action_type = pending.get("action_type")

    # ── Confirmação de lançamento extraído de imagem ─────────────────────────
    if action_type == "confirm_media_launch":
        payload = pending.get("payload", {})
        # Porteiro: registra dinheiro. Se a linha já não é a que lemos, outra
        # tarefa consumiu ou substituiu — não registra em dobro.
        if not db.consume_pending_action(user_id, pending):
            return None

        if not confirmed:
            return "❌ Lançamento cancelado. Se quiser corrigir, escreva o comando manualmente."

        # Caminho novo: prévia estruturada. Registra a categoria e a data DA
        # PRÉVIA, sem re-inferir do texto — é o que corrige "Amazônia" virar
        # "compras online" na re-classificação. A grafia ainda passa pela
        # normalização de storage (`_normalize_category_name`): 'Comida
        # Japonesa' é gravado como 'comida japonesa'.
        if payload.get("valor") is not None and payload.get("categoria"):
            from core.handlers.launches import add_from_entities

            criado_em = None
            data_iso = payload.get("data")
            if data_iso:
                from datetime import date as _date, datetime as _datetime, time as _time
                from utils_date import _tz
                try:
                    _d = _date.fromisoformat(data_iso)
                    criado_em = _datetime.combine(_d, _time(12, 0), tzinfo=_tz())
                except (ValueError, TypeError):
                    criado_em = None

            alvo = payload.get("alvo") or ""
            resp = add_from_entities(
                user_id,
                tipo=payload.get("tipo") or "despesa",
                valor=float(payload["valor"]),
                alvo=alvo,
                nota=alvo or None,
                categoria=payload.get("categoria") or "outros",
                # != "ai": respeita a categoria que o usuário confirmou; não
                # dispara o cross-check com regras locais.
                category_reason="image_confirmed",
                criado_em=criado_em,
                platform=payload.get("platform") or "whatsapp",
            )
            return f"✅ Lançamento registrado!\n{resp}"

        # Fallback legado: pendências antigas que só guardaram o texto.
        text = payload.get("text", "")
        if not text:
            return "⚠️ Não encontrei os dados do lançamento para confirmar. Tente digitar manualmente."

        from core.services.quick_entry import handle_quick_entry
        msg_out = handle_quick_entry(user_id, text)
        if msg_out:
            return f"✅ Lançamento registrado!\n{msg_out.text}"
        return f"⚠️ Não consegui registrar automaticamente. Tente: `{text}`"

    # ── Oferta "virar gasto fixo" (despesa que se repetiu) ───────────────────
    if action_type == "confirm_recurring_offer":
        payload = pending.get("payload", {})
        # Porteiro: cria um gasto fixo que o autopay vai debitar todo mês.
        if not db.consume_pending_action(user_id, pending):
            return None

        if not confirmed:
            # grava a recusa pra não re-sugerir a mesma combinação (merchant+valor)
            try:
                from db.recurring import dismiss_recurring_suggestion
                dismiss_recurring_suggestion(
                    user_id, payload.get("merchant_key", ""), payload.get("amount"),
                )
            except Exception:
                pass
            return "Ok, não marco como gasto fixo. 👍"

        from db.recurring import create_recurring_expense, mark_recurring_charged
        try:
            rec = create_recurring_expense(
                user_id,
                payload.get("name") or "Gasto fixo",
                float(payload.get("amount") or 0),
                payload.get("category") or "outros",
                int(payload.get("due_day") or 1),
                "account",
                payment_mode="autopay",
            )
            # A despesa deste mês que disparou a oferta JÁ foi lançada. O recorrente
            # nasce com due_day=hoje e start_date=hoje, então o charger autopay o
            # consideraria "vence hoje" e debitaria de novo no mesmo dia, dobrando o
            # lançamento. Marca o mês corrente como já cobrado — 1ª cobrança
            # automática cai só no mês que vem.
            from utils_date import today_tz
            mark_recurring_charged(user_id, rec["id"], today_tz().strftime("%Y-%m"))
        except (ValueError, TypeError) as e:
            return f"Não consegui criar o gasto fixo agora ({e}). Você pode cadastrar na aba *Recorrentes* do dashboard."
        return (
            f"✅ Pronto! *{rec['name']}* agora é *gasto fixo*: "
            f"{fmt_brl(rec['amount'])} todo dia {rec['due_day']}. "
            f"A Piggy lança sozinha. 🐷"
        )

    # só trata deletes abaixo
    if action_type not in ("delete_launch", "delete_launch_bulk", "delete_pocket", "delete_investment"):
        return None

    payload = pending.get("payload", {})

    if not confirmed:
        # Retorno IGNORADO de propósito: esta porta é o WhatsApp, serializado
        # por worker único, então "outra tarefa executou entre a leitura e o
        # cancelamento" não é alcançável aqui. No `/ai/chat` é, e lá o retorno
        # é checado (`ai_chat/runner.py`) — perder o CAS num cancelamento
        # significa que a outra requisição EXECUTOU, não que não havia nada.
        # A premissa depende de `/wa/dev/simulate` continuar fora do ar: ela
        # chama `process_payload` direto, furando a `_queue` do worker único
        # (`adapters/whatsapp/wa_app.py`). Hoje a rota só é registrada com
        # `ENABLE_DEV_ENDPOINTS` ligado (default OFF) e em produção responde
        # 404 (medido 2026-08-28). Ligar essa flag em produção quebra esta
        # premissa — e a rota não tem auth própria.
        db.consume_pending_action(user_id, pending)
        return "❌ Ação cancelada."

    if action_type == "delete_launch":
        launch_id = payload.get("launch_id")
        # display_id é o user_seq mostrado pro usuário; cai pro id interno
        # quando o pending foi criado em código antigo sem essa key.
        display_id = payload.get("display_id") or launch_id
        # ANTES de apagar, não depois: o clear posterior não protegeria nada —
        # as duas tarefas já teriam apagado o lançamento.
        if not db.consume_pending_action(user_id, pending):
            return None
        erro_tecnico = (
            f"❌ Não consegui apagar o lançamento #{display_id} agora — deu "
            f"erro do meu lado. Tenta de novo em alguns minutos."
        )
        try:
            db.delete_launch_and_rollback(user_id, launch_id)
            return f"✅ Lançamento **#{display_id}** apagado e saldo revertido."
        except LookupError:
            # NOT_FOUND (`db/accounts.py`): o lançamento sumiu entre a pergunta
            # e o "sim" — outra porta (dashboard, /ai/chat) apagou dentro da
            # janela de 10 min da pendência. Condição PERMANENTE: mandar tentar
            # de novo é conselho que nunca vai funcionar.
            return f"🐷 O lançamento **#{display_id}** já não está no seu histórico."
        except db.LaunchNoEffects as e:
            # Sem `efeitos` não dá pra reverter o saldo com segurança — também
            # permanente. É a MESMA distinção do "apagar tudo", que separa
            # `kept_no_effects` de `errors` (`db/accounts.py`); aqui a porta é
            # um lançamento só, mas a causa e a frase são as mesmas.
            _log_falha("delete_launch_sem_efeitos", user_id, e,
                       nivel=logging.WARNING, launch_id=launch_id, user_seq=display_id)
            return (
                f"⚠️ O lançamento **#{display_id}** é antigo e não guarda o que "
                f"precisaria ser revertido, então mantive ele intacto pra não "
                f"bagunçar seu saldo."
            )
        except db.InvestmentLotHasWithdrawal as e:
            # TEMPORÁRIA, e é a única aqui que tem contorno: apagar o resgate
            # reabre o lote. "Tenta de novo em alguns minutos" seria falso (o
            # tempo não destrava nada), e "é antigo" também — o dado está
            # inteiro. A frase tem de dizer O QUE destrava.
            _log_falha("delete_launch_lote_com_resgate", user_id, e,
                       nivel=logging.WARNING, launch_id=launch_id, user_seq=display_id)
            return (
                f"🐷 Não dá pra desfazer o aporte **#{display_id}**: esse lote já "
                f"teve resgate. Apaga o resgate primeiro e depois volta aqui pra "
                f"apagar o aporte."
            )
        except Exception as e:
            _log_falha("delete_launch", user_id, e, launch_id=launch_id, user_seq=display_id)
            return erro_tecnico

    if action_type == "delete_launch_bulk":
        ids = payload.get("launch_ids", [])
        display_ids_map = payload.get("display_ids") or {}
        if not db.consume_pending_action(user_id, pending):
            return None

        # converte ids internos pra user_seq pra exibição (fallback: id interno)
        def _disp(lid):
            return display_ids_map.get(str(lid), display_ids_map.get(lid, lid))

        failed = []
        for lid in ids:
            try:
                db.delete_launch_and_rollback(user_id, lid)
            except Exception as e:
                failed.append(lid)
                # os DOIS ids: a queixa cita "#2", o log cita o id interno.
                _log_falha("delete_launch_bulk", user_id, e,
                           launch_id=lid, user_seq=_disp(lid))
        ok_ids = [i for i in ids if i not in failed]
        parts = []
        if ok_ids:
            parts.append("✅ Apagados: " + ", ".join(f"**#{_disp(i)}**" for i in ok_ids))
        if failed:
            parts.append("⚠️ Falha: " + ", ".join(f"#{_disp(i)}" for i in failed))
        return "\n".join(parts) or "Nada foi apagado."

    if action_type == "delete_pocket":
        pocket_name = payload.get("pocket_name")
        if not db.consume_pending_action(user_id, pending):
            return None
        erro_tecnico = (
            f"❌ Não consegui deletar a caixinha **{pocket_name}** agora. "
            f"Tenta de novo em alguns minutos."
        )
        try:
            db.delete_pocket(user_id, pocket_name)
            return f"✅ Caixinha **{pocket_name}** deletada."
        except LookupError:
            # POCKET_NOT_FOUND: alcançável dentro da janela de 10 min — o
            # usuário pede pra apagar no WhatsApp, apaga pelo dashboard e só
            # então responde "sim". Permanente, sem "tenta de novo".
            return f"🐷 Não achei a caixinha **{pocket_name}** — parece que ela já não existe."
        except ValueError as e:
            # POCKET_NOT_ZERO / EMPTY_NAME são CÓDIGOS, não texto de usuário.
            # Mesma tradução que `core/services/ai_chat/tools/pockets.py` e
            # `frontend/routes/pockets.py` já fazem. Os dois são permanentes.
            if "POCKET_NOT_ZERO" in str(e):
                return (
                    f"🐷 A caixinha **{pocket_name}** ainda tem saldo. "
                    f"Saca o que tem dentro antes de apagar."
                )
            if "EMPTY_NAME" in str(e):
                return "🐷 Faltou o nome da caixinha — me diz qual você quer apagar."
            _log_falha("delete_pocket", user_id, e, pocket=pocket_name)
            return erro_tecnico
        except Exception as e:
            _log_falha("delete_pocket", user_id, e, pocket=pocket_name)
            return erro_tecnico

    if action_type == "delete_investment":
        investment_name = payload.get("investment_name")
        if not db.consume_pending_action(user_id, pending):
            return None
        erro_tecnico = (
            f"❌ Não consegui deletar o investimento **{investment_name}** "
            f"agora. Tenta de novo em alguns minutos."
        )
        try:
            db.delete_investment(user_id, investment_name)
            return f"✅ Investimento **{investment_name}** deletado."
        except LookupError:
            # INV_NOT_FOUND: mesma janela de 10 min da caixinha. Permanente.
            return (
                f"🐷 Não achei o investimento **{investment_name}** — parece que "
                f"ele já não existe."
            )
        except ValueError as e:
            # INV_NOT_ZERO / EMPTY_NAME: códigos. Mesma tradução de
            # `core/services/ai_chat/tools/investments.py`.
            if "INV_NOT_ZERO" in str(e):
                return (
                    f"🐷 O investimento **{investment_name}** ainda tem saldo — "
                    f"resgata tudo antes de apagar."
                )
            if "EMPTY_NAME" in str(e):
                return "🐷 Faltou o nome do investimento — me diz qual você quer apagar."
            _log_falha("delete_investment", user_id, e, investment=investment_name)
            return erro_tecnico
        except Exception as e:
            _log_falha("delete_investment", user_id, e, investment=investment_name)
            return erro_tecnico

    # Limpeza de estado: tipo destrutivo sem branch acima. Ignora o resultado.
    db.consume_pending_action(user_id, pending)
    return None


def get_pending_clarification(user_id: int) -> dict | None:
    """
    Retorna o pending de esclarecimento se existir, ou None.
    """
    pending = db.get_pending_action(user_id)
    if pending and pending.get("action_type") == "clarification":
        return pending
    return None
