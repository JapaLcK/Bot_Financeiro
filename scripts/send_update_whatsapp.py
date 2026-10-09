"""
scripts/send_update_whatsapp.py

Envia a mensagem de novidades do PigBank via template aprovado do WhatsApp.

Uso:
  cd "Bot Financeiro"
  python scripts/send_update_whatsapp.py --dry-run

  # Enviar apenas para um número de teste:
  python scripts/send_update_whatsapp.py --test 5511999999999

  # Enviar apenas para o WhatsApp vinculado a um e-mail:
  python scripts/send_update_whatsapp.py --test seu@email.com
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

import db
from adapters.whatsapp.wa_client import send_template
from utils_phone import mask_phone, normalize_phone_e164, phone_lookup_candidates

DEFAULT_TEMPLATE_NAME = "atualizacao_pigbank"
DEFAULT_TEMPLATE_LANGUAGE = "pt_BR"
WA_UPDATES_DISABLE_ID = "whatsapp_updates_disable"


@dataclass(frozen=True)
class UpdateTarget:
    user_id: int
    to: str
    email: str = ""
    source: str = "phone"
    raw: str = ""  # `external_id` como está gravado, para a reconferência achar a própria linha


def _env_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_whatsapp_target(raw: str) -> tuple[str, set[str]]:
    candidates = set(phone_lookup_candidates(raw))
    return max(candidates, key=len), candidates


def _dedupe_targets(rows: list[dict]) -> list[UpdateTarget]:
    targets: list[UpdateTarget] = []
    seen: set[str] = set()
    ambiguos = 0

    # Opt-out vale para o número: barra todas as variantes dele, em qualquer conta.
    for row in rows:
        if row.get("opt_out"):
            try:
                seen.update(_normalize_whatsapp_target(row["identity_phone"].strip())[1])
            except ValueError:
                pass

    for row in rows:
        user_id = int(row["user_id"])
        email = (row.get("email") or "").strip()
        raw_phone = (row.get("identity_phone") or "").strip()
        if not raw_phone:
            continue

        try:
            normalized, candidates = _normalize_whatsapp_target(raw_phone)
        except ValueError:
            print(f"  ! telefone invalido ignorado user_id={user_id}: {mask_phone(raw_phone)}")
            continue

        # #901: o `wa_id` da Meta traz o código do país. Com 10/11 dígitos (o que não
        # normaliza sem o 55 padrão) é ambíguo: brasileiro antigo sem 55 ou estrangeiro
        # que viraria um número brasileiro de outra pessoa. Falha fechado.
        try:
            normalize_phone_e164(raw_phone, default_country_code="")
        except ValueError:
            ambiguos += 1
            continue

        if seen.intersection(candidates):
            continue

        seen.update(candidates)
        targets.append(
            UpdateTarget(
                user_id=user_id,
                to=normalized,
                email=email,
                source="whatsapp",
                raw=row["identity_phone"],
            )
        )

    if ambiguos:
        print(f"  ! {ambiguos} destinatário(s) com número ambíguo (10/11 dígitos) ignorado(s), ver #901")
    return targets


def _sem_exclusao_pedida(rows: list[dict]) -> list[dict]:
    # Conta com exclusão pedida não recebe. A que também tem opt-out fica: ela não
    # recebe (o opt-out barra o número) e continua barrando as variantes nas outras.
    # ponytail: uma consulta por linha, para reusar a fonte única
    # (`db.is_account_scheduled_for_deletion`); vira join se o volume pesar.
    return [
        row for row in rows
        if row.get("opt_out") or not db.is_account_scheduled_for_deletion(int(row["user_id"]))
    ]


def get_all_update_targets(numeros: list[str] | None = None) -> list[UpdateTarget]:
    """`numeros` restringe às identidades com esses números: é a reconferência
    antes de cada envio, com a mesma regra (opt-out por número, exclusão pedida)."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select
              a.user_id,
              a.email,
              i.external_id as identity_phone,
              coalesce(a.whatsapp_updates_opt_out, false) as opt_out
            from auth_accounts a
            join user_identities i
              on i.user_id = a.user_id
             and i.provider = 'whatsapp'
            where nullif(i.external_id, '') is not null
              and (%(numeros)s::text[] is null or i.external_id = any(%(numeros)s::text[]))
            order by a.user_id asc
            """,
            {"numeros": numeros},
        )
        rows = cur.fetchall() or []
    return _dedupe_targets(_sem_exclusao_pedida(rows))


def get_test_targets(value: str) -> list[UpdateTarget]:
    value = (value or "").strip()
    if not value:
        return []

    if "@" not in value:
        normalized, _ = _normalize_whatsapp_target(value)
        return [UpdateTarget(user_id=0, to=normalized, source="test")]

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select
              a.user_id,
              a.email,
              i.external_id as identity_phone
            from auth_accounts a
            join user_identities i
              on i.user_id = a.user_id
             and i.provider = 'whatsapp'
            where lower(a.email) = lower(%s)
              and nullif(i.external_id, '') is not null
            order by a.user_id asc
            """,
            (value,),
        )
        rows = cur.fetchall() or []
    return _dedupe_targets(_sem_exclusao_pedida(rows))


