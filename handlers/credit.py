# handlers/credit.py
"""
Handlers de comandos relacionados a crédito, cartões e faturas.
Retorna True se tratou algum comando; False caso contrário.
"""
from collections import defaultdict
import re as regex
from ai_router import classify_category_with_gpt
from utils_date import extract_date_from_text, now_tz, today_tz, fmt_br
from utils_text import parse_money, normalize_text, fmt_brl
from db import (
    create_card,
    get_card_by_id,
    get_card_credit_usage,
    get_current_open_bill_id,
    list_cards,
    get_card_id_by_name,
    set_default_card,
    get_default_card_id,
    add_credit_purchase,
    add_credit_purchase_installments,
    get_open_bill_summary,
    pay_bill_amount,
    resolve_installment_group_id,
    get_memorized_category, 
    list_open_bills, 
    undo_credit_transaction,
    undo_installment_group, 
    list_installment_groups
)


def _pick_card_id(user_id: int, card_name: str | None):
    """Resolve card_id por nome (se vier) ou pelo cartão padrão."""
    if card_name:
        card_id = get_card_id_by_name(user_id, card_name)
        return card_id, card_name
    card_id = get_default_card_id(user_id)
    return card_id, "padrão"


def _infer_category(user_id: int, desc: str) -> str:
    """Categoria: memória -> GPT -> outros."""
    raw_norm = normalize_text(desc)
    categoria = get_memorized_category(user_id, raw_norm) or "outros"
    if categoria == "outros":
        try:
            categoria_gpt = classify_category_with_gpt(raw_norm, user_id=user_id, source="handlers.credit")
            if categoria_gpt:
                categoria = categoria_gpt
        except Exception:
            pass
    return categoria


def _purchase_code(tx_id: int) -> str:
    return f"CC{int(tx_id)}"


def _group_code(group_id: str | None) -> str:
    raw = (group_id or "").replace("-", "").upper()
    return f"PC{raw[:8]}" if raw else "PC?"


def _format_credit_purchase_success(card_label: str, valor: float, purchased_at, due: float, tx_id: int) -> str:
    code = _purchase_code(tx_id)
    return (
        f"💳 **Compra no crédito registrada**\n"
        f"🪪 Cartão: **{card_label}**\n"
        f"💰 Valor: {fmt_brl(valor)}\n"
        f"📅 Data da compra: {fmt_br(purchased_at)}\n"
        f"📌 Fatura atual: {fmt_brl(due)}\n"
        f"🔢 Código da compra: **{code}**\n"
        f"🗑️ Para apagar: `apagar {code}`"
    )


def _build_credit_limit_block_message(card_name: str, attempted_amount: float, limit_amount: float, used_amount: float) -> str:
    available = max(0.0, limit_amount - used_amount)
    exceeded = max(0.0, attempted_amount - available)
    return (
        f"❌ Compra não registrada no cartão **{card_name}**.\n"
        f"💳 Limite total: {fmt_brl(limit_amount)}\n"
        f"📌 Já usado: {fmt_brl(used_amount)}\n"
        f"🟢 Disponível: {fmt_brl(available)}\n"
        f"🧾 Tentativa de compra: {fmt_brl(attempted_amount)}\n"
        f"⚠️ Excede o limite em {fmt_brl(exceeded)}."
    )


def _validate_credit_limit_before_purchase(user_id: int, card_id: int, purchase_amount: float) -> str | None:
    card = get_card_by_id(user_id, card_id)
    if not card:
        return "❌ Cartão não encontrado."

    limit_amount = card.get("credit_limit")
    if limit_amount is None:
        return None

    used_amount = float(get_card_credit_usage(user_id, card_id))
    limit_float = float(limit_amount)
    if used_amount + float(purchase_amount) > limit_float:
        return _build_credit_limit_block_message(card["name"], float(purchase_amount), limit_float, used_amount)
    return None


