#!/usr/bin/env python3
"""Items Pluggy sem conexão local: diagnóstico e apagamento pelo OPERADOR.

O operador só APAGA o item na Pluggy e pede a reconexão ao usuário. Não adota:
a adoção por script dobrava saldo com duplicata do mesmo banco (saiu em
`924aee3f`). A regra dos estados está no docstring de
`db.open_finance_diagnostico.classifica_item`.

Estados e procedimento:
  REMOVIDO         o usuário tirou o banco. GET 404: nada a fazer. Item VIVO na
                   Pluggy: a remoção remota falhou — apagar.
  INTERROMPIDO     adoção ou POST /pluggy-item que não completou. Avisar o
                   usuário, apagar, pedir reconexão. (Um disconnect numa janela
                   de ms também cai aqui; apagar não causa dano.)
  LEGADO_AMBIGUO   rastro anterior à marca de remoção, ou sem rastro com dono mas
                   com auditoria de conexão. Nunca recuperar sozinho: perguntar
                   ao usuário; apagar, e pedir reconexão se ele quiser o banco.
  NUNCA_ATRIBUIDO  o webhook viu, ninguém ficou com ele. O `--item` mostra o dono
                   remoto: conta inexistente ou com exclusão agendada → apagar
                   (LGPD); conta existe → apagar e pedir reconexão.
  remoção remota falhou (marca, soma-se ao estado): o log registra DELETE falho
                   na Pluggy e nenhum `operator_delete` depois. GET 404 =
                   resolvido; vivo → apagar.
  CONECTADO_SEM_RASTRO  conexão viva sem linha com dono no registry. Só
                   informativo; nenhuma escrita.
  DESCONHECIDO     nem o registry nem os logs conhecem o id (digitação, outra
                   caixa). Recusado; item real sem rastro é no painel da Pluggy.
Apagado pelo operador: sai da lista e do card do painel até aparecer rastro novo.
O rastro de log é APAGÁVEL (`DELETE /admin/api/events`, `admin.py purge`) e o log
com `user_id` na coluna some na exclusão da conta: aí a marca desaparece e o item
pode sair da ferramenta.
Contato com o dono: `admin.py inspect <uid> --reason`, com o uid do `--item`.

Registry vazio NÃO prova que não há órfão na Pluggy: o `GET /items` dela devolve
401 e o registry só tem o que o webhook viu.

Uso:
  .venv/bin/python -m scripts.of_itens_operador               # lista (dry-run)
  .venv/bin/python -m scripts.of_itens_operador --item ID     # detalhe + GET Pluggy
  .venv/bin/python -m scripts.of_itens_operador --item ID --apagar --estado ESTADO --apply
"""
from __future__ import annotations

import argparse
import sys

import httpx

import db
from core.services.pluggy import (
    PluggyApiError,
    PluggyConfigError,
    delete_pluggy_item,
    get_pluggy_item,
)
from db.open_finance_diagnostico import (
    CONECTADO,
    CONECTADO_SEM_RASTRO,
    DESCONHECIDO,
    ESTADOS_SEM_CONEXAO,
    apagados_pelo_operador,
    classifica_item,
    conexoes_sem_rastro,
    itens_com_remocao_remota_falha,
    listar_sem_conexao,
)
from db.open_finance_state import pluggy_item_lock, register_item
from scripts.of_itens_alvos import _dica_de_recusa, recusa_id

# O que a Pluggy pode levantar e não é bug nosso: HTTP de erro, rede/timeout,
# credencial ausente. Vira mensagem e rc=1, nunca traceback.
_FALHA_PLUGGY = (PluggyApiError, PluggyConfigError, httpx.HTTPError)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--item", metavar="ITEM_ID", help="um item só: detalhe com GET na Pluggy")
    ap.add_argument("--apagar", action="store_true",
                    help="apaga o item na Pluggy. Exige --item, --estado e --apply. Irreversível.")
    ap.add_argument("--estado", choices=ESTADOS_SEM_CONEXAO,
                    help="o estado que o dry-run mostrou; mudou desde então → recusa")
    ap.add_argument("--apply", action="store_true", help="escreve de verdade")
    args = ap.parse_args(argv)
    # Fronteira de confiança em caminho irreversível, ANTES de abrir o banco.
    # `is not None`: `--item ""` é digitação errada, não "sem item".
    if args.item is not None and (motivo := recusa_id(args.item)):
        dica = ('  Id vazio costuma ser `--item "$VAR"` com a variável não setada.'
                if not args.item.strip() else _dica_de_recusa())
        ap.error(f"--item {args.item!r} {motivo}.{dica}")
    if args.apagar and (args.item is None or not args.apply or args.estado is None):
        ap.error("--apagar exige --item ID, --estado ESTADO e --apply.")
    if args.apply and not args.apagar:
        ap.error("--apply só vale com --apagar.")
    return args


