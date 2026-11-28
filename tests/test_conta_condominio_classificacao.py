"""PR 3b da conta de condomínio: a classificação segue o plano da conta. Banco real; LLM/OpenAI mockados
(o que prova o roteamento e a lista permitida, NÃO a interpretação real do modelo)."""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest import mock

import pytest

import ai_router
import db
from conftest import _cleanup_user, promote_to_pro
from core.handle_incoming import handle_incoming
from core.services import category_service
from core.services.ai_chat.tools import categories as tool_categories, listas_ia
from core.types import IncomingMessage
from db.categories import categorias_permitidas
from db.connection import get_conn
from scripts.ativar_conta_condominio import ativar

PROMPT_PESSOAL = (Path(__file__).parent / "fixtures" / "prompt_categoria_pessoal.txt").read_text(encoding="utf-8")


def _novo(condominio: bool) -> int:
    uid = 100_000_000 + int(uuid.uuid4().int % 1_900_000_000)  # < 2e9: acima o handle_incoming remapeia o id
    db.ensure_user(uid)
    promote_to_pro(uid)  # sem plano o gate barra a mensagem antes do route
    if condominio:
        ativar(uid)
    return uid


@pytest.fixture()
def condo():
    uid = _novo(True)
    yield uid
    _cleanup_user(uid)


@pytest.fixture()
def pessoal():
    uid = _novo(False)
    yield uid
    _cleanup_user(uid)


def _diga(uid: int, texto: str) -> str:
    m = IncomingMessage(platform="whatsapp", user_id=uid, text=texto, message_id=uuid.uuid4().hex,
                        attachments=[], external_id=uuid.uuid4().hex, raw={})
    return "\n".join(o.text for o in handle_incoming(m))