def _format_cards_list(user_id: int, cards: list[dict]) -> str:
    lines = ["💳 **Seus cartões cadastrados**", ""]
    for card in cards:
        title_bits = [f"**{card['name']}**"]
        if card.get("is_default"):
            title_bits.append("⭐ principal")

        limit_amount = card.get("credit_limit")
        if limit_amount is None:
            limit_lines = ["💰 Limite: **não definido**"]
        else:
            used_amount = float(get_card_credit_usage(user_id, int(card["id"])))
            limit_float = float(limit_amount)
            available = max(0.0, limit_float - used_amount)
            limit_lines = [
                f"💰 Limite: **{fmt_brl(limit_float)}**",
                f"📌 Em uso: {fmt_brl(used_amount)}",
                f"🟢 Disponível: {fmt_brl(available)}",
            ]

        reminder_txt = "desativado"
        if card.get("reminders_enabled"):
            reminder_txt = f"{int(card.get('reminders_days_before') or 3)} dia(s) antes"

        lines.append(f"💳 {' • '.join(title_bits)}")
        lines.append(f"🗓️ Fechamento: dia **{card['closing_day']}**")
        lines.append(f"📆 Vencimento: dia **{card['due_day']}**")
        lines.extend(limit_lines)
        lines.append(f"🔔 Lembrete: {reminder_txt}")
        lines.append("")

    return "\n".join(lines).strip()


