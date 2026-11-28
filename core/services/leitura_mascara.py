"""Ocultação de nomes para o LEITOR de uma conta de condomínio (PR 3d).

Na sessão `ro` (credencial de visualização) de conta condomínio, o item só mantém os campos que identificam pessoa
se a categoria dele é de grupo VISÍVEL (`GRUPOS_VISIVEIS_AO_LEITOR`: funcionais, manutencao, tributarias, outras).
Todo o resto (pessoal, receitas, "outros", sem categoria, categoria fora do plano) vira o nome da categoria
("Sem categoria" se não houver): fail-closed. Valor, data, categoria e grupo ficam. Dono, conta pessoal e leitor
de conta pessoal: nada muda (`categorias_visiveis` é `None` fora do condomínio).

Helper ÚNICO, aplicado no SERVIDOR, na saída de cada rota GET que o leitor pode chamar (a classificação de
cada uma está em `tests/test_leitura_guard.py::PERMITIDO_LEITOR`). Os grupos são lidos uma vez por
requisição, sem cache global (lista compartilhada vazaria uma conta na outra).
"""
import re

from db.categorias_condominio import _CONTAS, GRUPOS_VISIVEIS_AO_LEITOR, OCULTAS_AO_LEITOR
from db.connection import get_conn
from frontend.routes.leitura_guard import e_leitor
from utils_text import normalize_text

SEM_CATEGORIA = "Sem categoria"

# Campo que traz a categoria do item, e campos que identificam pessoa (descrição, nota, contraparte,
# documento, chave Pix, comprovante). Nome de campo de todo o repositório que carrega texto livre de lançamento.
CAMPOS_CATEGORIA = ("categoria", "category", "categoria_nome", "category_name")
CAMPOS_NOME = frozenset({
    "descricao", "description", "descricao_original", "memo", "nota", "note", "notes", "mensagem", "message",
    "texto", "text", "raw_text", "observacao", "observacoes", "obs", "titulo", "title", "label", "alvo", "target",
    "contraparte", "counterparty", "counterpart", "payee", "payer", "merchant", "estabelecimento", "favorecido",
    "beneficiario", "pagador", "recebedor", "remetente", "destinatario", "nome", "name", "nome_contraparte",
    "cpf", "cnpj", "cpf_cnpj", "documento", "document", "document_number", "chave_pix", "pix_key", "chave",
    "arquivo", "filename", "file_name", "comprovante", "comprovante_nome", "receipt_name", "display_name",
    "counterparty_name", "payee_name", "merchant_name", "description_raw", "raw_description",
})


def _chave(nome: str) -> str:
    # a v2 devolve a chave sem acento ("salarios"), a legada o nome ("Salários") e o import "nome (grupo)"
    return normalize_text(re.sub(r"\s*\([^)]*\)\s*$", "", nome)) or nome.strip().lower()


def leitor_da_requisicao(request) -> bool:
    """`request` pode ser o `Request` ou o `bool` que o chamador já tem (o `/ws` guarda `ro`)."""
    if isinstance(request, bool):
        return request
    # a claim `ro` do token (o guard) OU a marca que o resolvedor pôs depois de validar a sessão: fail-closed
    return request is not None and (bool(getattr(getattr(request, "state", None), "somente_leitura", False))
                                    or e_leitor(request))


def categorias_visiveis(uid: int, request) -> frozenset[str] | None:
    """Chaves das categorias cujo nome o leitor PODE ver; `None` quando o pedido não é de leitor de condomínio (nada a
    mascarar). TODAS as categorias da conta, inclusive arquivadas (arquivar não desmascara lançamento antigo)."""
    if not leitor_da_requisicao(request):
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select u.tipo_conta, uc.name, uc.grupo from users u left join user_categories uc "
                    "on uc.user_id = u.id where u.id = %s", (uid,))
        rows = cur.fetchall() or []
    if not rows or rows[0]["tipo_conta"] != "condominio":
        return None
    # só o nome ORIGINAL do plano abre (renomear "água" para um nome de pessoa fecha de novo)
    return frozenset(_chave(r["name"]) for r in rows if r["name"] and r["grupo"] in GRUPOS_VISIVEIS_AO_LEITOR
                     and _chave(r["name"]) in _VISIVEIS_DO_PLANO)


