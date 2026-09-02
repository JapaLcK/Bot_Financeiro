"""Mede, contra produção, quantas linhas de `launches` o #214 passa a RECUSAR
que o `b4d0085` (comportamento de hoje) apagava.

Roda a guarda de VERDADE, em Python (`_validar_efeitos`, `db/accounts.py`), numa
amostra de `efeitos` lida do banco. NÃO reimplementa a regra em SQL: o `falsy`
do SQL não é o do Python (`[]`, `{}`, `0.00` são recusados aqui e invisíveis lá)
e duas fontes de verdade da mesma regra é o que `CLAUDE.md` §0.7 proíbe.

SEGURANÇA
  - conexão read-only pelo SERVIDOR (`default_transaction_read_only=on`), não
    pela disciplina do script; sai com código 2 se o servidor não confirmar;
  - um SELECT só (`tipo`, `efeitos`), cursor server-side, `rollback` no fim;
  - a saída é SÓ contagem agregada. Nenhum `user_id`, `launch_id`, nome,
    valor ou pedaço de payload sai daqui — nem em amostra, nem em exemplo.
  - é a única query deste repositório sem `WHERE user_id` de propósito
    (`CLAUDE.md` §0): a pergunta é agregada sobre a base inteira, e o que sai
    do processo é contagem, não linha.

O QUE ESTA MEDIÇÃO **NÃO** RESPONDE
  1. "b4d0085 apaga" aqui = "passou no PRÉ-VOO do b4d0085". Parte dessas linhas
     estourava conversão CRUA mais adiante na reversão (balde `errors`, e
     SILÊNCIO nos três `except Exception: pass` de `db/open_finance.py`) em vez
     de apagar. A coluna `b4d0085 apaga / #214 recusa` é portanto um TETO do
     falso positivo, não o número exato.
  2. O #214 tem uma recusa FORA de `_validar_efeitos`: o `rowcount==0` do delete
     de `investment_lots` (`lot_id` bem formado que não casa lote do usuário).
     Ela não é medida aqui — exigiria ler `investment_lots`. O falso positivo
     real pode ser MAIOR que o daqui, por essa via.
  3. Nada do corpo da reversão é exercitado (saldos, faturas, lotes,
     `InvestmentLotHasWithdrawal`). Isto mede o PRÉ-VOO, não o delete.
  4. Não diz QUAIS linhas nem de quem — por requisito do dono.
  5. É um retrato do instante da leitura.

USO
    DATABASE_URL='<dsn de produção>' \
    PYTHONPATH=. python scripts/medir_guarda_efeitos_214.py

    python scripts/medir_guarda_efeitos_214.py --autoteste   # sem banco

ponytail: artefato de medição descartável. Apagar quando o #214 for decidido.
"""
import json
import os
import re
import sys
from collections import Counter
from decimal import Decimal

# ---------------------------------------------------------------------------
# GUARDA CONGELADA — `git show b4d0085:db/accounts.py`, pré-voo de
# `delete_launch_and_rollback`. Cópia literal, ARTEFATO DE MEDIÇÃO: não é
# segunda fonte de verdade de produto, não é para ser importada por nada, e
# morre junto com este arquivo. Ela existe porque o script roda num checkout
# que este PR não controla, então `import` do commit velho não é opção.
# As três constantes abaixo são cópias verificadas idênticas nos dois commits
# (`diff` de `_EFEITOS_REVERSIVEIS`/`_DELTA_EXIGE_LOTE`/`_EFEITOS_FORA_...`).
# ---------------------------------------------------------------------------
_F_REVERSIVEIS = frozenset({
    "delta_conta", "bill_id", "paid_amount_added",
    "create_pocket", "create_investment", "delete_pocket", "delete_investment",
    "delta_pocket", "delta_invest",
    "investment_lot_create", "investment_lot_withdrawals",
    "funding_source", "tax_summary", "investment_meta",
    "ofx", "open_finance", "time_known",
})
_F_DELTA_EXIGE_LOTE = (
    ("delta_pocket", ("pocket_lot_create", "pocket_lot_withdrawals")),
    ("delta_invest", ("investment_lot_create", "investment_lot_withdrawals")),
)
_F_FORA_DO_APAGAR_TUDO = (
    "create_pocket", "create_investment", "delete_pocket", "delete_investment",
    "delta_pocket", "delta_invest",
)
_F_CAMPOS_EXIGIDOS = (
    ("investment_lot_create", ("lot_id",)),
    ("investment_lot_withdrawals", ("lot_id", "before")),
    ("create_pocket", ("nome",)),
    ("create_investment", ("nome",)),
    ("delete_pocket", ("nome",)),
    ("delete_investment", ("nome",)),
)
_F_BEFORE_CAMPOS = ("balance", "principal_remaining")