async def handle_credit_commands(message, uid: int) -> bool:
    t = message.content.strip()
    t_low = t.lower().strip()
    user_id = uid
    # -------------------------
    # desfazer parcelamento por UUID do grupo
    # uso: desfazer grupo <uuid>
    # -------------------------
    if any(cmd in t_low for cmd in ("desfazer grupo", "apagar parcelamento", "desfazer parcelamento", "apagar grupo")) or regex.search(r"\bpc[0-9a-f]{8}\b", t_low):
        m_group = regex.search(
            r"\b(?:grupo|parcelamento)\s+(pc[0-9a-f]{8}|par-[0-9a-f]{8}|[0-9a-f]{8}|[0-9a-f]{32}|[0-9a-f-]{36})\b",
            t_low,
        )
        if not m_group:
            m_group = regex.search(r"\b(pc[0-9a-f]{8})\b", t_low)
        if not m_group:
            await message.reply("Use: `apagar PCAB12CD34`")
            return True
        group_id = resolve_installment_group_id(user_id, m_group.group(1))
        if not group_id:
            await message.reply("❌ Não achei esse parcelamento.")
            return True

        try:
            res = undo_installment_group(user_id, group_id)
            if not res:
                await message.reply("❌ Não achei esse grupo de parcelamento.")
                return True

            await message.reply(
                f"🗑️ Parcelamento desfeito ({_group_code(res['group_id'])}).\n"
                f"Removido: {fmt_brl(res['removed_total'])} em {res['removed_count']} itens."
            )
            return True
        except Exception as e:
            await message.reply(f"❌ Erro ao desfazer grupo: {e}")
            return True



    # -------------------------
    # desfazer compras no credito
    # uso: apagar CC123
    # -------------------------
    if any(word in t_low for word in ("desfazer", "apagar", "excluir", "remover", "deletar", "delete")) and any(word in t_low for word in ("ct", "cc", "compra", "credito", "crédito")):
        m = regex.search(r"\b(?:ct\s*#?|cc\s*#?|compra|credito|crédito)\s*(\d+)\b", t_low)
        if not m:
            await message.reply("Use: `apagar CC17`")
            return True

        ct_id = int(m.group(1))

        try:
            res = undo_credit_transaction(user_id, ct_id)
            if not res:
                await message.reply(f"❌ Não achei a compra de código CC{ct_id}.")
                return True

            if res["mode"] == "group":
                await message.reply(
                    f"🗑️ Parcelamento desfeito ({_group_code(res['group_id'])}).\n"
                    f"Removido: {fmt_brl(res['removed_total'])} em {res['removed_count']} itens."
                )
            else:
                await message.reply(
                    f"🗑️ Compra no crédito CC{ct_id} apagada.\n"
                    f"Removido: {fmt_brl(res['removed_total'])}."
                )
            return True

        except Exception as e:
            await message.reply(f"❌ Erro ao apagar a compra CC{ct_id}: {e}")
            return True


    # -------------------------
    # criar cartao
    # -------------------------
    if t_low.startswith("criar cartao"):
        # ex: criar cartao nubank fecha 10 vence 17
        m = regex.search(r"criar cartao\s+(.+?)\s+fecha\s+(\d{1,2})\s+vence\s+(\d{1,2})", t_low)
        if not m:
            await message.reply("Use: criar cartao NOME fecha 10 vence 17")
            return True

        name = m.group(1).strip()
        fecha = int(m.group(2))
        vence = int(m.group(3))

        try:
            create_card(user_id=user_id, name=name, closing_day=fecha, due_day=vence)
            await message.reply(f"✅ Cartão '{name}' criado/atualizado. Quer definir como padrão? Use: padrao {name}")
        except Exception as e:
            await message.reply(f"❌ Erro criando cartão: {e}")
        return True

    # -------------------------
    # definir cartao padrão
    # -------------------------
    if t_low.startswith("padrao "):
        name = t[7:].strip()
        card_id = get_card_id_by_name(user_id, name)
        if not card_id:
            await message.reply(f"❌ Não achei o cartão '{name}'. Crie com: criar cartao {name} fecha 10 vence 17")
            return True

        set_default_card(user_id, card_id)
        await message.reply(f"✅ Cartão padrão definido: {name}")
        return True

    # -------------------------
    # listar cartões
    # -------------------------
    if t_low in ("cartoes", "cartões", "listar cartoes", "listar cartões"):
        cards = list_cards(user_id)
        if not cards:
            await message.reply("📭 Você ainda não tem cartões. Crie com: criar cartao nubank fecha 10 vence 17")
            return True

        await message.reply(_format_cards_list(user_id, cards))
        return True

    # -------------------------
    # compra no crédito (fatura) via comando "credito ..."
    # -------------------------
    if t_low.startswith("credito"):
        # exemplos:
        #   credito 120 mercado
        #   credito nubank 120 mercado
        rest = t[len("credito"):].strip()
        if not rest:
            await message.reply("Use: credito 120 mercado OU credito nubank 120 mercado")
            return True

        # data opcional (ontem/hoje/2026-02-01 etc), retorna (dt, texto_sem_data)
        dt_evento, rest2 = extract_date_from_text(rest)
        if dt_evento is None:
            dt_evento = now_tz()
        purchased_at = dt_evento.date()

        valor = parse_money(rest2)
        if valor is None:
            await message.reply("❌ Não achei o valor. Ex: credito 120 mercado")
            return True

        tokens = rest2.split()
        card_name = None

        # se o primeiro token não tem número, tratamos como nome do cartão
        if tokens and parse_money(tokens[0]) is None:
            card_name = tokens[0]
            rest_desc = " ".join(tokens[1:])
        else:
            rest_desc = rest2

        nota = normalize_text(rest_desc)
        categoria = _infer_category(user_id, rest_desc)

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            if card_name:
                await message.reply(f"❌ Não achei o cartão '{card_name}'. Crie com: criar cartao {card_name} fecha 10 vence 17")
            else:
                await message.reply("❓ Você não tem cartão padrão. Defina com: padrao NOME (ou crie: criar cartao nubank fecha 10 vence 17)")
            return True

        limit_error = _validate_credit_limit_before_purchase(user_id, card_id, float(valor))
        if limit_error is not None:
            await message.reply(limit_error)
            return True

        try:
            tx_id, due, bill_id = add_credit_purchase(
                user_id=user_id,
                card_id=card_id,
                valor=float(valor),
                categoria=categoria,
                nota=nota,
                purchased_at=purchased_at,
            )
            await message.reply(_format_credit_purchase_success(resolved_name, float(valor), purchased_at, float(due), int(tx_id)))
        except Exception as e:
            await message.reply(f"❌ Erro registrando compra no crédito: {e}")
        return True
    
    # alias: "criar parcelas" / "parcelas" -> instrução
    if t_low in ("criar parcelas", "criar parcela", "parcelas"):
        await message.reply("Use: `parcelar 300 em 3x no cartao nubank` (ex: `parcelar 120 em 4x no cartao nubank`)")
        return True
    
    if t_low in ("parcelas", "listar parcelas"):
        t_low = "parcelamentos"

    # -------------------------
    # PARCELAR 
    # -------------------------
    # normaliza "parcelei" para "parcelar"
    if t_low.startswith("parcelei "):
        t_low = "parcelar " + t_low[len("parcelei "):]

    if t_low.startswith("parcelar"):
        # exemplos:
        #   parcelar 300 no cartao nubank
        #   parcelar 300 em 3x no cartao nubank
        valor = parse_money(t_low)
        if valor is None:
            await message.reply("Use: parcelar 300 em 3x no cartao nubank")
            return True

        # parcelas (default 1 se não informar)
        n = 1
        mx = regex.search(r"(\d+)\s*x", t_low)
        if mx:
            try:
                n = int(mx.group(1))
            except Exception:
                n = 1

        # pega nome do cartão (se tiver)
        card_name = None
        m = regex.search(r"(?:no\s+)?cart[aã]o\s+(.+)$", t_low)
        if m:
            card_name = m.group(1).strip()

        # data opcional
        dt_evento, rest2 = extract_date_from_text(t)
        if dt_evento is None:
            dt_evento = now_tz()
        purchased_at = dt_evento.date()

        # Remove os tokens de comando da nota (valor, parcelas, cartão, data)
        desc_clean = rest2  # já sem data
        desc_clean = regex.sub(r"\bparcelei?\b", "", desc_clean, flags=regex.IGNORECASE)
        desc_clean = regex.sub(r"\b\d+[\.,]?\d*\b", "", desc_clean)        # remove números/valores
        desc_clean = regex.sub(r"\b\d+\s*x\b", "", desc_clean, flags=regex.IGNORECASE)  # remove "3x"
        desc_clean = regex.sub(r"\bem\b", "", desc_clean, flags=regex.IGNORECASE)
        desc_clean = regex.sub(r"\bno\s+cart[aã]o\s+\S+", "", desc_clean, flags=regex.IGNORECASE)
        desc_clean = regex.sub(r"\bcart[aã]o\s+\S+", "", desc_clean, flags=regex.IGNORECASE)
        desc_clean = " ".join(desc_clean.split())  # normaliza espaços

        nota = normalize_text(desc_clean) if desc_clean.strip() else normalize_text(t)
        categoria = _infer_category(user_id, desc_clean or t)

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            if card_name:
                await message.reply(f"❌ Não achei o cartão '{card_name}'. Crie com: criar cartao {card_name} fecha 10 vence 17")
            else:
                await message.reply(
                    "❓ Qual cartão?\n"
                    "Ex: `parcelei 500 em 5x no cartao nubank`\n"
                    "Dica: defina um padrão com `padrao nubank`."
                )
            return True

        limit_error = _validate_credit_limit_before_purchase(user_id, card_id, float(valor))
        if limit_error is not None:
            await message.reply(limit_error)
            return True

        try:
            ret = add_credit_purchase_installments(
                user_id=user_id,
                card_id=card_id,
                valor_total=float(valor),
                categoria=categoria,
                nota=nota,
                purchased_at=purchased_at,
                installments=n,
            )

            # ret pode ser:
            # 1) dict {"group_id": "...", "tx_ids":[...]}
            # 2) (dict, total)
            # 3) (dict, total, alguma_coisa)
            # 4) qualquer coisa errada -> a gente não quebra

            result = None
            total = float(valor)

            if isinstance(ret, tuple):
                if len(ret) >= 1:
                    result = ret[0]
                if len(ret) >= 2:
                    try:
                        total = float(ret[1])
                    except:
                        total = float(valor)
            else:
                result = ret

            # garante que "result" seja dict
            if not isinstance(result, dict):
                await message.reply(f"❌ Retorno inesperado do DB no parcelamento: {type(result)} | {result}")
                return True

            tx_ids = result.get("tx_ids") or []
            group_id = result.get("group_id")

            ids_str = ", ".join(f"#{x}" for x in tx_ids[:10]) if tx_ids else "(sem ids)"
            if len(tx_ids) > 10:
                ids_str += " ..."

            await message.reply(
                f"💳 Parcelado no cartão ({resolved_name}): R$ {float(valor):.2f} em {n}x\n"
                f"📌 Total lançado nas faturas: R$ {float(total):.2f}\n"
                f"🔢 Código do parcelamento: **{_group_code(group_id)}**\n"
                f"🗑️ Para apagar: `apagar {_group_code(group_id)}`\n"
                f"IDs internos: {ids_str}"
            )
            return True

        except Exception as e:
            await message.reply(f"❌ Erro ao parcelar no cartão: {e}")
            return True
        

    # -------------------------
    # pagar fatura (total ou parcial) - fatura ATUAL do período
    # -------------------------
    if t_low.startswith("pagar fatura") or t_low.startswith("paguei fatura"):
        rest = t[len("pagar fatura"):].strip()

        tokens = rest.split() if rest else []
        amount = None
        card_name = None

        if tokens:
            last_val = parse_money(tokens[-1])
            if last_val is not None:
                amount = float(last_val)
                tokens = tokens[:-1]
            if tokens:
                card_name = " ".join(tokens).strip()

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            await message.reply("❓ Você não tem cartão padrão. Defina com: padrao NOME")
            return True

        try:
            as_of = today_tz()
            bill_id = get_current_open_bill_id(user_id, card_id, as_of)
            if not bill_id:
                await message.reply("📭 Nenhuma fatura aberta do período atual para pagar.")
                return True

            res = pay_bill_amount(user_id, card_id, resolved_name, amount, bill_id=bill_id)

            if isinstance(res, dict) and res.get("error") == "amount_too_high":
                await message.reply(
                    "❌ Valor maior do que o em aberto.\n"
                    f"Em aberto: {fmt_brl(res['due'])} | Total: {fmt_brl(res['total'])} | Já pago: {fmt_brl(res['paid_amount'])}"
                )
                return True

            if isinstance(res, dict) and res.get("error") == "invalid_amount":
                await message.reply("❌ Valor inválido. Use: pagar fatura 300")
                return True

            if not res:
                await message.reply("📭 Nada para pagar.")
                return True

            await message.reply(
                f"✅ Pagamento registrado: {fmt_brl(res['paid'])}\n"
                f"Conta agora: {fmt_brl(res['new_balance'])}\n"
                f"ID lançamento: #{res['launch_id']}"
            )
            return True

        except Exception as e:
            await message.reply(f"❌ Erro ao pagar fatura: {e}")
            return True


