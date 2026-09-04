"""Mede, contra produção, quantas linhas de `launches` o #214 passa a RECUSAR
que o `b4d0085` (comportamento de hoje) apagava.

Roda a guarda de VERDADE, em Python (`_validar_efeitos`, `db/accounts.py`), na
tabela `launches` INTEIRA — a query não tem `WHERE` nem `LIMIT`, então não é
amostra: é todo `efeitos` que existe. NÃO reimplementa a regra em SQL: o `falsy`
do SQL não é o do Python (`[]`, `{}`, `0.00` são recusados aqui e invisíveis lá)
e duas fontes de verdade da mesma regra é o que `CLAUDE.md` §0.7 proíbe.

SEGURANÇA
  - conexão read-only pelo SERVIDOR (`default_transaction_read_only=on`), não
    pela disciplina do script; sai com código 2 se o servidor não confirmar;
  - um SELECT só (`tipo`, `efeitos`), cursor server-side, `rollback` e `close`
    no fim (o `with psycopg.connect` mandava COMMIT no `__exit__`; era inócuo
    numa transação vazia e read-only, mas não era o que este texto prometia);
  - a saída é SÓ contagem agregada. Nenhum `user_id`, `launch_id`, nome,
    valor ou pedaço de payload sai daqui — nem em amostra, nem em exemplo.
  - a saída é fechada nos DESCRITORES 1 e 2, não num `try` só. `executar()`
    imprime TIPO e LOCAL de uma falha e NUNCA `str(exc)`; mas o próprio
    tratador pode falhar (2º Ctrl-C durante o aviso, stderr em EIO/disco
    cheio), e aí o traceback PADRÃO do interpretador imprimia a cadeia inteira.
    Por isso o tratador roda dentro de `_calado`, e `sys.excepthook`,
    `sys.unraisablehook` e o `logging.lastResort` ficam mudos. Quem mais
    escreve nesses dois descritores, e o que fecha cada um, está enumerado no
    bloco da fronteira (`grep -n "descritores 1 e 2"`). Não é zelo teórico: a
    mensagem de `chave_desconhecida` é
    `f"efeitos que não sei reverter: {sorted(desconhecidas)}"` — chave CRUA de
    payload — e ela vazava em 2 de 15 execuções por Ctrl-C dentro do `except`
    do `classificar`, e em 100% das execuções por Ctrl-C duplo ou stderr
    quebrado. Ctrl-C, import quebrado e `RecursionError` fecham no mesmo ponto.
  - é a única query deste repositório sem `WHERE user_id` de propósito
    (`CLAUDE.md` §0): a pergunta é agregada sobre a base inteira, e o que sai
    do processo é contagem, não linha.

O QUE ESTA MEDIÇÃO **NÃO** RESPONDE
  1. "b4d0085 apaga" aqui = "passou no PRÉ-VOO do b4d0085". Parte dessas linhas
     estourava conversão CRUA mais adiante na reversão (balde `errors`, e
     SILÊNCIO nos três `except Exception: pass` de `db/open_finance.py`) em vez
     de apagar. A coluna `b4d0085 apaga / #214 recusa` é portanto um TETO do
     falso positivo, não o número exato.
  2. O #214 tem TRÊS recusas FORA de `_validar_efeitos`, todas por
     `rowcount == 0` no corpo da reversão
     (`grep -n "rowcount == 0" db/accounts.py`, conferidas uma a uma): o update
     de `credit_bills` (`bill_id` bem formado que casa fatura de OUTRO
     usuário), o delete de `investment_lots` (`lot_id` que não casa lote do
     usuário) e o update do resgate (`investment_lot_withdrawals.lot_id`).
     Nenhuma é medida aqui — exigiria ler as outras tabelas. Por essas três
     vias o falso positivo real pode ser MAIOR que o daqui: as células
     `apaga/apaga` são um PISO, não o número exato.
  3. Nada do corpo da reversão é exercitado (saldos, faturas, lotes,
     `InvestmentLotHasWithdrawal`). Isto mede o PRÉ-VOO, não o delete.
  4. Não diz QUAIS linhas nem de quem — por requisito do dono. Nem QUAL
     chave desconhecida: a chave crua é texto de payload (um escritor futuro
     pode gravar `cliente_<nome>_<cpf>`), então ela sai `?` e o que o dono lê é
     a CONTAGEM da linha `chave_desconhecida`, que já responde "existe chave
     nova em produção, em N linhas" — o suficiente para decidir; qual é ele
     descobre no código do escritor, não no terminal.
  5. É um retrato do instante da leitura.

USO
    DATABASE_URL='<dsn de produção>' \
    PYTHONPATH=. python scripts/medir_guarda_efeitos_214.py

    python scripts/medir_guarda_efeitos_214.py --autoteste   # sem banco
    python scripts/medir_guarda_efeitos_214.py --help        # sem banco

CÓDIGOS DE SAÍDA
    0  medição feita.
    2  flag inválida — do argparse, e SÓ dele.
    3  a medição falhou; o relato (tipo + arquivo:linha) saiu em stderr.
    4  recusa ANTES de ler: sem `DATABASE_URL`, ou o servidor não confirmou
       `transaction_read_only=on`. Era 2 também, e quem automatizava
       `if rc == 2` não distinguia typo de flag de recusa do servidor.
    5  falhou DENTRO do tratador: nada pôde ser impresso com segurança, então
       o código de saída é o único aviso que sobra.

QUANDO ALGO FALHA
    A saída é TIPO + arquivo:linha + a linha de CÓDIGO de cada quadro, sem
    mensagem nenhuma (`executar`/`_relatar_falha`). Dá para localizar a falha,
    e nada do banco aparece. Se o que falhou foi o `--autoteste`, o CASO exato
    sai em `pytest tests/test_medir_guarda_efeitos.py`: a suíte chama
    `_autoteste()` direto, sem a fronteira, e ali o `assert` só tem literal
    deste arquivo.

ponytail: artefato de medição descartável. Apagar quando o #214 for decidido.
"""
import json
import logging
import os
import re
import sys
from collections import Counter
from decimal import Decimal
from functools import lru_cache

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

