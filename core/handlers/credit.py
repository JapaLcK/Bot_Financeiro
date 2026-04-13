from __future__ import annotations

import re
from collections import defaultdict

from ai_router import classify_category_with_gpt
from db import (
    add_credit_purchase,
    add_credit_purchase_installments,
    create_card,
    get_card_id_by_name,
    get_current_open_bill_id,
    get_default_card_id,
    get_memorized_category,
    get_open_bill_summary,
    list_cards,
    list_installment_groups,
    list_open_bills,
    pay_bill_amount,
    set_default_card,
)
from utils_date import extract_date_from_text, fmt_br, now_tz, today_tz
from utils_text import fmt_brl, normalize_text, parse_money


def _pick_card_id(user_id: int, card_name: str | None):
    if card_name:
        card_id = get_card_id_by_name(user_id, card_name)
        return card_id, card_name
    card_id = get_default_card_id(user_id)
    return card_id, "padrão"


def _infer_category(user_id: int, desc: str) -> str:
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


def handle(user_id: int, text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None

    t_low = t.lower().strip()

    if t_low.startswith("criar cartao") or t_low.startswith("criar cartão"):
        m = re.search(r"criar\s+cart[aã]o\s+(.+?)\s+fecha\s+(\d{1,2})\s+vence\s+(\d{1,2})", t, re.IGNORECASE)
        if not m:
            return "Use: criar cartao NOME fecha 10 vence 17"

        name = m.group(1).strip()
        fecha = int(m.group(2))
        vence = int(m.group(3))

        try:
            create_card(user_id=user_id, name=name, closing_day=fecha, due_day=vence)
            return f"✅ Cartão '{name}' criado/atualizado. Quer definir como padrão? Use: padrao {name}"
        except Exception as e:
            return f"❌ Erro criando cartão: {e}"

    if t_low.startswith("padrao ") or t_low.startswith("padrão "):
        name = re.sub(r"^padr[aã]o\s+", "", t, flags=re.IGNORECASE).strip()
        card_id = get_card_id_by_name(user_id, name)
        if not card_id:
            return f"❌ Não achei o cartão '{name}'. Crie com: criar cartao {name} fecha 10 vence 17"

        set_default_card(user_id, card_id)
        return f"✅ Cartão padrão definido: {name}"

    if t_low in ("cartoes", "cartões", "listar cartoes", "listar cartões"):
        cards = list_cards(user_id)
        if not cards:
            return "📭 Você ainda não tem cartões. Crie com: criar cartao nubank fecha 10 vence 17"

        lines = ["💳 **Seus cartões:**"]
        for c in cards:
            badge = " (padrão)" if c.get("is_default") else ""
            lines.append(f"- {c['name']}{badge} — fecha dia {c['closing_day']} / vence dia {c['due_day']}")
        return "\n".join(lines)

    if t_low.startswith("credito"):
        rest = t[len("credito"):].strip()
        if not rest:
            return "Use: credito 120 mercado OU credito nubank 120 mercado"

        dt_evento, rest2 = extract_date_from_text(rest)
        if dt_evento is None:
            dt_evento = now_tz()
        purchased_at = dt_evento.date()

        valor = parse_money(rest2)
        if valor is None:
            return "❌ Não achei o valor. Ex: credito 120 mercado"

        tokens = rest2.split()
        card_name = None
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
                return f"❌ Não achei o cartão '{card_name}'. Crie com: criar cartao {card_name} fecha 10 vence 17"
            return "❓ Você não tem cartão padrão. Defina com: padrao NOME (ou crie: criar cartao nubank fecha 10 vence 17)"

        try:
            tx_id, due, _bill_id = add_credit_purchase(
                user_id=user_id,
                card_id=card_id,
                valor=float(valor),
                categoria=categoria,
                nota=nota,
                purchased_at=purchased_at,
            )
            return (
                f"💳 Compra no crédito registrada: {fmt_brl(valor)}\n"
                f"📅 Data da compra: {fmt_br(purchased_at)}\n"
                f"📌 Fatura atual: {fmt_brl(due)}\n"
                f"ID crédito: CT#{tx_id}"
            )
        except Exception as e:
            return f"❌ Erro registrando compra no crédito: {e}"

    if t_low in ("criar parcelas", "criar parcela", "parcelas"):
        return "Use: `parcelar 300 em 3x no cartao nubank` (ex: `parcelar 120 em 4x no cartao nubank`)"

    if t_low.startswith("parcelei "):
        t_low = "parcelar " + t_low[len("parcelei "):]

    if t_low.startswith("parcelar"):
        valor = parse_money(t_low)
        if valor is None:
            return "Use: parcelar 300 em 3x no cartao nubank"

        n = 1
        mx = re.search(r"(\d+)\s*x", t_low)
        if mx:
            try:
                n = int(mx.group(1))
            except Exception:
                n = 1

        card_name = None
        m = re.search(r"(?:no\s+)?cart[aã]o\s+(.+)$", t_low)
        if m:
            card_name = m.group(1).strip()

        dt_evento, rest2 = extract_date_from_text(t)
        if dt_evento is None:
            dt_evento = now_tz()
        purchased_at = dt_evento.date()

        desc_clean = rest2
        desc_clean = re.sub(r"\bparcelei?\b", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = re.sub(r"\b\d+[\.,]?\d*\b", "", desc_clean)
        desc_clean = re.sub(r"\b\d+\s*x\b", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = re.sub(r"\bem\b", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = re.sub(r"\bno\s+cart[aã]o\s+\S+", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = re.sub(r"\bcart[aã]o\s+\S+", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = " ".join(desc_clean.split())

        nota = normalize_text(desc_clean) if desc_clean.strip() else normalize_text(t)
        categoria = _infer_category(user_id, desc_clean or t)

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            if card_name:
                return f"❌ Não achei o cartão '{card_name}'. Crie com: criar cartao {card_name} fecha 10 vence 17"
            return "❓ Qual cartão?\nEx: `parcelei 500 em 5x no cartao nubank`\nDica: defina um padrão com `padrao nubank`."

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
            result = ret[0] if isinstance(ret, tuple) else ret
            total = float(ret[1]) if isinstance(ret, tuple) and len(ret) >= 2 else float(valor)
            tx_ids = result.get("tx_ids") or []
            group_id = result.get("group_id")
            ids_str = ", ".join(f"#{x}" for x in tx_ids[:10]) if tx_ids else "(sem ids)"
            if len(tx_ids) > 10:
                ids_str += " ..."
            return (
                f"💳 Parcelado no cartão ({resolved_name}): R$ {float(valor):.2f} em {n}x\n"
                f"📌 Total lançado nas faturas: R$ {float(total):.2f}\n"
                f"Grupo: {group_id}\n"
                f"IDs: {ids_str}"
            )
        except Exception as e:
            return f"❌ Erro ao parcelar no cartão: {e}"

    if t_low.startswith("pagar fatura") or t_low.startswith("paguei fatura"):
        rest = re.sub(r"^paguei?\s+fatura", "", t, flags=re.IGNORECASE).strip()
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
            return "❓ Você não tem cartão padrão. Defina com: padrao NOME"

        try:
            bill_id = get_current_open_bill_id(user_id, card_id, today_tz())
            if not bill_id:
                return "📭 Nenhuma fatura aberta do período atual para pagar."

            res = pay_bill_amount(user_id, card_id, resolved_name, amount, bill_id=bill_id)
            if isinstance(res, dict) and res.get("error") == "amount_too_high":
                return (
                    "❌ Valor maior do que o em aberto.\n"
                    f"Em aberto: {fmt_brl(res['due'])} | Total: {fmt_brl(res['total'])} | Já pago: {fmt_brl(res['paid_amount'])}"
                )
            if isinstance(res, dict) and res.get("error") == "invalid_amount":
                return "❌ Valor inválido. Use: pagar fatura 300"
            if not res:
                return "📭 Nada para pagar."
            return (
                f"✅ Pagamento registrado: {fmt_brl(res['paid'])}\n"
                f"Conta agora: {fmt_brl(res['new_balance'])}\n"
                f"ID lançamento: #{res['launch_id']}"
            )
        except Exception as e:
            return f"❌ Erro ao pagar fatura: {e}"

    if t_low in ("faturas", "listar faturas", "faturas abertas", "listar faturas abertas", "listar fatura", "listar faturas em aberto"):
        try:
            rows = list_open_bills(user_id)
            if not rows:
                return "📭 Nenhuma fatura em aberto."

            months = [
                "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
            ]
            groups = defaultdict(list)
            for r in rows:
                total = float(r["total"] or 0)
                paid = float(r["paid_amount"] or 0)
                due = max(0.0, total - paid)
                if total == 0.0 and paid == 0.0 and due == 0.0:
                    continue
                ps = r["period_start"]
                groups[(ps.year, ps.month)].append((r, total, paid, due))

            if not groups:
                return "📭 Nenhuma fatura em aberto (as futuras zeradas foram ocultadas)."

            lines = ["🧾 **Faturas em aberto (por mês):**", ""]
            for (y, m) in sorted(groups.keys()):
                lines.append(f"📅 **{months[m-1]}/{y}:**")
                items = sorted(groups[(y, m)], key=lambda it: (it[0]["card_name"] or "").lower())
                for (r, total, paid, due) in items:
                    lines.append(f"• {r['card_name']}: Total {fmt_brl(total)} | Pago {fmt_brl(paid)} | Em aberto {fmt_brl(due)}")
                lines.append("")
            return "\n".join(lines).strip()
        except Exception as e:
            return f"❌ Erro ao listar faturas: {e}"

    if t_low.startswith("fatura ") or t_low == "fatura":
        parts = t.split()
        card_name = parts[1] if len(parts) >= 2 else None

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            return "❓ Você não tem cartão padrão. Defina com: `padrao NOME`."

        try:
            res = get_open_bill_summary(user_id, card_id, as_of=today_tz())
            if not res:
                return f"📭 Nenhuma fatura aberta para {resolved_name}."

            bill, items = res
            total = float(bill["total"] or 0)
            paid = float(bill.get("paid_amount", 0) or 0)
            due = max(0.0, total - paid)
            lines = [
                f"💳 Fatura atual ({resolved_name}) {fmt_br(bill['period_start'])} → {fmt_br(bill['period_end'])}",
                f"Total: {fmt_brl(total)} | Pago: {fmt_brl(paid)} | Em aberto: {fmt_brl(due)}",
                "",
            ]
            for it in items[:10]:
                parcela = ""
                if it.get("installment_no") and it.get("installments_total"):
                    parcela = f" [{it['installment_no']}/{it['installments_total']}]"
                lines.append(
                    f"• {fmt_brl(it['valor'])} | {it['categoria'] or 'outros'} | {fmt_br(it['purchased_at'])} | {it['nota'] or ''}{parcela}"
                )
            if len(items) > 10:
                lines.append(f"\n… e mais {len(items) - 10} lançamento(s).")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Erro ao buscar fatura: {e}"

    if t_low in ("parcelamentos", "listar parcelamentos"):
        rows = list_installment_groups(user_id, limit=15)
        if not rows:
            return "📭 Você não tem parcelamentos registrados."

        lines = ["📦 **Parcelamentos ativos:**"]
        for r in rows:
            n_total = int(r.get("n_total") or r.get("n_registered") or 0)
            n_pending = int(r.get("n_pending") or 0)
            if n_pending == 0:
                continue
            n_paid = n_total - n_pending
            total = float(r.get("total") or 0)
            pending = float(r.get("total_pending") or 0)
            nota = (r.get("nota") or "").strip()
            desc = f" — {nota}" if nota else ""
            progress = f"{n_paid}/{n_total} pagas"
            lines.append(
                f"• {r.get('card_name', '?')}{desc}\n"
                f"  💰 Total: {fmt_brl(total)} | Restante: {fmt_brl(pending)} ({progress})\n"
                f"  🔑 grupo: `{r.get('group_id') or ''}`"
            )

        if len(lines) == 1:
            return "✅ Você não tem parcelamentos em aberto."
        return "\n".join(lines)

    return None
