"""
scripts/send_onboarding_missed_plans.py — envio ÚNICO pros usuários Grátis que
caíram direto no dashboard sem passar pelos planos (bug de onboarding).

O banco de prod não é acessível do Mac (host interno postgres.railway.internal só
resolve dentro da Railway). Então a lista de destinatários vem de um ARQUIVO
exportado do DBeaver, e o script só ENVIA (Resend é HTTPS externo, roda do Mac via
`railway run`, que injeta o RESEND_API_KEY).

Passos:
  1) No DBeaver rode e exporte user_id + email dos free (query no fim deste arquivo)
     para um arquivo, ex.: /tmp/free.tsv  (uma linha por conta: "user_id<TAB>email")
  2) Dry-run:  railway run .venv/bin/python scripts/send_onboarding_missed_plans.py --emails-file /tmp/free.tsv
  3) Enviar:   railway run .venv/bin/python scripts/send_onboarding_missed_plans.py --emails-file /tmp/free.tsv --send

>>> DRY-RUN por padrão: só lista. Pra enviar de verdade, --send. Rode UMA vez.  <<<
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _parse_recipients(path: str) -> list[tuple[int | None, str]]:
    """Lê 'user_id<sep>email' por linha (sep = tab, ; ou ,). Pula header e linhas
    sem e-mail válido."""
    out: list[tuple[int | None, str]] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip().strip('"')
            if not line:
                continue
            parts = None
            for sep in ("\t", ";", ","):
                if sep in line:
                    parts = [p.strip().strip('"') for p in line.split(sep)]
                    break
            if parts is None:
                parts = [line]
            email = next((p for p in parts if "@" in p), None)
            if not email:
                continue  # header ou linha inválida
            uid = next((int(p) for p in parts if p.isdigit()), None)
            out.append((uid, email))
    return out


def _build_and_send(es, email: str, uid: int | None, dash: str):
    unsub = es.make_unsub_url(uid, email) if uid is not None else ""
    content = f"""
      <p>Oi! Piggy aqui. 🐷</p>
      <p>Descobrimos um errinho nosso: quando você criou sua conta, a gente te levou
         direto pro dashboard e pode ter deixado você sem ver os <strong>planos</strong>
         — e o <strong>teste grátis de 30 dias</strong> do Plus.</p>
      <p>Já corrigimos! E aqui vai o que você pode ter perdido:</p>
      <ul>
        <li><strong>Open Finance</strong> — conecta seu banco e o saldo atualiza sozinho</li>
        <li><strong>Agentes do Piggy</strong> — Xerife, Repórter e Carteiro trabalhando por você</li>
        <li><strong>Histórico ilimitado</strong>, caixinhas e cartões sem limite</li>
      </ul>
      <p>Dá pra <strong>testar 30 dias grátis</strong> — cancela quando quiser.</p>
      <p style="text-align:center;margin:24px 0"><a class="btn" href="{dash}/precos">Ver os planos</a></p>
      <p class="sig">Desculpa o tropeço!<br/><strong>Piggy 🐷</strong></p>
    """
    html = es._piggy_html("Você pode ter perdido os planos", content, unsub)
    text = (
        "Oi! Piggy aqui.\n\n"
        "Descobrimos um errinho: no seu cadastro você foi direto pro dashboard e pode não "
        "ter visto os planos e o teste grátis de 30 dias do Plus. Dá uma olhada: "
        f"{dash}/precos\n\nDesculpa o tropeço! Piggy"
    )
    headers = es.unsub_headers(unsub) if unsub else {}
    return es.send_email(
        to=email,
        subject="🐷 Você pode ter perdido os planos do PigBank",
        html_body=html,
        text_body=text,
        from_addr=es.EMAIL_FROM_PIGGY,
        headers=headers or None,
    )


def main():
    argv = sys.argv[1:]
    send = "--send" in argv
    path = None
    if "--emails-file" in argv:
        path = argv[argv.index("--emails-file") + 1]
    if not path:
        print("ERRO: passe --emails-file <arquivo> (exportado do DBeaver).")
        sys.exit(2)

    from core.services import email_service as es
    dash = os.getenv("DASHBOARD_URL", "https://pigbankai.com").rstrip("/")

    recipients = _parse_recipients(path)
    print(f"[{'ENVIO REAL' if send else 'DRY-RUN'}] {len(recipients)} destinatário(s) no arquivo")
    sent = fail = 0
    for uid, email in recipients:
        if not send:
            print(f"  [dry] enviaria → {email} (user_id={uid})")
            continue
        ok = _build_and_send(es, email, uid, dash)
        if ok:
            sent += 1
            print(f"  enviado → {email}")
        else:
            fail += 1
            print(f"  FALHOU → {email}")
    if send:
        print(f"\nResumo: enviados={sent} · falhas={fail}")
    else:
        print("\n(dry-run — nada foi enviado. Rode de novo com --send pra mandar.)")


if __name__ == "__main__":
    main()

# ── Query pro DBeaver (exporte user_id + email pra um arquivo) ────────────────
# SELECT user_id, email
# FROM auth_accounts
# WHERE engagement_opt_out = false
#   AND email IS NOT NULL
#   AND COALESCE(plan, 'free') = 'free';
