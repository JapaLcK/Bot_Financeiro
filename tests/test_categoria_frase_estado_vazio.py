r"""A frase do estado vazio da lista de categoria tem que ser VERDADE.

Quando "Ver lançamentos" abre uma categoria sem nada, o dashboard ensina uma
mensagem pra mandar pro Piggy (`openCategoryLaunches`, frontend/dashboard.js).
Se essa mensagem cair em OUTRA categoria, a lista continua vazia e a tela acaba
de confirmar pro usuário que "não funcionou" — é pior que não ensinar nada.

Duas versões erradas já passaram por aqui:

1. `gastei 30 em <nome>` sempre: texto livre passa pelas LOCAL_RULES do
   `infer_category`, e 5 das 15 categorias do seed caem em "outros".
2. hashtag quando o nome não tem ESPAÇO: espaço não é o critério. O `#` casa
   `[a-zA-ZÀ-ÿ0-9_\-]+` (`_extract_explicit_category`, parsers.py:119), e
   `create_user_category` só remove Cc/Cf (db/categories.py), então `'`, `/`,
   `&`, `%`, `(`, `+` e emoji sobrevivem no nome e CORTAM a hashtag. Medido pelo
   `handle_incoming`: `#mcdonald's` grava a categoria "mcdonald" — a lista segue
   vazia E nasce uma categoria FANTASMA, que ainda vira barra na Distribuição.

O critério passou a ser a CLASSE DE CARACTERES, não o espaço:

  • nome inteiro em `[a-zA-ZÀ-ÿ0-9_-]` → `gastei 30 na loja #<nome>`
  • qualquer outro                     → `gastei 30 em <nome>`

A menção erra em alguns nomes (todos os tokens com menos de 3 letras, ex.
`b+c`), mas — medido — ela NUNCA cria categoria fantasma: quando erra, cai em
"outros", que já existe. Por isso o estado vazio parou de prometer "e o
lançamento aparece aqui" e passou a dizer que dá pra trocar a categoria no
próprio lançamento.

Os testes rodam a CONVERSA pelo `handle_incoming` — mensagem de WhatsApp entra,
linha no banco sai — e conferem pela MESMA função que a tela usa pra listar
(`list_launches_by_category`), não pela inferência isolada.

Controle NEGATIVO do grupo (dois, um por versão errada):
`test_frase_antiga_erra_em_5_categorias_do_seed` roda a frase 1 e exige que ela
erre; `test_criterio_de_espaco_criava_categoria_fantasma` roda a regra 2 e exige
que ela grave a categoria errada. Trocar `_CAT_HASHTAG_OK` de volta por um teste
de espaço deixa `test_frase_nova_nao_cria_categoria_fantasma` vermelho.
Controle POSITIVO: `test_frase_nova_acerta_todo_o_seed` e
`test_frase_nova_acerta_categoria_custom_de_nome_composto`.
"""

from __future__ import annotations

import pathlib
import re

import pytest

import db
from core.types import IncomingMessage
import core.handle_incoming as HI
from db.categories import (
    SYSTEM_CATEGORIES_SEED,
    create_user_category,
    ensure_user_categories_seeded,
)

# Fonte única: o mesmo par de frases que o dashboard escreve. Se mudar lá, muda
# aqui — e este arquivo é quem diz se a nova continua verdadeira.
FRASE_HASHTAG = "gastei 30 na loja #{nome}"
FRASE_MENCAO = "gastei 30 em {nome}"

# Espelho de `_CAT_HASHTAG_OK` (frontend/dashboard.js), que por sua vez espelha a
# classe de `_extract_explicit_category` (parsers.py:119). São as MESMAS três
# cópias que `test_classe_da_hashtag_bate_com_o_parser` compara.
HASHTAG_OK = re.compile(r"^[a-zA-ZÀ-ÿ0-9_-]+$")


def _frase_do_dashboard(nome: str) -> str:
    """Espelho do ternário de `_catExemploFrase` (frontend/dashboard.js)."""
    tpl = FRASE_HASHTAG if HASHTAG_OK.match(nome) else FRASE_MENCAO
    return tpl.format(nome=nome)


def _limpa(uid: int) -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for t in ("launches", "pending_actions"):
                cur.execute(f"delete from {t} where user_id=%s", (uid,))
        conn.commit()


def _diga(uid: int, texto: str) -> str:
    msg = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                          message_id="m", attachments=[], external_id="e", raw={})
    saida = HI.handle_incoming(msg)
    return saida[0].text if saida else ""


