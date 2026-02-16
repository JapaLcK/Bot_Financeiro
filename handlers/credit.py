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
    get_current_open_bill_id,
    list_cards,
    get_card_id_by_name,
    set_default_card,
    get_default_card_id,
    add_credit_purchase,
    add_credit_purchase_installments,
    get_open_bill_summary,
    pay_bill_amount,
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
            categoria_gpt = classify_category_with_gpt(raw_norm)
            if categoria_gpt:
                categoria = categoria_gpt
        except Exception:
            pass
    return categoria


async def handle_credit_commands(message) -> bool:
    t = message.content.strip()
    t_low = t.lower().strip()
    user_id = message.author.id
    # -------------------------
    # desfazer parcelamento por UUID do grupo
    # uso: desfazer grupo <uuid>
    # -------------------------
    if t_low.startswith("desfazer grupo"):
        raw = t[len("desfazer grupo"):].strip().lower()
        group_id = raw
        group_id_compact = group_id.replace("-", "")

        # aceita UUID com hífen (36) OU compacto (32)
        if not regex.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", group_id):
            await message.reply("Use: desfazer grupo <UUID>")
            return True

        try:
            # passe os dois formatos pro DB (ver patch abaixo)
            res = undo_installment_group(user_id, group_id)
            if not res:
                await message.reply("❌ Não achei esse grupo de parcelamento.")
                return True

            await message.reply(
                f"🗑️ Parcelamento desfeito (grupo {res['group_id']}).\n"
                f"Removido: {fmt_brl(res['removed_total'])} em {res['removed_count']} itens."
            )
            return True
        except Exception as e:
            await message.reply(f"❌ Erro ao desfazer grupo: {e}")
            return True



    # -------------------------
    # desfazer compras no credito
    # uso: desfazer CT#123
    # -------------------------
    if t_low.startswith("desfazer") and "ct" in t_low:
        m = regex.search(r"\bct\s*#?\s*(\d+)\b", t_low)
        if not m:
            await message.reply("Use: desfazer CT#123")
            return True

        ct_id = int(m.group(1))

        try:
            res = undo_credit_transaction(user_id, ct_id)
            if not res:
                await message.reply(f"❌ Não achei o crédito CT#{ct_id}.")
                return True

            if res["mode"] == "group":
                await message.reply(
                    f"🗑️ Parcelamento desfeito (grupo {res['group_id']}).\n"
                    f"Removido: {fmt_brl(res['removed_total'])} em {res['removed_count']} itens."
                )
            else:
                await message.reply(
                    f"🗑️ Crédito CT#{ct_id} desfeito.\n"
                    f"Removido: {fmt_brl(res['removed_total'])}."
                )
            return True

        except Exception as e:
            await message.reply(f"❌ Erro ao desfazer CT#{ct_id}: {e}")
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

        lines = ["💳 **Seus cartões:**"]
        for c in cards:
            badge = " (padrão)" if c.get("is_default") else ""
            lines.append(f"- {c['name']}{badge} — fecha dia {c['closing_day']} / vence dia {c['due_day']}")
        await message.reply("\n".join(lines))
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

        try:
            tx_id, due, bill_id = add_credit_purchase(
                user_id=user_id,
                card_id=card_id,
                valor=float(valor),
                categoria=categoria,
                nota=nota,
                purchased_at=purchased_at,
            )
            await message.reply(
                f"💳 Compra no crédito registrada: {fmt_brl(valor)}\n"
                f"📅 Data da compra: {fmt_br(purchased_at)}\n"
                f"📌 Fatura atual: {fmt_brl(due)}\n"
                f"ID crédito: CT#{tx_id}"
            )
        except Exception as e:
            await message.reply(f"❌ Erro registrando compra no crédito: {e}")
        return True

    # -------------------------
    # PARCELAR 
    # -------------------------
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

        # nota/categoria
        nota = normalize_text(t)
        categoria = _infer_category(user_id, t)

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            if card_name:
                await message.reply(f"❌ Não achei o cartão '{card_name}'. Crie com: criar cartao {card_name} fecha 10 vence 17")
            else:
                await message.reply("❓ Você não tem cartão padrão. Defina com: padrao NOME")
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
                f"Grupo: {group_id}\n"
                f"IDs: {ids_str}"
            )
            return True

        except Exception as e:
            await message.reply(f"❌ Erro ao parcelar no cartão: {e}")
            return True
        

    # -------------------------
    # pagar fatura (total ou parcial) - fatura ATUAL do período
    # -------------------------
    if t_low.startswith("pagar fatura"):
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

            for it in items[:10]:
                lines.append(
                    f"• {fmt_brl(it['valor'])} | {it['categoria'] or 'outros'} | {fmt_br(it['purchased_at'])} | {it['nota'] or ''}"
                )

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

        lines = ["📦 **Parcelamentos (grupos):**"]
        for r in rows:
            lines.append(
                f"• {r['card_name']} | {fmt_brl(r['total'])} | {r['n']} itens | grupo: {r['group_id']}"
            )

        msg = "\n".join(lines)
        if len(msg) > 1900:
            msg = "\n".join(lines[:10]) + "\n\n(⚠️ Muitos resultados; vou mostrar só os 10 primeiros.)"

        await message.reply(msg)
        return True