class _RecusaVelha(Exception):
    def __init__(self, motivo, campo=""):
        super().__init__(motivo)
        self.motivo, self.campo = motivo, campo


def _guarda_b4d0085(efeitos, *, escopo_conta_corrente):
    if "delta_conta" not in efeitos:
        raise _RecusaVelha("sem_delta_conta", "delta_conta")
    if set(efeitos) - _F_REVERSIVEIS:
        raise _RecusaVelha("chave_desconhecida")
    for chave, campos in _F_CAMPOS_EXIGIDOS:
        valor = efeitos.get(chave)
        if valor is None:
            continue
        lista = chave == "investment_lot_withdrawals"
        if isinstance(valor, list) != lista:
            raise _RecusaVelha("efeito_incompleto", chave)
        for item in (valor if lista else [valor]):
            if not isinstance(item, dict):
                raise _RecusaVelha("efeito_incompleto", chave)
            faltando = [c for c in campos if not item.get(c)]
            if not faltando and "before" in campos:
                antes = item.get("before")
                if not isinstance(antes, dict) or any(
                    antes.get(c) is None for c in _F_BEFORE_CAMPOS
                ):
                    faltando = ["before"]
            if faltando:
                raise _RecusaVelha("efeito_incompleto", chave)
    if (efeitos.get("bill_id") is None) != (efeitos.get("paid_amount_added") is None):
        raise _RecusaVelha("efeito_incompleto", "bill_id")
    delta_conta = Decimal(str(efeitos.get("delta_conta", 0)))
    for delta_key, lot_keys in _F_DELTA_EXIGE_LOTE:
        delta_val = efeitos.get(delta_key)
        if not isinstance(delta_val, dict):
            continue
        if Decimal(str(delta_val.get("delta") or 0)) == 0 and delta_conta == 0:
            continue
        if not any(efeitos.get(k) for k in lot_keys):
            raise _RecusaVelha("lote_ausente", delta_key)
    if escopo_conta_corrente and any(
        efeitos.get(k) is not None for k in _F_FORA_DO_APAGAR_TUDO
    ):
        raise _RecusaVelha("fora_do_escopo")
    return delta_conta


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------
# `escopo_conta_corrente=True` é usado SÓ por `delete_all_launches_and_rollback`,
# que lê `where tipo in ('despesa','receita')` (`_CONTA_CORRENTE_LAUNCH_FILTER`).
# Fora desse filtro o `fora_do_escopo` seria ruído — por isso a tabela do
# "apagar tudo" só conta essas linhas.
_TIPOS_APAGAR_TUDO = ("despesa", "receita")

# Só a FORMA do caminho ('bill_id', 'delete_pocket.nome'). O `_validar_efeitos`
# já não põe VALOR na mensagem; o filtro é o cinto: se o texto entre aspas não
# for um caminho de esquema, sai '?' em vez de sair para o terminal do dono.
_CAMPO_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,60}$")


def _campo_da_msg(msg):
    m = re.search(r"'([^']*)'", str(msg))
    campo = m.group(1) if m else ""
    return campo if _CAMPO_OK.match(campo) else "?"


def _normalizar(efeitos):
    """Mesma normalização de `delete_launch_and_rollback` — inclusive o
    `json.loads` do jsonb string. `None` = cai em `LaunchNoEffects` lá."""
    if isinstance(efeitos, str):
        try:
            efeitos = json.loads(efeitos)
        except ValueError:
            return None
    return efeitos if isinstance(efeitos, dict) else None