def listar() -> None:
    sem_conexao = listar_sem_conexao()
    falhas = itens_com_remocao_remota_falha()
    # A régua vale também para o que o registry trouxe: id que o `--item` recusa
    # não é contado nem prescrito.
    recusados = sorted(i for i in {*sem_conexao, *falhas} if recusa_id(i))
    if recusados:
        print(f"{len(recusados)} id(s) FORA da lista — nenhum comando os aceita: "
              + ", ".join(map(repr, recusados)) + _dica_de_recusa())
    for estado in ESTADOS_SEM_CONEXAO:
        itens = [i for i, e in sem_conexao.items() if e == estado and i not in recusados]
        if itens:
            print(f"{estado} ({len(itens)}): {', '.join(itens)}")
    falhas = [i for i in falhas if i not in recusados]
    if falhas:
        print(f"remoção remota falhou ({len(falhas)}): "
              + ", ".join(f"{i} [{classifica_item(i)}]" for i in falhas))
    if sem_rastro := conexoes_sem_rastro():
        print(f"{CONECTADO_SEM_RASTRO} ({len(sem_rastro)}), só informativo: "
              + ", ".join(sem_rastro))
    if apagados := apagados_pelo_operador():
        print(f"{apagados} apagado(s) pelo operador (fora da lista até novo rastro).")
    if not (sem_conexao or falhas or sem_rastro):
        print("Nada no registry nem nos logs. Isso NÃO prova que não há órfão na Pluggy.")
    print("Detalhe de um item: --item ID. Procedimento por estado: --help.")


def detalhe(item: str) -> int:
    estado = classifica_item(item)
    marca = item in itens_com_remocao_remota_falha()
    print(f"{item}: {estado}" + (" + remoção remota falhou" if marca else ""))
    if estado in (CONECTADO, CONECTADO_SEM_RASTRO):
        print("  conexão local viva: nada a fazer aqui (a remoção é pelo app).")
        return 0
    if estado == DESCONHECIDO:
        print("  nem o registry nem os logs conhecem este id (confira a caixa e o id)."
              " Esta ferramenta não age nele; item real sem rastro é no painel da Pluggy.")
        return 0
    comando = f"--item {item} --apagar --estado {estado} --apply"
    try:
        remoto = get_pluggy_item(item)
    except PluggyApiError as exc:
        if exc.status_code == 404:
            print("  Pluggy: 404, o item não existe lá. Nada a apagar.")
            if marca:
                # Sem isto a marca ficava eterna. O DELETE é idempotente (404 =
                # sucesso) e grava o `operator_delete` que a fecha.
                print(f"  Para fechar a marca de falha: {comando}")
            return 0
        print(f"  Pluggy: erro ({exc}). Tente de novo.")
        return 1
    except _FALHA_PLUGGY as exc:
        print(f"  Pluggy inalcançável ({type(exc).__name__}: {exc}). Tente de novo.")
        return 1
    dono = str(remoto.get("clientUserId") or "")
    conector = (remoto.get("connector") or {}).get("name")
    print(f"  Pluggy: VIVO, status={remoto.get('status')} banco={conector} dono={dono or '?'}")
    if not dono.isdigit():
        print("  dono remoto não é um user_id nosso.")
    elif not db.user_exists(int(dono)):
        print("  conta do dono: NÃO existe.")
    elif db.is_account_scheduled_for_deletion(int(dono)):
        print("  conta do dono: exclusão AGENDADA.")
    else:
        print("  conta do dono: existe.")
    print(f"  Apagar: {comando}")
    return 0


def apagar(item: str, estado_esperado: str) -> int:
    """Reclassificação e DELETE dentro do lock do item: uma conexão que o POST ou
    a adoção gravem sob o mesmo lock aparece aqui antes do DELETE, nunca depois.
    A checagem de conexão é a própria `classifica_item` (CONECTADO*), e esses
    estados nunca casam com `--estado`. Sobra a janela externa (POST cujo GET na
    Pluggy já passou e ainda não pegou o lock) — o job de saúde marca `item_missing`.
    """
    with pluggy_item_lock(item) as locked:
        if not locked:
            print(f"{item}: lock do item ocupado (sync em andamento). Tente de novo.")
            return 1
        atual = classifica_item(item)
        if atual in (CONECTADO, CONECTADO_SEM_RASTRO):
            print(f"{item}: tem conexão LOCAL viva. Recusado — a remoção é pelo app "
                  "(DELETE /open-finance/{uid}).")
            return 1
        if atual == DESCONHECIDO:
            print(f"{item}: id desconhecido (nem registry nem logs). Recusado.")
            return 1
        if atual != estado_esperado:
            print(f"{item}: o estado mudou ({estado_esperado} → {atual}). "
                  "Recusado; rode o --item de novo.")
            return 1
        try:
            delete_pluggy_item(item)   # 404 conta como sucesso (idempotente)
        except _FALHA_PLUGGY as exc:
            print(f"{item}: o DELETE na Pluggy falhou ({type(exc).__name__}: {exc})."
                  " Nada gravado.")
            return 1
    try:
        register_item(None, provider_item_id=item, origin="operator_delete",
                      last_event="operator_delete")
    except Exception as exc:  # noqa: BLE001 — o DELETE já aconteceu: diga isso
        print(f"{item}: APAGADO na Pluggy, mas o rastro `operator_delete` falhou "
              f"({type(exc).__name__}). A marca de falha, se houver, continua na lista.")
        return 1
    print(f"{item}: apagado na Pluggy. Peça a reconexão ao usuário, se for o caso.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.apagar:
        return apagar(args.item, args.estado)
    if args.item is not None:
        return detalhe(args.item)
    listar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
