"""Ocultação de nomes para o LEITOR de uma conta de condomínio (PR 3d).

Na sessão `ro` (credencial de visualização) de conta condomínio, todo item cuja categoria é de grupo
`pessoal` ou `receitas` (funcionário, condômino) perde os campos que identificam pessoa: eles viram o
nome da categoria. Valor, data, categoria e grupo ficam. Dono, conta pessoal e leitor de conta pessoal:
nada muda (`plano_da_conta` é `None` fora do condomínio).

Helper ÚNICO, aplicado no SERVIDOR, na saída de cada rota GET que o leitor pode chamar (a classificação de
cada uma está em `tests/test_leitura_guard.py::PERMITIDO_LEITOR`). Os grupos são lidos uma vez por
requisição, sem cache global (lista compartilhada vazaria uma conta na outra).
"""
from db.categories import plano_da_conta
from frontend.routes.leitura_guard import e_leitor
from utils_text import normalize_text

GRUPOS_OCULTOS = frozenset({"pessoal", "receitas"})

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


def leitor_da_requisicao(request) -> bool:
    """`request` pode ser o `Request` ou o `bool` que o chamador já tem (o `/ws` guarda `ro`)."""
    if isinstance(request, bool):
        return request
    # a claim `ro` do token (o guard) OU a marca que o resolvedor pôs depois de validar a sessão: fail-closed
    return request is not None and (bool(getattr(getattr(request, "state", None), "somente_leitura", False))
                                    or e_leitor(request))


def categorias_ocultas(uid: int, request) -> frozenset[str]:
    """Nomes (normalizados) das categorias a ocultar; vazio quando o pedido não é de leitor de condomínio."""
    if not leitor_da_requisicao(request):
        return frozenset()
    plano = plano_da_conta(uid)
    return frozenset(normalize_text(n) for n, g in plano or () if g in GRUPOS_OCULTOS)


def e_oculta(ocultas: frozenset[str], categoria) -> bool:
    # a v2 devolve a chave sem acento ("salarios") e a legada o nome ("Salários"): compara pela normalização
    return isinstance(categoria, str) and normalize_text(categoria) in ocultas


def _anda(x, ocultas, campos, herda=None):
    if isinstance(x, (list, tuple)):
        return [_anda(i, ocultas, campos, herda) for i in x]
    if not isinstance(x, dict):
        return x
    novo = {k: _anda(v, ocultas, campos, herda) for k, v in x.items()}
    propria = any(isinstance(novo.get(c), str) for c in CAMPOS_CATEGORIA)
    cat = next((novo[c] for c in CAMPOS_CATEGORIA if e_oculta(ocultas, novo.get(c))), None)
    if cat is None and not propria and e_oculta(ocultas, herda):
        cat = herda
    if cat is not None:
        for k in novo:
            if k in campos and novo[k] is not None and not isinstance(novo[k], (dict, list)):
                novo[k] = cat
    return novo


def mascarar_para_leitor(uid: int, request, dados, campos=CAMPOS_NOME, herda=None):
    """`herda`: categoria de TODA a resposta (rota por categoria), para o item que não traz a sua. Cópia de `dados` (dict/lista aninhados) com os `campos` dos itens de categoria oculta trocados pelo nome
    da categoria. Sem efeito para dono e conta pessoal. `dados` nunca é alterado (pode vir de cache)."""
    ocultas = categorias_ocultas(uid, request)
    return _anda(dados, ocultas, campos, herda) if ocultas else dados


def e_leitor_de_condominio(uid: int, request) -> bool:
    """Leitor de conta de condomínio: o texto livre gerado (insights) não tem categoria para mascarar, então sai."""
    return leitor_da_requisicao(request) and plano_da_conta(uid) is not None


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
