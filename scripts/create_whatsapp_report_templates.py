"""
scripts/create_whatsapp_report_templates.py

Monta e registra na Meta (WhatsApp Cloud API) os templates dos resumos
**semanal** e **mensal** — exatamente no formato que o bot já envia em
`adapters/whatsapp/wa_app.py` (`_periodic_template_named_body_params`):

    {{periodo}} {{saldo}} {{gastos}} {{receita}} {{lancamentos}}

Como o código envia **parâmetros nomeados**, o template PRECISA ser criado com
`example.body_text_named_params` (parâmetros nomeados), e não posicionais
(`{{1}}`, `{{2}}`, ...). Este script já monta assim.

Uso:
  cd "Bot Financeiro"

  # Só imprime os payloads que seriam enviados (não chama a Meta):
  python scripts/create_whatsapp_report_templates.py --dry-run

  # Cria os dois templates na Meta:
  python scripts/create_whatsapp_report_templates.py

  # Só um deles:
  python scripts/create_whatsapp_report_templates.py --only weekly
  python scripts/create_whatsapp_report_templates.py --only monthly

  # Com o botão de resposta rápida "Desligar resumo" (quick reply):
  python scripts/create_whatsapp_report_templates.py --stop-button

Env vars usadas:
  WA_TOKEN (ou WA_ACCESS_TOKEN)  — token com permissão whatsapp_business_management
  WA_BUSINESS_ACCOUNT_ID (WABA)  — ID da conta WhatsApp Business (não é o phone_number_id)
  WA_GRAPH_VERSION               — ex.: v25.0 (default v21.0)
  WA_WEEKLY_TEMPLATE_NAME        — nome do template semanal (default resumo_semanal)
  WA_MONTHLY_TEMPLATE_NAME       — nome do template mensal  (default resumo_mensal)
  WA_PROACTIVE_TEMPLATE_LANGUAGE — idioma (default pt_BR)

Depois de APROVADO pela Meta, basta setar WA_WEEKLY_TEMPLATE_NAME /
WA_MONTHLY_TEMPLATE_NAME no ambiente do bot que o envio automático liga sozinho
(semanal toda segunda, mensal todo dia 1º).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

import requests

DEFAULT_WEEKLY_NAME = "resumo_semanal"
DEFAULT_MONTHLY_NAME = "resumo_mensal"
DEFAULT_LANGUAGE = "pt_BR"

# payload que o bot usa no botão quick reply para desligar cada resumo
WA_WEEKLY_REPORT_DISABLE_ID = "weekly_report_disable"
WA_MONTHLY_REPORT_DISABLE_ID = "monthly_report_disable"

# corpo dos templates — espelha core/reports/reports_daily.py (build_*_report_text)
WEEKLY_BODY = (
    "📊 *Resumo semanal do Bot Financeiro*\n"
    "📅 Período: {{periodo}}\n"
    "\n"
    "🏦 Saldo atual: {{saldo}}\n"
    "📉 Gastos da semana: {{gastos}}\n"
    "📈 Receitas da semana: {{receita}}\n"
    "📊 Lançamentos da semana: {{lancamentos}}"
)

MONTHLY_BODY = (
    "📊 *Resumo mensal do Bot Financeiro*\n"
    "📅 Período: {{periodo}}\n"
    "\n"
    "🏦 Saldo atual: {{saldo}}\n"
    "📉 Gastos do mês: {{gastos}}\n"
    "📈 Receitas do mês: {{receita}}\n"
    "📊 Lançamentos do mês: {{lancamentos}}"
)

# exemplos exigidos pela Meta (um por variável nomeada)
WEEKLY_EXAMPLES = {
    "periodo": "27/07/2026 a 02/08/2026",
    "saldo": "R$ 1.000,00",
    "gastos": "R$ 150,00",
    "receita": "R$ 20,00",
    "lancamentos": "3",
}

MONTHLY_EXAMPLES = {
    "periodo": "01/07/2026 a 31/07/2026",
    "saldo": "R$ 1.000,00",
    "gastos": "R$ 999,90",
    "receita": "R$ 500,00",
    "lancamentos": "1",
}


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return default


def build_template_payload(
    *,
    name: str,
    header_text: str,
    body_text: str,
    examples: dict[str, str],
    language: str,
    stop_button_payload: str | None,
    footer_text: str = "Bot Financeiro",
) -> dict:
    """Monta o corpo do POST /{WABA_ID}/message_templates.

    Usa parâmetros NOMEADOS (body_text_named_params) porque é assim que o bot
    envia os valores em runtime (send_template(..., named_body_params=...)).
    """
    components: list[dict] = [
        {"type": "HEADER", "format": "TEXT", "text": header_text},
        {
            "type": "BODY",
            "text": body_text,
            "example": {
                "body_text_named_params": [
                    {"param_name": key, "example": value}
                    for key, value in examples.items()
                ]
            },
        },
        {"type": "FOOTER", "text": footer_text},
    ]

    if stop_button_payload:
        components.append(
            {
                "type": "BUTTONS",
                "buttons": [
                    {"type": "QUICK_REPLY", "text": "Desligar resumo"},
                ],
            }
        )

    return {
        "name": name,
        "language": language,
        "category": "UTILITY",
        "parameter_format": "NAMED",
        "components": components,
    }


def create_template(waba_id: str, token: str, base: str, payload: dict) -> tuple[bool, dict]:
    url = f"{base}/{waba_id}/message_templates"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    return r.ok, data


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria os templates de resumo (semanal/mensal) na Meta.")
    parser.add_argument("--dry-run", action="store_true", help="Só imprime os payloads, não chama a Meta")
    parser.add_argument("--only", choices=["weekly", "monthly"], help="Cria apenas um dos templates")
    parser.add_argument(
        "--stop-button",
        action="store_true",
        default=(os.getenv("WA_PERIODIC_TEMPLATE_STOP_BUTTON", "0").strip() == "1"),
        help='Inclui o botão de resposta rápida "Desligar resumo"',
    )
    parser.add_argument("--weekly-name", default=_env("WA_WEEKLY_TEMPLATE_NAME", default=DEFAULT_WEEKLY_NAME))
    parser.add_argument("--monthly-name", default=_env("WA_MONTHLY_TEMPLATE_NAME", default=DEFAULT_MONTHLY_NAME))
    parser.add_argument("--language", default=_env("WA_PROACTIVE_TEMPLATE_LANGUAGE", default=DEFAULT_LANGUAGE))
    args = parser.parse_args()

    language = args.language

    specs = []
    if args.only in (None, "weekly"):
        specs.append(
            (
                "weekly",
                build_template_payload(
                    name=args.weekly_name,
                    header_text="📊 Resumo semanal",
                    body_text=WEEKLY_BODY,
                    examples=WEEKLY_EXAMPLES,
                    language=language,
                    stop_button_payload=WA_WEEKLY_REPORT_DISABLE_ID if args.stop_button else None,
                ),
            )
        )
    if args.only in (None, "monthly"):
        specs.append(
            (
                "monthly",
                build_template_payload(
                    name=args.monthly_name,
                    header_text="📊 Resumo mensal",
                    body_text=MONTHLY_BODY,
                    examples=MONTHLY_EXAMPLES,
                    language=language,
                    stop_button_payload=WA_MONTHLY_REPORT_DISABLE_ID if args.stop_button else None,
                ),
            )
        )

    if args.dry_run:
        for kind, payload in specs:
            print(f"# ── template {kind} ──")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            print()
        print("[DRY-RUN] Nada foi enviado para a Meta.")
        return

    token = _env("WA_ACCESS_TOKEN", "WA_TOKEN")
    waba_id = _env("WA_BUSINESS_ACCOUNT_ID", "WA_WABA_ID", "WABA_ID")
    version = _env("WA_GRAPH_VERSION", default="v21.0")

    if not token:
        sys.exit("ERRO: defina WA_TOKEN (ou WA_ACCESS_TOKEN) no .env")
    if not waba_id:
        sys.exit("ERRO: defina WA_BUSINESS_ACCOUNT_ID (ID da conta WhatsApp Business) no .env")

    base = f"https://graph.facebook.com/{version}"

    ok_count = 0
    for kind, payload in specs:
        print(f"-> Criando template {kind}: {payload['name']} ({language}) ...")
        ok, data = create_template(waba_id, token, base, payload)
        if ok:
            ok_count += 1
            status = data.get("status", "?")
            print(f"   OK id={data.get('id')} status={status}")
        else:
            print(f"   ERRO: {json.dumps(data, ensure_ascii=False)}")

    print(f"\nCriados: {ok_count}/{len(specs)}")
    print("Aguarde a aprovação da Meta e então configure as env vars no bot:")
    print(f"  WA_WEEKLY_TEMPLATE_NAME={args.weekly_name}")
    print(f"  WA_MONTHLY_TEMPLATE_NAME={args.monthly_name}")


if __name__ == "__main__":
    main()
