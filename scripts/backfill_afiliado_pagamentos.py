"""Pagamentos de indicado na Stripe que o registro não tem, e os reembolsos antigos deles.

Completa `affiliate_referral_payments` com o que o `init_db` não alcança: fatura de plano sem
comissão (afiliado 0% ou desativado, renovação, webhook perdido) e reembolso anterior ao
`charge.refunded`. Idempotente: rodar de novo não duplica nem rebaixa rótulo.

Dinheiro: NÃO estorna comissão antiga e não toca em saque. Contestação fica só na contagem,
porque a cobrança não diz se foi perdida. Conta excluída não volta: sem titular não há cliente.
Só imprime contagens.

Rodar (o dono, na raiz do repositório, com a chave de produção):
    DATABASE_URL='postgres://...' STRIPE_SECRET_KEY='...' .venv/bin/python scripts/backfill_afiliado_pagamentos.py
Sem `--aplicar` é simulação: lê a Stripe e o banco e não grava nada.

Teste: tests/test_afiliado_reembolso.py.
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.services.afiliado_reembolso import tipo_do_reembolso  # noqa: E402
from core.services.cartao_recusado_por_pix import MARCA  # noqa: E402
from core.services.ebook_entrega import cobrancas_da_fatura  # noqa: E402
from core.services.extras_assinar import (  # noqa: E402
    _ler,
    assinatura_da_fatura,
    da_metadata,
    extras_liquido_cents,
    linhas_da_fatura,
)
from db.affiliate_payments import marcar_reembolso, registrar_pagamento  # noqa: E402
from db.connection import get_conn  # noqa: E402


def _clientes() -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            """
            select r.referred_user_id as uid, a.stripe_customer_id as cliente
              from affiliate_referrals r
              join auth_accounts a on a.user_id = r.referred_user_id
             where coalesce(a.stripe_customer_id, '') <> ''
            """).fetchall()


def _ja_registrado(fatura: str) -> bool:
    with get_conn() as conn:
        return conn.execute("select 1 from affiliate_referral_payments"
                            " where source = 'stripe' and payment_ref = %s", (fatura,)).fetchone() is not None


def _valor_do_plano(stripe, fatura) -> int:
    """O mesmo cálculo do webhook: a fatura menos os extras. 0 = não é pagamento de plano.

    Cartão recusado com Pix cobrindo fica fora, como no webhook, pela `MARCA` que a recusa
    grava na assinatura: `pix_cobre_agora` responde sobre hoje, não sobre a data da fatura."""
    id_assinatura = assinatura_da_fatura(fatura)
    if not id_assinatura:
        return 0
    assinatura = stripe.Subscription.retrieve(id_assinatura)
    if _ler(_ler(assinatura, "cancellation_details"), "comment") == MARCA:
        return 0
    precos = {p for p, _ in da_metadata(_ler(assinatura, "metadata"))}
    linhas = linhas_da_fatura(fatura) if precos else []
    return max(0, (_ler(fatura, "amount_paid") or 0) - extras_liquido_cents(linhas, precos))


def _reembolso(stripe, fatura_id: str, chave: str, n: Counter) -> tuple[str | None, datetime | None]:
    """O tipo e a data do reembolso mais recente da fatura."""
    tipos, datas = set(), []
    for cobranca in cobrancas_da_fatura(fatura_id, chave):
        tipo = tipo_do_reembolso(cobranca)
        tipos.add(tipo)
        n["contestadas_para_conferir"] += bool(cobranca["disputed"])
        if tipo:
            datas += [r["created"] for r in stripe.Refund.list(charge=cobranca["id"], limit=100)["data"]]
    quando = datetime.fromtimestamp(max(datas), timezone.utc) if datas else None
    return ("total" if "total" in tipos else "partial" if "partial" in tipos else None), quando


def _fatura(stripe, fatura, uid: int, chave: str, aplicar: bool, n: Counter) -> None:
    valor = _valor_do_plano(stripe, fatura)
    if valor <= 0:
        n["faturas_fora_da_regra"] += 1
        return
    fatura_id = fatura["id"]
    n["faturas_ja_no_registro" if _ja_registrado(fatura_id) else "faturas_novas"] += 1
    tipo, devolvido_em = _reembolso(stripe, fatura_id, chave, n)
    n[f"reembolsos_{tipo}"] += bool(tipo)
    if aplicar:
        pago_em = _ler(_ler(fatura, "status_transitions"), "paid_at") or fatura["created"]
        registrar_pagamento(uid, "stripe", fatura_id, valor, datetime.fromtimestamp(pago_em, timezone.utc))
        if tipo:
            marcar_reembolso("stripe", fatura_id, tipo, devolvido_em)


def rodar(aplicar: bool = False) -> Counter:
    """Falha de um cliente não para os outros; quem chama lê `clientes_com_falha`."""
    import stripe  # noqa: PLC0415

    chave = os.environ["STRIPE_SECRET_KEY"]
    stripe.api_key = chave
    n: Counter = Counter()
    for linha in _clientes():
        n["clientes"] += 1
        try:
            for fatura in stripe.Invoice.list(customer=linha["cliente"], status="paid", limit=100).auto_paging_iter():
                _fatura(stripe, fatura, int(linha["uid"]), chave, aplicar, n)
        except Exception as exc:  # noqa: BLE001 — o tipo basta; o texto pode trazer o id do cliente
            n["clientes_com_falha"] += 1
            n[f"erro_{type(exc).__name__}"] += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aplicar", action="store_true", help="grava; sem isto é simulação")
    args = ap.parse_args()
    if not os.getenv("STRIPE_SECRET_KEY"):
        print("STRIPE_SECRET_KEY ausente.", file=sys.stderr)
        return 2
    n = rodar(args.aplicar)
    print("aplicado" if args.aplicar else "simulação: nada gravado")
    for nome, valor in sorted(n.items()):
        print(f"{nome}: {valor}")
    return 1 if n["clientes_com_falha"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