def build_quick_reply_buttons(enabled: bool) -> list[dict] | None:
    if not enabled:
        return None
    return [{"index": 0, "payload": WA_UPDATES_DISABLE_ID}]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Lista destinatários sem enviar")
    parser.add_argument("--test", metavar="DESTINO", help="Envia apenas para este número ou e-mail")
    parser.add_argument("--limit", type=int, default=0, help="Limita a quantidade de envios")
    parser.add_argument(
        "--template",
        default=os.getenv("WA_UPDATE_TEMPLATE_NAME", DEFAULT_TEMPLATE_NAME),
        help="Nome do template aprovado no WhatsApp",
    )
    parser.add_argument(
        "--language",
        default=os.getenv("WA_UPDATE_TEMPLATE_LANGUAGE", DEFAULT_TEMPLATE_LANGUAGE),
        help="Código de idioma do template",
    )
    parser.add_argument(
        "--stop-button",
        action="store_true",
        default=_env_flag("WA_UPDATE_TEMPLATE_STOP_BUTTON"),
        help="Envia payload para botão quick reply de parar atualizações",
    )
    args = parser.parse_args()

    test_value = (args.test or "").strip()
    direct_test_number = bool(test_value and "@" not in test_value)
    targets = get_test_targets(test_value) if test_value else get_all_update_targets()
    if args.limit > 0:
        targets = targets[: args.limit]

    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(f"{prefix}Template: {args.template} ({args.language})")
    if direct_test_number:
        print(f"{prefix}Destinatários informados para teste: {len(targets)}")
        print(f"{prefix}Aviso: número passado em --test não é validado no banco.")
    elif test_value:
        print(f"{prefix}Destinatários encontrados para o e-mail de teste: {len(targets)}")
        if not targets:
            print(f"{prefix}Aviso: nenhuma conta ativa com este e-mail tem WhatsApp ligado; nada será enviado.")
    else:
        print(f"{prefix}Destinatários encontrados na base: {len(targets)}")
    print()

    ok = 0
    fail = 0
    pulados = 0
    buttons = build_quick_reply_buttons(args.stop_button)

    for target in targets:
        label = f"user_id={target.user_id} to={mask_phone(target.to)} source={target.source}"
        if target.email:
            label += f" email={target.email}"

        if args.dry_run:
            print(f"  -> {label}")
            continue

        try:
            # Opt-out (Configurações ou botão de atualização anterior) ou exclusão pedidos
            # durante a execução valem para quem ainda não recebeu. Limite: número fixo de
            # 12 dígitos (local 2-5) não gera a forma de 13, então o opt-out feito nela
            # durante a execução não é visto aqui (a montagem da lista vê).
            if not test_value and not get_all_update_targets(
                sorted(_normalize_whatsapp_target(target.to)[1] | {target.raw})
            ):
                print(f"  PULADO (opt-out ou exclusão pedida) {label}")
                pulados += 1
                continue

            # `None` é o 401 da Meta (token inválido), que `send_template` não levanta.
            if send_template(
                target.to,
                args.template,
                language_code=args.language,
                quick_reply_buttons=buttons,
            ) is None:
                raise RuntimeError("envio recusado (token do WhatsApp inválido)")
            print(f"  OK {label}")
            ok += 1
        except Exception as exc:
            print(f"  ERRO {label}: {exc}")
            fail += 1

    if not args.dry_run:
        print(f"\nEnviados: {ok} | Falhas: {fail} | Pulados: {pulados}")


if __name__ == "__main__":
    main()