def classificar(efeitos, tipo):
    """(resultado_velho, resultado_novo) para um `efeitos` cru do banco.

    Cada resultado é ('apaga'|'recusa'|'erro', motivo, campo). 'erro' é
    conversão CRUA no pré-voo — no `delete_all` cai no balde `errors`, não
    apaga e não vira frase de produto."""
    from db.accounts import LaunchUnsafeRollback, _validar_efeitos

    ef = _normalizar(efeitos)
    if ef is None:
        return ("sem_efeitos", "", ""), ("sem_efeitos", "", "")
    escopo = tipo in _TIPOS_APAGAR_TUDO

    def rodar(fn):
        try:
            fn(ef, escopo_conta_corrente=escopo)
            return ("apaga", "", "")
        except (_RecusaVelha, LaunchUnsafeRollback) as e:
            campo = getattr(e, "campo", None) or _campo_da_msg(e)
            return ("recusa", e.motivo, campo)
        except Exception as e:  # noqa: BLE001 — conversão crua = balde `errors`
            return ("erro", type(e).__name__, "")

    return rodar(_guarda_b4d0085), rodar(_validar_efeitos)


def medir(cur):
    """Consome o cursor e devolve (cruzamento, detalhe, total).

    O registro individual é DESCARTADO logo depois de contado: nada acumula em
    lista, nem para amostra."""
    cruz, detalhe, total = Counter(), Counter(), 0
    for tipo, efeitos in cur:
        total += 1
        velho, novo = classificar(efeitos, tipo)
        alvo = "tudo" if tipo in _TIPOS_APAGAR_TUDO else "singular"
        cruz[(alvo, velho[0], novo[0])] += 1
        if novo[0] == "recusa":   # `sem_efeitos` ja tem linha propria
            detalhe[(alvo, velho[0], novo[0], novo[1], novo[2])] += 1
        del velho, novo, efeitos, tipo
    return cruz, detalhe, total


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------
_LEGENDA = {
    ("apaga", "recusa"): "b4d0085 APAGA / #214 RECUSA   <-- teto do falso positivo",
    ("apaga", "erro"): "b4d0085 apaga  / #214 erro cru  <-- nao esperado",
    ("apaga", "apaga"): "as duas APAGAM",
    ("recusa", "recusa"): "as duas RECUSAM",
    ("recusa", "apaga"): "b4d0085 recusa / #214 apaga    <-- nao esperado",
    ("recusa", "erro"): "b4d0085 recusa / #214 erro cru  <-- nao esperado",
    ("erro", "recusa"): "b4d0085 erro cru / #214 recusa  (ganho: vira recusa)",
    ("erro", "apaga"): "b4d0085 erro cru / #214 apaga   <-- nao esperado",
    ("erro", "erro"): "as duas estouram cru",
    ("sem_efeitos", "sem_efeitos"): "sem 'efeitos' (as duas recusam antes da guarda)",
}


def _bloco(titulo, alvo, cruz, detalhe):
    print(f"\n-- {titulo}")
    subtotal = sum(v for (a, *_), v in cruz.items() if a == alvo)
    print(f"   linhas neste recorte: {subtotal}")
    for (a, v, n), qtd in sorted(cruz.items(), key=lambda kv: -kv[1]):
        if a == alvo:
            print(f"   {_LEGENDA.get((v, n), f'{v}/{n}'):<52} {qtd}")
    linhas = sorted(
        ((k, q) for k, q in detalhe.items() if k[0] == alvo), key=lambda kv: -kv[1]
    )
    if linhas:
        print("   quebra das recusas do #214 (velho -> motivo -> campo):")
        for (_, v, _n, motivo, campo), qtd in linhas:
            print(f"     {v:<7} {motivo:<19} {campo:<34} {qtd}")


def relatar(cruz, detalhe, total, info_servidor):
    print("== guarda de 'efeitos': b4d0085 (hoje) x 91493d8 (#214) ==")
    print(f"servidor: {info_servidor}")
    print(f"linhas de 'launches' examinadas: {total}")
    _bloco("apagar tudo (escopo_conta_corrente=True) — tipo in "
           "('despesa','receita')", "tudo", cruz, detalhe)
    _bloco("demais portas (escopo_conta_corrente=False) — os outros tipos",
           "singular", cruz, detalhe)


