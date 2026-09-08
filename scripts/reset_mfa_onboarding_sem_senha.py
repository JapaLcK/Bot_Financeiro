#!/usr/bin/env python3
"""Devolve o convite de MFA às contas sem senha que já o gastaram.

Conta criada só via Google tem `auth_accounts.password_hash` NULL, e o setup de
MFA re-autentica por senha. O convite da /home é de UMA VEZ SÓ: qualquer coisa
que grave `mfa_onboarding_shown_at` o apaga para sempre — e o "Ativar agora"
gravava, então essas contas iam ao settings só para levar erro e voltavam sem
convite. O "Ativar agora" não marca mais (frontend/home.html) e o settings agora
tem "Definir senha" para elas (#232); este script repara o que já aconteceu,
zerando `mfa_onboarding_shown_at` das contas SEM senha.

O predicado NÃO distingue quem clicou "Ativar agora" de quem clicou "Agora não"
(nem do Esc, que roteia pelo dismiss): os três gravavam o mesmo timestamp. Então
o script PODE devolver o convite a quem dispensou de propósito. Separá-los
exigiria coluna nova, que o dono vetou; o custo do falso positivo é ver o overlay
mais uma vez, e agora ele leva a um lugar útil — o funil de senha do #232.

Também não se aperta o predicado com `mfa_onboarding_shown_at < <data do deploy>`:
sendo one-shot rodado logo APÓS o deploy, toda linha alvo já é pré-deploy e o
filtro seria redundante. Quem rodar isto meses depois perde essa garantia — aí a
data volta a fazer diferença e tem de entrar no `_ALVO`.

Conta COM senha nunca é tocada — e não porque o `shown_at` dela signifique "viu e
decidiu": quem tem senha e abandonou o setup no meio queimou o convite igual.
É que elas são a base inteira; alargar o predicado para elas re-incomodaria todo
mundo que dispensou legitimamente, risco muito maior que o reparo.

O UPDATE não chama `invalidate_auth_user_cache`, contrariando o "chame após
QUALQUER escrita em auth_accounts" de `db_support.py:541` — e não tem como: é
outro processo. Já verificado, é inofensivo: o `select` do cache
(`db_support.py:566-576`) não traz `mfa_onboarding_shown_at`, e o único leitor da
coluna (`should_show_mfa_onboarding`, `db/mfa.py:396`) vai ao banco direto.

É one-shot e idempotente: depois de aplicar, as linhas alvo ficam com
`mfa_onboarding_shown_at` NULL e deixam de casar com o WHERE — rodar de novo
reporta 0. Por isso mora aqui e não no `init_db()`, que é para estrutura de
schema, não para correção histórica de dados rodando em todo boot.

Uso:
    # PRÉ-REQUISITO: rode só DEPOIS do deploy do frontend. Nada impede a escrita
    # de acontecer de novo — uma aba da /home carregada ANTES do deploy ainda tem
    # o "Ativar agora" que marcava, e re-queima o convite que este script acabou
    # de devolver. A janela é só essa: o service worker não cacheia o HTML da
    # /home — ele sai antes de qualquer cache no `if (request.mode ===
    # "navigate") return;` (frontend/service-worker.js:103) —, então quem
    # recarregar já pega a versão nova.
    .venv/bin/python -m scripts.reset_mfa_onboarding_sem_senha            # dry-run (só conta)
    .venv/bin/python -m scripts.reset_mfa_onboarding_sem_senha --apply    # aplica
"""
from __future__ import annotations

import argparse

import db

# Uma fonte de verdade para o alvo: as duas queries usam LITERALMENTE o mesmo
# predicado, então o número do dry-run é o número que o --apply altera. Só o
# `where` mora aqui de propósito — com o `from auth_accounts` junto, o UPDATE
# viraria `update auth_accounts ... from auth_accounts`, que o Postgres NEM
# PARSA: `DuplicateAlias: table name "auth_accounts" specified more than once`.
# Estouraria só no --apply, porque o dry-run não usa o `from` — passando batido
# na revisão e quebrando na hora de reparar produção.
_ALVO = "where password_hash is null and mfa_onboarding_shown_at is not null"


def reparar(apply: bool = False) -> int:
    """Sem `apply`, conta o alvo sem escrever nada. Com `apply`, zera o
    `mfa_onboarding_shown_at` do alvo numa transação e devolve quantas linhas
    mudaram."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select count(*) as n from auth_accounts {_ALVO}")
        total = int(cur.fetchone()["n"])
        if not apply or not total:
            return total
        cur.execute(f"update auth_accounts set mfa_onboarding_shown_at = null {_ALVO}")
        alteradas = cur.rowcount
        conn.commit()
    return alteradas


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="aplica de verdade (sem isso, só conta — dry-run)")
    args = ap.parse_args()

    total = reparar(apply=args.apply)
    if args.apply:
        print(f"{total} conta(s) sem senha tiveram o convite de MFA devolvido.")
    else:
        print(f"{total} conta(s) sem senha com o convite de MFA queimado.")
        if total:
            print("Rode de novo com --apply para reparar.")


if __name__ == "__main__":
    main()