def _cai_na_lista(uid: int, nome: str, frase: str) -> bool:
    """Manda a frase pelo handle_incoming e pergunta à lista se ela apareceu."""
    _limpa(uid)
    _diga(uid, frase)
    _, resumo = db.list_launches_by_category(uid, nome)
    return resumo["n_total"] == 1


@pytest.fixture
def uid_wa():
    """Usuário com id CURTO (< 1e9): é o que o caminho do WhatsApp aceita — com
    o id grande da fixture `user_id` o `handle_incoming` responde "registrado" e
    não grava linha nenhuma pra ele. Mesmo molde de
    `test_dois_assuntos_diferentes_em_sequencia_pelo_handle_incoming`
    (tests/test_bill_amount_pending.py)."""
    import uuid

    from tests.conftest import promote_to_pro

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    yield promote_to_pro(uid)


@pytest.fixture
def semeado(uid_wa):
    ensure_user_categories_seeded(uid_wa)
    return uid_wa


def test_frase_nova_acerta_todo_o_seed(semeado):
    erros = [
        nome for nome, *_ in SYSTEM_CATEGORIES_SEED
        if not _cai_na_lista(semeado, nome, _frase_do_dashboard(nome))
    ]
    assert erros == [], erros


def test_frase_antiga_erra_em_5_categorias_do_seed(semeado):
    """Controle negativo: prova que a troca de frase mede alguma coisa."""
    erros = [
        nome for nome, *_ in SYSTEM_CATEGORIES_SEED
        if not _cai_na_lista(semeado, nome, FRASE_MENCAO.format(nome=nome))
    ]
    assert set(erros) == {
        "transporte", "saúde", "educação", "pets", "investimento_aporte",
    }, erros


@pytest.mark.parametrize("nome", ["zumbaria", "gastos da vovó", "saúde da família"])
def test_frase_nova_acerta_categoria_custom_de_nome_composto(uid_wa, nome):
    """A porta real do estado vazio: "Ver lançamentos" de uma categoria que o
    usuário criou na tela — inclusive as de nome composto, onde a hashtag NÃO
    serve (`#gastos da vovó` casa só "gastos")."""
    create_user_category(uid_wa, nome, "🏷️", "#FF2D8E")
    assert _cai_na_lista(uid_wa, nome, _frase_do_dashboard(nome))


def test_hashtag_de_nome_composto_cairia_na_categoria_errada(uid_wa):
    """Por que o ternário existe: `_extract_explicit_category` (parsers.py) casa
    UM token, então a hashtag num nome com espaço leva o gasto pra outro lugar."""
    create_user_category(uid_wa, "gastos da vovó", "🏷️", "#FF2D8E")
    assert not _cai_na_lista(
        uid_wa, "gastos da vovó", FRASE_HASHTAG.format(nome="gastos da vovó"),
    )
    # e o gasto foi parar em "gastos", que é o que a tela NÃO pode prometer
    _, perdido = db.list_launches_by_category(uid_wa, "gastos")
    assert perdido["n_total"] == 1, perdido


# ══ D3a. A frase não pode POLUIR a base do usuário ══════════════════════════
# O critério anterior era o espaço; o real é a classe de caracteres do `#`.
# Nomes que o usuário digita e que a hashtag CORTA (medido pelo handle_incoming,
# um usuário novo por caso — regra aprendida de um caso contamina o seguinte).

_NOMES_QUE_CORTAM_A_HASHTAG = [
    ("mcdonald's", "mcdonald"),
    ("uber/99", "uber"),
    ("l'occitane", "l"),
    ("cafe & cia", "cafe"),
    ("100%natural", "100"),
    ("mercado(bairro)", "mercado"),
    ("rock'n roll", "rock"),
]


def _uid_novo() -> int:
    """Usuário limpo. `_limpa` apaga launches e pendências, mas NÃO as regras
    que o auto-aprendizado grava — sem um uid por caso, o segundo nome herda a
    regra que o primeiro ensinou e o resultado deixa de medir a frase."""
    import uuid

    from tests.conftest import promote_to_pro

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    return promote_to_pro(uid)


def _categorias_gravadas(uid: int) -> list[str]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select categoria from launches where user_id=%s", (uid,))
        return [r["categoria"] for r in cur.fetchall()]