# ---------------------------------------------------------------------------
# Autoteste (sem banco): prova que o script DISCRIMINA, não que ele roda.
# ---------------------------------------------------------------------------
def _autoteste():
    casos = [
        # (efeitos, tipo, velho, novo)
        ({"delta_conta": -50.0}, "despesa", "apaga", "apaga"),
        ({"delta_conta": -50, "bill_id": 93, "paid_amount_added": 50},
         "despesa", "apaga", "apaga"),
        # o risco nomeado no plano: id como string de digitos
        ({"delta_conta": 0, "bill_id": "93", "paid_amount_added": 50},
         "despesa", "apaga", "recusa"),
        # `delta_conta: null` NAO era delete no b4d0085: era `InvalidOperation`
        # cru (balde `errors` / silencio no OF). O #214 troca por recusa limpa.
        ({"delta_conta": None}, "despesa", "erro", "recusa"),
        ({"delta_conta": "NaN"}, "despesa", "apaga", "recusa"),
        ({"delta_conta": True}, "despesa", "erro", "recusa"),
        ({"delta_conta": 0, "delete_pocket": {"nome": "x", "balance": "abc"}},
         "deposito_caixinha", "apaga", "recusa"),
        ({"delta_conta": 0, "delta_pocket": {"nome": 42, "delta": 0}},
         "deposito_caixinha", "apaga", "recusa"),
        ({"delta_conta": 0,
          "delete_investment": {"nome": "x", "last_date": "2026-13-45"}},
         "aporte", "apaga", "recusa"),
        # b4d0085 estoura CRU (nao apaga, nao recusa) e o #214 recusa limpo
        ({"delta_conta": "abc"}, "despesa", "erro", "recusa"),
        # controles que as DUAS recusam
        ({}, "despesa", "recusa", "recusa"),
        ({"delta_conta": 0, "pocket_lot_create": {"lot_id": 1}},
         "despesa", "recusa", "recusa"),
        ({"delta_conta": 0, "create_pocket": {"nome": "x"}},
         "despesa", "recusa", "recusa"),   # fora_do_escopo, nas duas
        # 'efeitos' que nao e dict
        (None, "despesa", "sem_efeitos", "sem_efeitos"),
        ([1, 2], "despesa", "sem_efeitos", "sem_efeitos"),
        ("oi", "despesa", "sem_efeitos", "sem_efeitos"),
    ]
    for ef, tipo, esp_v, esp_n in casos:
        v, n = classificar(ef, tipo)
        assert (v[0], n[0]) == (esp_v, esp_n), (ef, tipo, v, n)
    # o mesmo 'create_pocket' FORA do apagar tudo passa nas duas: prova que o
    # controle positivo existe e que 'fora_do_escopo' nao vira ruido.
    v, n = classificar({"delta_conta": 0, "create_pocket": {"nome": "x"}},
                       "deposito_caixinha")
    assert (v[0], n[0]) == ("apaga", "apaga"), (v, n)
    print(f"autoteste: {len(casos) + 1} casos ok")


# ---------------------------------------------------------------------------
# Produção (somente leitura)
# ---------------------------------------------------------------------------
# `-c default_transaction_read_only=on` faz o SERVIDOR recusar escrita
# (SQLSTATE 25006) — garantia do servidor, não da disciplina do script.
_OPTIONS = ("-c default_transaction_read_only=on "
            "-c statement_timeout=120000 "
            "-c idle_in_transaction_session_timeout=300000")


def main():
    import psycopg

    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        print("DATABASE_URL não definido.", file=sys.stderr)
        return 2
    with psycopg.connect(dsn, options=_OPTIONS, connect_timeout=10,
                         autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_setting('transaction_read_only'), "
                        "pg_is_in_recovery()")
            somente_leitura, replica = cur.fetchone()
        if somente_leitura != "on":
            print(f"ABORTADO: transaction_read_only={somente_leitura!r}, "
                  "esperado 'on'. Nada foi lido.", file=sys.stderr)
            return 2
        info = f"read_only={somente_leitura} pg_is_in_recovery={replica}"
        # cursor server-side: 50k linhas não sobem de uma vez.
        with conn.cursor(name="medir_guarda_214") as cur:
            cur.itersize = 2000
            cur.execute("select tipo, efeitos from launches")
            cruz, detalhe, total = medir(cur)
        conn.rollback()
    relatar(cruz, detalhe, total, info)
    return 0


if __name__ == "__main__":
    if "--autoteste" in sys.argv:
        _autoteste()
    else:
        sys.exit(main())