# Só a FORMA do caminho ('bill_id', 'delete_pocket.nome'), e só de um conjunto
# FECHADO deles. Regex de identificador não servia: a mensagem de
# `chave_desconhecida` é `f"...: {sorted(desconhecidas)}"`, e chave desconhecida
# é TEXTO DE PAYLOAD — `cliente_joao_12345678900` e `nome.do.cliente` casam
# `^[A-Za-z_][A-Za-z0-9_.]*$` inteiro e iam parar no terminal do dono, contra a
# garantia deste script ("nenhum pedaço de payload sai daqui"). Formato não
# distingue esquema de chave que um escritor futuro inventou; só a lista
# distingue. Ela é DERIVADA de `_EFEITOS_FORMA`/`_BEFORE_FORMA` (`CLAUDE.md`
# §0.7): chave nova no esquema entra sozinha, chave nova no payload não entra
# nunca. Fora dela sai '?'.
@lru_cache(maxsize=1)
def _caminhos_do_esquema():
    """{'bill_id', 'delete_pocket.nome', 'investment_lot_withdrawals.before',
    'investment_lot_withdrawals.before.balance', ...} — os caminhos que as
    mensagens de `_validar_efeitos`/`_checar_forma` podem nomear."""
    from db.accounts import _EFEITOS_FORMA

    def campos(tabela, prefixo):
        # `{campo: (predicado, obrigatorio)}`; predicado `dict` é sub-objeto.
        for campo, (predicado, _obrigatorio) in tabela.items():
            yield f"{prefixo}.{campo}"
            if isinstance(predicado, dict):
                yield from campos(predicado, f"{prefixo}.{campo}")

    caminhos = set()
    for chave, (container, forma) in _EFEITOS_FORMA.items():
        caminhos.add(chave)
        if container != "valor":  # em "valor", `forma` é o predicado, não tabela
            caminhos.update(campos(forma, chave))
    return frozenset(caminhos)


def _campo_da_msg(msg):
    m = re.search(r"'([^']*)'", str(msg))
    return m.group(1) if m and m.group(1) in _caminhos_do_esquema() else "?"


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