@pytest.mark.parametrize(
    "nome,fantasma",
    [(n, f) for n, f in _NOMES_QUE_CORTAM_A_HASHTAG if " " not in n],
)
def test_criterio_de_espaco_criava_categoria_fantasma(nome, fantasma):
    """Controle NEGATIVO: a regra ANTIGA (hashtag se não tem espaço) aplicada a
    estes nomes grava uma categoria que o usuário nunca criou — e ela vira barra
    na Distribuição. Dois danos, não um: a lista segue vazia E a base suja."""
    uid = _uid_novo()
    create_user_category(uid, nome, "🏷️", "#FF2D8E")
    _diga(uid, FRASE_HASHTAG.format(nome=nome))   # a regra ANTIGA, sem espaço
    assert _categorias_gravadas(uid) == [fantasma], _categorias_gravadas(uid)
    _, resumo = db.list_launches_by_category(uid, nome)
    assert resumo["n_total"] == 0, resumo


@pytest.mark.parametrize("nome,fantasma", _NOMES_QUE_CORTAM_A_HASHTAG)
def test_frase_nova_nao_cria_categoria_fantasma(nome, fantasma):
    """O conserto: a frase que a tela ensina agora cai NA categoria, e em
    nenhuma hipótese numa categoria nova."""
    uid = _uid_novo()
    create_user_category(uid, nome, "🏷️", "#FF2D8E")
    _diga(uid, _frase_do_dashboard(nome))
    assert _categorias_gravadas(uid) == [nome], _categorias_gravadas(uid)
    _, resumo = db.list_launches_by_category(uid, nome)
    assert resumo["n_total"] == 1, resumo


@pytest.mark.parametrize("nome", ["🍕 pizza", "🍕pizza", "day-trade", "casa",
                                  "casaco", "x_y", "n1", "café", "a" * 40,
                                  "compras do mês", "pai&mãe", "eu & ela"])
def test_frase_nova_acerta_a_bateria_de_nomes_reais(nome):
    """Controle POSITIVO largo: emoji colado e separado, hífen, underscore,
    acento, nome curto, nome de 40 caracteres, `&` colado e separado."""
    uid = _uid_novo()
    create_user_category(uid, nome, "🏷️", "#FF2D8E")
    _diga(uid, _frase_do_dashboard(nome))
    assert _categorias_gravadas(uid) == [nome], (nome, _categorias_gravadas(uid))


def test_nome_sem_token_de_3_letras_erra_mas_nao_polui():
    """O limite CONHECIDO: quando todo token do nome tem menos de 3 letras
    (`_CUSTOM_CATEGORY_MIN_TOKEN_LEN`, core/services/category_service.py) e o
    nome sai da classe da hashtag, nenhuma das duas frases casa. O que importa é
    que o estrago pára aí: cai em "outros", que já existe, e NÃO nasce categoria
    fantasma. É por isso que o estado vazio não promete mais "aparece aqui"."""
    uid = _uid_novo()
    create_user_category(uid, "b+c", "🏷️", "#FF2D8E")
    _diga(uid, _frase_do_dashboard("b+c"))
    gravadas = _categorias_gravadas(uid)
    assert gravadas == ["outros"], gravadas
    _, resumo = db.list_launches_by_category(uid, "b+c")
    assert resumo["n_total"] == 0, resumo


def test_classe_da_hashtag_bate_com_o_parser():
    """§0.7: a classe está escrita em TRÊS lugares (parsers.py, dashboard.js e o
    espelho deste arquivo). Um teste compara as três — mudar só uma quebra a
    frase que a tela ensina, e nada mais avisaria."""
    raiz = pathlib.Path(__file__).resolve().parent.parent
    parser_src = (raiz / "parsers.py").read_text(encoding="utf-8")
    js_src = (raiz / "frontend/dashboard.js").read_text(encoding="utf-8")

    assert r"#([a-zA-ZÀ-ÿ0-9_\-]+)" in parser_src, "a classe do parser mudou"
    m = re.search(r"const _CAT_HASHTAG_OK = /\^\[(.+?)\]\+\$/;", js_src)
    assert m, "_CAT_HASHTAG_OK sumiu ou mudou de forma em frontend/dashboard.js"
    # `-` no fim (JS) × `\-` no meio (Python) são a MESMA classe; comparamos o
    # conjunto de átomos, não o texto.
    assert set(m.group(1).replace("-", "")) == set("a-zA-ZÀ-ÿ0-9_".replace("-", "")), m.group(1)
    assert HASHTAG_OK.pattern == "^[a-zA-ZÀ-ÿ0-9_-]+$"