# --- faturas (lista todas as faturas em aberto, agrupadas por mês) ---
    if t_low in ("faturas", "listar faturas", "faturas abertas", "listar faturas abertas", "listar fatura", "listar faturas em aberto"):
        try:
            rows = list_open_bills(user_id)
            if not rows:
                await message.reply("📭 Nenhuma fatura em aberto.")
                return True

            meses = [
                "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
            ]

            # agrupa por (ano, mes) usando o period_start
            groups = defaultdict(list)
            for r in rows:
                total = float(r["total"] or 0)
                paid = float(r["paid_amount"] or 0)
                due = max(0.0, total - paid)

                # remove faturas futuras "vazias"
                if total == 0.0 and paid == 0.0 and due == 0.0:
                    continue

                ps = r["period_start"]
                key = (ps.year, ps.month)
                groups[key].append((r, total, paid, due))

            if not groups:
                await message.reply("📭 Nenhuma fatura em aberto (as futuras zeradas foram ocultadas).")
                return True

            # ordena por data
            keys = sorted(groups.keys())

            lines = ["🧾 **Faturas em aberto (por mês):**", ""]

            # controle de tamanho pra não estourar 2000 chars do Discord
            max_chars = 1800

            for (y, m) in keys:
                header = f"📅 **{meses[m-1]}/{y}:**"
                if sum(len(x) + 1 for x in lines) + len(header) + 2 > max_chars:
                    lines.append("")
                    lines.append("... (mensagem cortada: muitas faturas)")
                    break

                lines.append(header)

                # ordena dentro do mês por card_name
                items = sorted(groups[(y, m)], key=lambda it: (it[0]["card_name"] or "").lower())

                for (r, total, paid, due) in items:
                    card = r["card_name"]
                    line = f"• {card}: Total {fmt_brl(total)} | Pago {fmt_brl(paid)} | Em aberto {fmt_brl(due)}"

                    if sum(len(x) + 1 for x in lines) + len(line) + 1 > max_chars:
                        lines.append("... (mensagem cortada: muitas faturas)")
                        break

                    lines.append(line)

                lines.append("")  # linha em branco entre meses

            await message.reply("\n".join(lines).strip())
            return True

        except Exception as e:
            await message.reply(f"❌ Erro ao listar faturas: {e}")
            return True



    # --- fatura (mostra a fatura atual do período) ---
    if t_low.startswith("fatura " ) or t_low == "fatura":
        parts = t.split()
        # "fatura" ou "fatura nubank"
        card_name = parts[1] if len(parts) >= 2 else None

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            await message.reply("❓ Você não tem cartão padrão. Defina com: `padrao NOME`.")
            return True

        try:
            res = get_open_bill_summary(user_id, card_id, as_of=today_tz())
            if not res:
                await message.reply(f"📭 Nenhuma fatura aberta para {resolved_name}.")
                return True

            bill, items = res

            ps = fmt_br(bill["period_start"])
            pe = fmt_br(bill["period_end"])

            total = float(bill["total"] or 0)
            paid = float(bill.get("paid_amount", 0) or 0)
            due = max(0.0, total - paid)

            lines = [
                f"💳 Fatura atual ({resolved_name}) {ps} → {pe}",
                f"Total: {fmt_brl(total)} | Pago: {fmt_brl(paid)} | Em aberto: {fmt_brl(due)}",
                "",
            ]

            shown = items[:10]
            for it in shown:
                parcela = ""
                if it.get("installment_no") and it.get("installments_total"):
                    parcela = f" [{it['installment_no']}/{it['installments_total']}]"
                lines.append(
                    f"• {fmt_brl(it['valor'])} | {it['categoria'] or 'outros'} | {fmt_br(it['purchased_at'])} | {it['nota'] or ''}{parcela}"
                )

            if len(items) > 10:
                lines.append(f"\n… e mais {len(items) - 10} lançamento(s). Use `exportar` para ver tudo.")

            await message.reply("\n".join(lines))
            return True

        except Exception as e:
            await message.reply(f"❌ Erro ao buscar fatura: {e}")
            return True
        
    
    # -------------------------
    # listar parcelamentos (grupos)
    # uso: parcelamentos  (ou listar parcelamentos)
    # -------------------------
    if t_low in ["parcelamentos", "listar parcelamentos"]:
        rows = list_installment_groups(user_id, limit=15)
        if not rows:
            await message.reply("📭 Você não tem parcelamentos registrados.")
            return True

        lines = ["📦 **Parcelamentos ativos:**"]
        for r in rows:
            n_total    = int(r.get("n_total") or r.get("n_registered") or 0)
            n_pending  = int(r.get("n_pending") or 0)
            n_paid     = n_total - n_pending
            total      = float(r.get("total") or 0)
            pending    = float(r.get("total_pending") or 0)
            nota       = (r.get("nota") or "").strip()
            card       = r.get("card_name", "?")
            group_id   = str(r.get("group_id") or "")

            # Oculta parcelamentos totalmente quitados
            if n_pending == 0:
                continue

            desc = f" — {nota}" if nota else ""
            progress = f"{n_paid}/{n_total} pagas"
            group_code = _group_code(group_id)
            lines.append(
                f"• {card}{desc}\n"
                f"  💰 Total: {fmt_brl(total)} | Restante: {fmt_brl(pending)} ({progress})\n"
                f"  🔢 Código: `{group_code}`\n"
                f"  🗑️ Apagar: `apagar {group_code}`"
            )

        if len(lines) == 1:
            await message.reply("✅ Você não tem parcelamentos em aberto.")
            return True

        msg = "\n".join(lines)
        if len(msg) > 1900:
            msg = "\n".join(lines[:12]) + "\n\n(⚠️ Muitos resultados; veja os mais recentes acima.)"

        await message.reply(msg)
        return True
