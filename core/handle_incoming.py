from core.types import IncomingMessage
from core.help_text import HELP_TEXT_SHORT, HELP_TEXT_FULL, TUTORIAL_TEXT
from core.services.ofx_service import handle_ofx_import

def handle_incoming(msg: IncomingMessage) -> dict:
    t = (msg.text or "").strip()
    t_low = t.lower().strip()

    # HELP / TUTORIAL
    if t_low in {"ajuda", "help"}:
        return {"text": HELP_TEXT_FULL}
    if t_low == "tutorial":
        return {"text": TUTORIAL_TEXT}

    # OFX (se o user digitou importar ofx e mandou anexo)
    if "importar ofx" in t_low:
        if not msg.attachments:
            return {"text": "Envie `importar ofx` junto com o arquivo `.ofx` anexado."}

        # pega o primeiro ofx
        ofx_att = None
        for a in msg.attachments:
            if a.filename.lower().endswith(".ofx") or "ofx" in a.content_type.lower():
                ofx_att = a
                break

        if not ofx_att:
            return {"text": "Não achei um `.ofx` no anexo. Envie um arquivo OFX, por favor."}

        report = handle_ofx_import(msg.user_id, ofx_att.data, ofx_att.filename)

        # monta resposta humana (exemplo)
        periodo = f"{report.get('dt_start')} → {report.get('dt_end')}"
        total = report.get("total_in_file")
        ins = report.get("inserted")
        dup = report.get("duplicates")
        saldo = report.get("new_balance") or report.get("balance")
        return {
            "text": (
                "✅ **OFX importado**\n"
                f"📅 Período: {periodo}\n"
                f"🧾 Transações no arquivo: {total}\n"
                f"➕ Inseridas: {ins} | ♻️ Duplicadas: {dup}\n"
                f"🏦 Saldo atual: R$ {saldo}\n"
            ),
            "debug": report,
        }

    # TODO: aqui você liga seus parsers existentes (gastei/recebi/caixinhas/etc).
    # Por enquanto, fallback:
    return {"text": HELP_TEXT_SHORT}