def _lancamentos(uid: int) -> list[tuple[str, str]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select tipo, categoria from launches where user_id=%s order by id", (uid,))
        return [(r["tipo"], r["categoria"]) for r in cur.fetchall()]


def _llm(monkeypatch, resposta: str, prompts: list | None = None):
    """Cliente OpenAI falso: devolve `resposta` e guarda o prompt."""
    def create(**kw):
        if prompts is not None:
            prompts.append(kw["messages"][0]["content"])
        return mock.Mock(choices=[mock.Mock(message=mock.Mock(content=resposta))])
    cli = mock.Mock()
    cli.chat.completions.create = create
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(ai_router, "_get_client", lambda: cli)
    monkeypatch.setattr("core.ai_rate_limiter.is_allowed", lambda u: True)


# ── isolamento ───────────────────────────────────────────────────────────────

def test_isolamento_entre_contas(condo, pessoal):
    plano = categorias_permitidas(condo)
    assert "energia elétrica" in plano and "outros" in plano and "moradia" not in plano
    assert categorias_permitidas(pessoal) == ai_router.ALLOWED_CATEGORIES
    assert "energia elétrica" not in categorias_permitidas(pessoal)
    # a tool do chat e a lista da IA também
    assert tool_categories._list_categories(condo, {})["categories"] == plano
    assert "energia elétrica" not in tool_categories._list_categories(pessoal, {})["categories"]
    assert categorias_permitidas(-1) == ai_router.ALLOWED_CATEGORIES  # conta inexistente: pessoal


def test_listas_ia_nome_por_usuario(condo, pessoal, monkeypatch):
    item = {"categoria": "energia eletrica", "categoria_nome": None}
    seen = {}
    monkeypatch.setattr(listas_ia.lancamentos_leitura, "recentes",
                        lambda uid, *a, **k: {"itens": [item], "motivos": []})
    monkeypatch.setattr(listas_ia, "_para_ia", lambda i, nome: seen.setdefault(len(seen), nome) or {})
    monkeypatch.setattr(listas_ia, "mostrar_itens", lambda *a: False)
    listas_ia._lista(condo, {}, False)
    listas_ia._lista(pessoal, {}, False)
    assert "energia eletrica" in seen[0] and "moradia" not in seen[0]
    assert "energia eletrica" not in seen[1] and seen[1]["moradia"] == "moradia"


# ── classificação com LLM mockado ────────────────────────────────────────────

def test_llm_moradia_condominio_vira_outros_e_pessoal_fica(condo, pessoal, monkeypatch):
    _llm(monkeypatch, "moradia")
    assert ai_router.classify_category_with_gpt("xyz", user_id=condo) == "outros"      # negativo: reverter _normalize
    assert ai_router.classify_category_with_gpt("xyz", user_id=pessoal) == "moradia"   # positivo
    _llm(monkeypatch, "Energia Elétrica")
    assert ai_router.classify_category_with_gpt("xyz", user_id=condo) == "energia elétrica"


def test_infer_ia_condominio_so_devolve_plano(condo, monkeypatch):
    monkeypatch.setattr("core.services.wa_ia_primeiro.SEM_LLM_NO_TURNO", mock.Mock(get=lambda: False))
    _llm(monkeypatch, "moradia")
    assert category_service.infer_category(condo, "zzz sem regra nenhuma").category == "outros"
    _llm(monkeypatch, "energia elétrica (funcionais)")  # o LLM devolveu o grupo junto
    assert category_service.infer_category(condo, "zzz sem regra nenhuma").category == "energia elétrica"


def test_prompt_pessoal_byte_a_byte_e_prompt_do_condominio(condo, pessoal, monkeypatch):
    p: list[str] = []
    _llm(monkeypatch, "outros", p)
    ai_router.classify_category_with_gpt("luz", user_id=None)
    ai_router.classify_category_with_gpt("luz", user_id=pessoal)
    assert p[0] == p[1] == PROMPT_PESSOAL
    ai_router.classify_category_with_gpt("luz", user_id=condo)
    assert "condomínio" in p[2] and "energia elétrica (funcionais)" in p[2] and "moradia" not in p[2]


# ── a conversa (handle_incoming, estado real, LLM sem chave) ─────────────────

@pytest.fixture(autouse=True)
def _sem_llm(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


CONVERSA_CONDO = [
    ("paguei 1.200 de folha de pagamento", "despesa", "salários"),
    ("paguei a conta de luz 480", "despesa", "energia elétrica"),   # a regra semeada ("conta de luz") já a cobre
    ("paguei 480 de luz", "despesa", "outros"),                     # sem regra do plano: com LOCAL_RULES seria moradia
    ("paguei salario do porteiro 1500", "despesa", "salários"),     # sem acento
    ("paguei sabesp 300", "despesa", "água"),
]


def test_conversa_condominio(condo):
    for texto, _, _ in CONVERSA_CONDO:
        _diga(condo, texto)
    assert _lancamentos(condo) == [(t, c) for _, t, c in CONVERSA_CONDO]


def test_receita_mensalidade_vai_para_mensalidades_so_no_condominio(condo, pessoal):
    # "recebi ... mensalidade" só vira receita pelo LLM (sem chave aqui o parser não o entende): prova-se a inferência.
    assert category_service.infer_category(condo, "mensalidade apto 101").category == "mensalidades"
    assert category_service.infer_category(pessoal, "mensalidade apto 101").category != "mensalidades"


def test_categoria_do_llm_fora_do_plano(condo, pessoal):
    from core.handlers.launches import resolver_categoria_da_ia
    assert resolver_categoria_da_ia(condo, "moradia", "paguei luz")[0] == "outros"
    assert resolver_categoria_da_ia(condo, "energia elétrica", "paguei luz")[0] == "energia elétrica"  # positivo
    assert resolver_categoria_da_ia(pessoal, "moradia", "paguei algo")[0] == "moradia"                 # pessoal igual


def test_conversa_pessoal_luz_continua_moradia(pessoal):
    _diga(pessoal, "paguei a conta de luz 480")
    _diga(pessoal, "paguei 480 de luz")
    assert _lancamentos(pessoal) == [("despesa", "moradia")] * 2


# ── categoria explícita / recorrente / recategoriza: um resolvedor só contra o plano ──

@pytest.mark.parametrize("hashtag,esperado", [
    ("copa", "despesas de copa"),             # token do nome
    ("energia", "energia elétrica"),          # a hashtag pega UMA palavra
    ("moradia", "outros"),                    # canônica pessoal: fora do plano
    ("energia elétrica", "energia elétrica"), # nome exato (acento)
])
def test_hashtag_condominio_passa_pelo_plano(condo, hashtag, esperado):
    _diga(condo, f"gastei 90 em coisa #{hashtag}")
    assert _lancamentos(condo) == [("despesa", esperado)]


def test_hashtag_pessoal_intacta(pessoal):
    _diga(pessoal, "gastei 90 em coisa #moradia")
    assert _lancamentos(pessoal) == [("despesa", "moradia")]


def test_luz_com_hashtag_energia(condo):
    _diga(condo, "gastei 90 de luz #energia")
    assert _lancamentos(condo) == [("despesa", "energia elétrica")]


def test_resolvedor_ambiguo_exato_e_grupo(condo):
    plano = db.categories.plano_da_conta(condo)
    r = lambda raw: db.categories.categoria_no_plano(plano, raw)
    assert r("material") == "outros"                          # casa várias → ambíguo
    assert r("supermercado") == "outros"                      # nada no plano
    assert r("serviços pf (terceiros)") == "serviços pf"     # sufixo de grupo: sem tirá-lo, o token casa pf e pj → outros
    assert r("serviços pf") == "serviços pf"
    assert r("") == "outros"


def test_nome_exato_da_conta_vence_alias_pessoal(condo):
    # "supermercado" custom na conta: o exato vale, não o apelido pessoal "mercado"
    plano = db.categories.plano_da_conta(condo) + [("supermercado", None)]
    assert db.categories.categoria_no_plano(plano, "supermercado") == "supermercado"
    assert db.categories.categoria_no_plano(plano[:-1], "supermercado") == "outros"


def test_outros_arquivada_continua_no_plano(condo):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update user_categories set is_archived=true where user_id=%s and name='outros'", (condo,))
    assert "outros" in categorias_permitidas(condo)


def test_recorrente_condominio_categoria_fora_do_plano(condo, pessoal):
    from core.handlers import recurring
    from db.recurring import list_recurring_expenses
    ent = {"valor": 480, "dia": 10, "categoria": "moradia", "nome": "Luz"}
    for uid in (condo, pessoal):
        recurring.add(uid, "gasto fixo de 480 todo dia 10", dict(ent))
    assert [r["category"] for r in list_recurring_expenses(condo)] == ["outros"]
    assert [r["category"] for r in list_recurring_expenses(pessoal)] == ["moradia"]
    recurring.add(condo, "gasto fixo", {**ent, "categoria": "energia", "nome": "Luz 2"})
    assert sorted(r["category"] for r in list_recurring_expenses(condo)) == ["energia elétrica", "outros"]


def test_recategorizar_condominio_pelo_plano(condo):
    from core.services.ai_chat.tools import extrato_ia
    _diga(condo, "paguei sabesp 300")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from launches where user_id=%s", (condo,))
        lid = cur.fetchone()["id"]
    with mock.patch.object(extrato_ia, "_reler", return_value=("l", lid, {})), \
            mock.patch.object(extrato_ia, "_resumo_item", return_value={"parcelas": 1}), \
            mock.patch.object(extrato_ia, "_quem", return_value="x"):
        extrato_ia.recategorizar(condo, {"new_category": "moradia"})
    assert _lancamentos(condo) == [("despesa", "outros")]


# ── falha de leitura do plano: propaga, não cai no comportamento pessoal ──

def test_leitura_do_plano_falha_nada_gravado_e_mensagem_padrao(condo, monkeypatch):
    def falha(uid):
        raise RuntimeError("banco caiu")
    def regras():
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("select count(*) n from user_category_rules where user_id=%s", (condo,))
            return cur.fetchone()["n"]
    antes = regras()
    monkeypatch.setattr("db.categories.plano_da_conta", falha)
    resp = _diga(condo, "gastei 90 de luz")  # não pode estourar: o handler devolve a mensagem de falha
    assert resp.strip()
    assert _lancamentos(condo) == [] and regras() == antes


def test_leitura_ok_conta_pessoal_inalterada(pessoal):
    _diga(pessoal, "paguei 480 de luz")
    assert _lancamentos(pessoal) == [("despesa", "moradia")]


def test_infer_propaga_falha_do_plano(condo, monkeypatch):
    def falha(uid):
        raise RuntimeError("banco caiu")
    monkeypatch.setattr("db.categories.plano_da_conta", falha)
    for texto, explicita in (("paguei 480 de luz", None), ("", "moradia")):
        with pytest.raises(RuntimeError):
            category_service.infer_category(condo, texto, explicita)