def _head():
    """Rotulo da guarda NOVA: ela e importada do checkout, entao o HEAD do
    checkout E a versao medida. String a mao ja saiu errada uma vez (o relatorio
    dizia `91493d8` com o codigo de outro commit rodando)."""
    import subprocess
    try:
        return subprocess.run(
            ["git", "-C", os.path.dirname(os.path.abspath(__file__)),
             "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True).stdout.strip() or "?"
    except Exception:  # noqa: BLE001 -- sem git, sem rotulo; a medicao segue
        return "?"


def relatar(cruz, detalhe, total, info_servidor):
    print(f"== guarda de 'efeitos': b4d0085 (congelado aqui) x {_head()} "
          "(HEAD do checkout, #214) ==")
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
        # jsonb entregue como STRING: o único caso que mata o `json.loads` do
        # `_normalizar`. Sem ele a linha vira `sem_efeitos` e o relatório conta
        # zero onde havia lançamento.
        ('{"delta_conta": -50}', "despesa", "apaga", "apaga"),
        ({"delta_conta": -50, "bill_id": 93, "paid_amount_added": 50},
         "despesa", "apaga", "apaga"),
        # CONTROLE POSITIVO do afrouxamento de `_id`: id como string de
        # digitos. No `91493d8` isto era ("apaga","recusa") -- falso positivo,
        # e `kept_unsafe` e permanente. O `13fb792` passou a aceitar porque o
        # Postgres coage `"93"` no `where id=%s` (medido).
        ({"delta_conta": 0, "bill_id": "93", "paid_amount_added": 50},
         "despesa", "apaga", "apaga"),
        # e a cauda que continua recusada: nao-ASCII e id acima de bigint
        # estouravam CRU no Postgres antes do 13fb792+1.
        ({"delta_conta": 0, "bill_id": "\xa093", "paid_amount_added": 50},
         "despesa", "apaga", "recusa"),
        ({"delta_conta": 0, "bill_id": "9" * 30, "paid_amount_added": 50},
         "despesa", "apaga", "recusa"),
        # `_data`: os 10 primeiros caracteres eram ISO e o resto ia cru.
        ({"delta_conta": 0,
          "delete_investment": {"nome": "x", "maturity_date": "2026-09-02extra"}},
         "aporte", "apaga", "recusa"),
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

    # O QUE SAI NA COLUNA `campo` (allowlist de caminhos, nao regex de formato).
    # Os dois primeiros sao o CONTROLE NEGATIVO: com a regex `_CAMPO_OK` de
    # volta no lugar de `_caminhos_do_esquema`, os dois passam a imprimir a
    # chave do payload e este bloco fica VERMELHO.
    casos_campo = [
        # (efeitos, tipo, motivo, campo)
        ({"delta_conta": 0, "cliente_joao_12345678900": 1},
         "despesa", "chave_desconhecida", "?"),
        ({"delta_conta": 0, "nome.do.cliente": 1},
         "despesa", "chave_desconhecida", "?"),
        # positivos: caminho de esquema continua saindo com NOME, senao a
        # quebra por motivo perde o valor de diagnostico.
        ({"delta_conta": 0, "bill_id": 93}, "despesa", "efeito_incompleto",
         "bill_id"),
        ({"delta_conta": 0, "delete_pocket": {"nome": ""}},
         "aporte", "efeito_incompleto", "delete_pocket.nome"),
        ({"delta_conta": 0, "investment_lot_create": {"lot_id": "abc"}},
         "aporte", "efeito_incompleto", "investment_lot_create.lot_id"),
        ({"delta_conta": 0,
          "investment_lot_withdrawals": [{"lot_id": 1, "before": "x"}]},
         "aporte", "efeito_incompleto", "investment_lot_withdrawals.before"),
        ({"delta_conta": 0, "investment_lot_withdrawals": [
            {"lot_id": 1, "before": {"balance": "abc",
                                     "principal_remaining": 1}}]},
         "aporte", "efeito_incompleto",
         "investment_lot_withdrawals.before.balance"),
        ({"delta_conta": -300, "delta_pocket": {"nome": "x", "delta": -300}},
         "deposito_caixinha", "lote_ausente", "delta_pocket"),
        # motivos que nao nomeiam campo de esquema: o texto entre aspas e
        # 'apagar tudo' e 'efeitos', que nao sao caminhos -> '?'. O `motivo` ja
        # diz tudo o que o dono precisa nos dois.
        ({"delta_conta": 0, "create_pocket": {"nome": "x"}},
         "despesa", "fora_do_escopo", "?"),
        ({}, "despesa", "sem_delta_conta", "?"),
    ]
    for ef, tipo, motivo, campo in casos_campo:
        _v, n = classificar(ef, tipo)
        assert n == ("recusa", motivo, campo), (ef, tipo, n)
    print(f"autoteste: {len(casos) + len(casos_campo) + 1} casos ok")


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
        return 4  # 2 é do argparse (flag inválida); ver CÓDIGOS DE SAÍDA
    # O que a medição importa, ANTES de conectar. Rodando de outro diretório sem
    # `PYTHONPATH=.` o `ModuleNotFoundError` só aparecia depois de conectar,
    # passar no read-only e buscar o primeiro FETCH. E `_caminhos_do_esquema()`
    # é aquecido aqui pelo mesmo motivo: dentro do `except` do `classificar` a
    # falha dele nascia com um `efeitos` no `__context__`.
    from db.accounts import LaunchUnsafeRollback, _validar_efeitos  # noqa: F401
    _caminhos_do_esquema()

    conn = psycopg.connect(dsn, options=_OPTIONS, connect_timeout=10,
                           autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("select current_setting('transaction_read_only'), "
                        "pg_is_in_recovery()")
            somente_leitura, replica = cur.fetchone()
        if somente_leitura != "on":
            print(f"ABORTADO: transaction_read_only={somente_leitura!r}, "
                  "esperado 'on'. Nada foi lido.", file=sys.stderr)
            return 4  # recusa do servidor, não flag inválida
        info = f"read_only={somente_leitura} pg_is_in_recovery={replica}"
        # cursor server-side: 50k linhas não sobem de uma vez.
        with conn.cursor(name="medir_guarda_214") as cur:
            cur.itersize = 2000
            cur.execute("select tipo, efeitos from launches")
            cruz, detalhe, total = medir(cur)
        conn.rollback()
    finally:
        # `close()` no lugar do `__exit__` do `with`, que mandava COMMIT.
        conn.close()
    relatar(cruz, detalhe, total, info)
    return 0


# ---------------------------------------------------------------------------
# Fronteira de saída — o único caminho por onde este processo termina
# ---------------------------------------------------------------------------
# A fronteira NÃO é o `except` de `executar`: é o par de descritores 1 e 2.
# Um `except` na borda, por definição, não pega o que nasce DENTRO dele. Quem
# escreve nos dois descritores, e o que fecha cada um:
#   1. os `print` deste arquivo — nenhum passa `str(exc)`, e o relato de falha
#      é tipo + arquivo:linha + linha de código (`_relatar_falha`);
#   2. o próprio tratador, quando FALHA (2º Ctrl-C durante o aviso, stderr em
#      EIO): a exceção nova escapa com a antiga no `__context__` e o traceback
#      padrão imprime a cadeia toda, com a mensagem — `_calado`;
#   3. `sys.excepthook`, para qualquer escape do main thread que os dois de
#      cima não previram, inclusive de dentro do `_calado` — `_encerrar_calado`;
#   4. `sys.unraisablehook`: exceção em `__del__`/finalizador na saída do
#      interpretador; imprime `str(exc)` e NÃO passa por `except` nenhum;
#   5. `logging`: root sem handler cai no `logging.lastResort`, que escreve a
#      mensagem em stderr. Hoje nada no caminho da medição loga — `logging`
#      cobre o dia em que `db/accounts.py` ganhar um `logger.exception`;
#   6. `threading.excepthook` — este script não cria thread nenhuma. Se um dia
#      criar, este item vira código;
#   7. sinal fatal (SIGSEGV) e `faulthandler` escrevem em C, sem `str(exc)`.
_RC_TRATADOR = 5


def _encerrar_calado(*_args, _saida=os._exit):
    """`sys.excepthook` que não imprime NADA — nem o seu próprio erro.

    Corpo de uma chamada só, a um syscall que não levanta exceção, com o
    `os._exit` preso em `default` (nem busca de global sobra para falhar). É de
    propósito: um `excepthook` que estoura faz o interpretador imprimir
    `Error in sys.excepthook:` e, em seguida, `Original exception was:` com a
    cadeia inteira — exatamente o vazamento que este hook existe para evitar.

    `os._exit` pula o flush do stdout: perde-se o que estivesse em buffer. É o
    preço certo — se chegamos aqui, o relatório já não é confiável, e o flush é
    justamente a operação que estava falhando no caso de stderr em EIO.
    """
    _saida(_RC_TRATADOR)


def _calado(aviso, codigo):
    """Roda `aviso()`; se ELE falhar, o processo cala a boca e muda o código.

    `BaseException` de propósito: o gatilho medido é um SEGUNDO Ctrl-C chegando
    durante o aviso do primeiro. Não vira silêncio total — o dono continua
    sabendo que algo falhou pelo código de saída (`_RC_TRATADOR`), que é
    diferente do código do caminho que conseguiu relatar.
    """
    try:
        aviso()
        return codigo
    except BaseException:  # noqa: BLE001 — ver docstring
        return _RC_TRATADOR


def _relatar_falha(exc):
    """Imprime TIPO e LOCAL de cada exceção da cadeia, NUNCA a mensagem.

    A mensagem é o canal: `chave_desconhecida` carrega `sorted(desconhecidas)`,
    que é payload cru. Traceback encadeado traz a mensagem de volta mesmo
    quando a exceção que escapou é outra, então o corte é na mensagem, não no
    `except`. Fica o que o dono precisa para diagnosticar (que erro, em que
    arquivo e linha) e sai o que ele nunca pode ver.

    Texto e formato em ASCII de propósito: este relatório não pode morrer no
    próprio `print` (é o mesmo motivo do `reconfigure` em `executar`).
    """
    import traceback

    print("FALHA na medicao. As mensagens sao omitidas de proposito: elas "
          "podem conter payload. Tipo e local:", file=sys.stderr)
    vistas = set()
    while exc is not None and id(exc) not in vistas:
        vistas.add(id(exc))
        print(f"  {type(exc).__module__}.{type(exc).__qualname__}",
              file=sys.stderr)
        for quadro in traceback.extract_tb(exc.__traceback__):
            print(f"    {os.path.basename(quadro.filename)}:{quadro.lineno}"
                  f" em {quadro.name}", file=sys.stderr)
            # a LINHA DE CÓDIGO (do arquivo, não do banco) fica: é o que torna
            # a falha diagnosticável sem a mensagem. Texto de arquivo `.py`
            # nunca contém payload; `str(exc)` contém.
            if quadro.line:
                print(f"      {quadro.line}", file=sys.stderr)
        exc = exc.__cause__ or exc.__context__


def executar(fn):
    """Roda `fn` e devolve o código de saída.

    Nada sai daqui com `str(exc)`: nem a falha (`_relatar_falha`), nem a falha
    DO RELATO da falha (`_calado`), nem o que escapar por um caminho que a
    enumeração acima não previu (`_encerrar_calado`). Efeito colateral de
    propósito: os três hooks são globais do processo e ficam instalados — este
    script existe para ser um processo só, e quem chamar `executar` de dentro
    de outro (a suíte) restaura o que precisar.
    """
    sys.excepthook = _encerrar_calado
    sys.unraisablehook = lambda _args: None
    logging.disable(logging.CRITICAL)
    try:
        for stream in (sys.stdout, sys.stderr):
            # stdout ASCII não pode custar o run inteiro depois do scan: o `—`
            # do relatório sai `\u2014` em vez de estourar `UnicodeEncodeError`.
            # `getattr`: sob pytest, console de IDE ou qualquer captura o stream
            # não é `TextIOWrapper` e não tem `reconfigure`. Chamar direto era
            # `AttributeError` na PRIMEIRA linha da fronteira — a medição nunca
            # começava e o dono lia "FALHA na medicao" (medido: rc=3, relatório
            # nenhum, e um re-teste inteiro saiu limpo porque nada rodou).
            getattr(stream, "reconfigure", lambda **_: None)(
                errors="backslashreplace")
        return fn() or 0
    except KeyboardInterrupt:
        # sem traceback: o `__context__` de um Ctrl-C no meio do laço é a
        # recusa que estava sendo tratada, com o payload na mensagem.
        return _calado(lambda: print(
            "interrompido (Ctrl-C). Nada foi escrito no banco.",
            file=sys.stderr), 130)
    except Exception as exc:  # noqa: BLE001 — é a fronteira; ver `_relatar_falha`
        return _calado(lambda: _relatar_falha(exc), 3)


if __name__ == "__main__":
    import argparse

    # `"--autoteste" in sys.argv` era casamento exato: `--help`, `-h`,
    # `--dry-run`, `--autoteste=1` e `--auto-teste` caíam TODOS no ramo de
    # produção e escaneavam a base. O argparse recusa o que não conhece (exit 2)
    # e responde `--help` sem conectar.
    _p = argparse.ArgumentParser(
        description="Mede a guarda de 'efeitos' (b4d0085 x HEAD) contra o "
                    "banco de DATABASE_URL. Somente leitura, saida agregada.")
    _p.add_argument("--autoteste", action="store_true",
                    help="roda os casos embutidos, sem banco")
    sys.exit(executar(_autoteste if _p.parse_args().autoteste else main))
