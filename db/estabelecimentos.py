"""Os estabelecimentos de uma categoria (`GET /api/v2/gastos/categoria`): onde mais se gasta.

Parte SEMPRE das linhas que já somam o total (as saídas do `lancamentos.listar`); o `raw` da transação do
Open Finance só é lido à parte, por id de linha, para achar a CHAVE do estabelecimento: `merchant.cnpj`,
senão o nome do recebedor do Pix/TED, senão a descrição normalizada (`utils_text.merchant_key`). O
RÓTULO é a descrição mais frequente do grupo; `businessName`, CNPJ e nome do recebedor nunca saem.
Concentra o `raw` (PII) num módulo só. Depois de rotular, grupos de rótulo com o mesmo `merchant_key`
se juntam e o nome da união é o rótulo mais frequente ENTRE os rótulos desses grupos (empate: o da linha mais
recente): a tela nunca repete um nome.

Isolamento: `open_finance_transactions` não tem `user_id`; o dono vem de conta -> conexão
(`c.user_id`), que também barra a transação forjada de outro usuário apontando para uma linha daqui.

Limite declarado: o ganho medido é pequeno (−10% de grupos no banco, −1% no cartão, numa conta só, plano
§3.1); em outra conta pode não haver `raw` útil e a chave cai na descrição.
"""
import re
from collections import Counter
from decimal import Decimal

from utils_text import merchant_key

SEM_DESCRICAO = "Sem descrição"
TOP = 5

_SQL = """
    select distinct on (t.tabela, t.ref) t.tabela, t.ref,
           t.raw #> '{merchant,cnpj}' as cnpj, t.raw #> '{paymentData,receiver,name}' as recebedor
      from (select 'l' as tabela, imported_launch_id as ref, id, account_id, raw
              from open_finance_transactions where imported_launch_id = any(%(l)s::bigint[])
            union all
            select 'c', imported_credit_tx_id, id, account_id, raw
              from open_finance_transactions where imported_credit_tx_id = any(%(c)s::bigint[])) t
      join open_finance_accounts a on a.id = t.account_id
      join open_finance_connections c on c.id = a.connection_id and c.user_id = %(u)s
     order by t.tabela, t.ref, t.id desc"""


def _chave(descricao, cnpj, recebedor) -> tuple:
    # Mesma regra de documento de `core/services/previsao_recorrencias.py::_doc`, só os 14 dígitos
    # (não importa dele: puxaria cashflow/assinaturas para dentro de db/, e aceita CPF de 11).
    if isinstance(cnpj, str):
        d = re.sub(r"\D", "", cnpj, flags=re.A)
        if len(d) == 14 and d.strip("0"):
            return ("cnpj", d)
    if isinstance(recebedor, str) and (k := merchant_key(recebedor)):
        return ("rec", k)
    # A junção por rótulo (passo 5 de `ler`) já cobre "Uber Trip" x "UBER *TRIP"; a normalização fica por clareza.
    # `or texto.strip()`: descrição sem letra nem dígito ("***", "🍕") não colapsa todas num grupo só.
    texto = descricao or ""
    return ("desc", merchant_key(texto) or texto.strip())


def _rotulo(linhas) -> str:
    return Counter(n for _, n, _ in linhas).most_common(1)[0][0]  # empate: a primeira (a mais recente)


def ler(cur, uid: int, saidas: list[dict]) -> list[dict]:
    """Top 5 `{nome, valor, quantidade}` das `saidas` (linhas do `listar`, com `tabela` e `id`)."""
    ids = {t: [r["id"] for r in saidas if r["tabela"] == t] for t in "lc"}
    achados = {}
    if saidas:
        cur.execute(_SQL, {"l": ids["l"], "c": ids["c"], "u": uid})
        achados = {(r["tabela"], r["ref"]): r for r in cur.fetchall()}
    grupos: dict = {}
    for i, r in enumerate(saidas):  # a ordem do `listar`
        raw = achados.get((r["tabela"], r["id"]), {})
        nome = (r["descricao"] or "").strip() or SEM_DESCRICAO
        grupos.setdefault(_chave(r["descricao"], raw.get("cnpj"), raw.get("recebedor")), []).append(
            (i, nome, r["valor"]))
    juntos: dict = {}  # chave de junção -> (rótulos dos grupos, linhas de todos eles)
    for g in grupos.values():
        rotulo = _rotulo(g)
        rotulos, linhas = juntos.setdefault(merchant_key(rotulo) or rotulo, (set(), []))  # "***" não junta com outro
        rotulos.add(rotulo)
        linhas.extend(g)
    # O nome da união sai só dos RÓTULOS dos grupos que a formaram (todos com a mesma chave de junção): contar
    # as linhas de tudo de novo deixava uma descrição solta de outro grupo ("B") vencer e repetir o nome.
    lista = [{"nome": _rotulo([l for l in sorted(linhas) if l[1] in rotulos]),
              "valor": sum((v for _, _, v in linhas), Decimal(0)), "quantidade": len(linhas)}
             for rotulos, linhas in juntos.values()]
    return sorted(lista, key=lambda e: (-e["valor"], -e["quantidade"], e["nome"]))[:TOP]