# Nomes do plano semeado: só eles servem de rótulo; categoria própria/renomeada pode conter nome de pessoa.
_NOMES_DO_PLANO = frozenset(_chave(n) for n, *_ in _CONTAS) | {_chave("outros")}
_VISIVEIS_DO_PLANO = frozenset(_chave(n) for n, g, *_ in _CONTAS
                               if g in GRUPOS_VISIVEIS_AO_LEITOR and n not in OCULTAS_AO_LEITOR)
OCULTO = "Oculto"


def _rotulo(visiveis, categoria) -> str | None:
    """None = o nome fica; senão o rótulo que o substitui (a própria categoria; sem categoria, `SEM_CATEGORIA`)."""
    if isinstance(categoria, str) and categoria.strip():
        k = _chave(categoria)
        return None if k in visiveis else (categoria if k in _NOMES_DO_PLANO else OCULTO)
    return SEM_CATEGORIA


def _anda(x, visiveis, campos, pai=None):
    """`pai`: rótulo de máscara herdado (item pai oculto, ou categoria oculta da rota inteira). O filho nunca reabre."""
    if isinstance(x, (list, tuple)):
        return [_anda(i, visiveis, campos, pai) for i in x]
    if not isinstance(x, dict):
        return x
    # item = dict com campo de categoria (str ou nulo); dict sem categoria (ex.: cash_declaration) só herda do pai
    rotulo = pai or next((r for c in CAMPOS_CATEGORIA if c in x and (x[c] is None or isinstance(x[c], str))
                          and (r := _rotulo(visiveis, x[c]))), None)
    novo = {k: _anda(v, visiveis, campos, rotulo) for k, v in x.items()}
    if rotulo:
        for k in novo:
            if k in campos and novo[k] is not None and not isinstance(novo[k], dict):
                novo[k] = [rotulo if i is not None and not isinstance(i, (dict, list)) else i for i in novo[k]] \
                    if isinstance(novo[k], list) else rotulo
    return novo


def mascarar_para_leitor(uid: int, request, dados, campos=CAMPOS_NOME, herda=None):
    """`herda`: categoria de TODA a resposta (rota por categoria), para o item que não traz a sua. Cópia de `dados` (dict/lista aninhados) com os `campos` dos itens de categoria oculta trocados pelo nome
    da categoria. Sem efeito para dono e conta pessoal. `dados` nunca é alterado (pode vir de cache)."""
    visiveis = categorias_visiveis(uid, request)
    return dados if visiveis is None else _anda(dados, visiveis, campos, _rotulo(visiveis, herda) if herda else None)


def e_leitor_de_condominio(uid: int, request) -> bool:
    """Leitor de conta de condomínio: o texto livre gerado (insights) não tem categoria para mascarar, então sai."""
    return categorias_visiveis(uid, request) is not None


def sem_busca(uid: int, request, q):
    """Busca por texto livre: com ela o leitor confirmaria "João" mesmo com o nome oculto (item filtrado, contagem
    e paginação não mentem). Leitor de condomínio: o `q` é ignorado."""
    return None if q and e_leitor_de_condominio(uid, request) else q


def neutralizar(uid: int, request, dados, campos, rotulo):
    """Para dado SEM categoria (previsão): `campos` de todo dict vira `rotulo(dict)` (None = deixa) para o leitor de condomínio."""
    if not e_leitor_de_condominio(uid, request):
        return dados

    def anda(x):
        if isinstance(x, (list, tuple)):
            return [anda(i) for i in x]
        if not isinstance(x, dict):
            return x
        novo = {k: anda(v) for k, v in x.items()}
        for k in campos:
            if novo.get(k) is not None and not isinstance(novo[k], (dict, list)) and (r := rotulo(novo)) is not None:
                novo[k] = r
        return novo
    return anda(dados)


def rotulo_previsao(d: dict) -> str | None:
    """Compromisso previsto não traz categoria: para o leitor de condomínio o nome vira o tipo (fatura de cartão fica)."""
    if "status" in d:  # receita detectada no extrato: o nome é o do pagador
        return "Receita do banco"
    return {"gasto_recorrente": "Gasto fixo", "receita_recorrente": "Receita fixa", "recorrencia_banco": "Recorrência do banco",
            "instancia": "Compromisso"}.get(d.get("fonte"))
