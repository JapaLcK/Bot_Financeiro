"""A categoria que o usuário criou na tela vale quando o gasto chega pelo WhatsApp?

O `test_custom_category_infer.py` tem 73 testes desta área e **nenhum** passa
pela conversa — todos chamam `infer_category` / `custom_category_match` direto.
É o ponto cego do CLAUDE.md §3 ("rode a conversa, não a função"): o caminho real
percorre classify → route → handler → infer_category, e cada camada pode
atravessar a decisão da seguinte.

Foi por aí que o defeito apareceu na auditoria de 2026-08-26: com a categoria
`café da manhã` criada, `gastei ... cafe da manha` pelo WhatsApp caía em
alimentação. Este arquivo mede a conversa; o que ele reprovar, reprova no
caminho que o usuário usa.
"""
from __future__ import annotations

import uuid

import pytest

import db
from db.categories import create_user_category
from db.connection import get_conn
from core.intent_classifier import classify
from core.intent_router import route
from core.types import IncomingMessage
from utils_text import normalize_text


def _uid() -> int:
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    return uid


def _diga(uid: int, texto: str) -> str | None:
    m = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                        message_id="x", attachments=[], external_id="e", raw={})
    return route(classify(texto, user_id=uid), m)


def _categoria_do_ultimo_lancamento(uid: int) -> str | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select categoria from launches where user_id = %s order by id desc limit 1",
            (uid,),
        )
        row = cur.fetchone()
    return None if row is None else row["categoria"]


@pytest.mark.parametrize("nome_criado,frase", [
    ("cafe", "gastei 37,50 em cafe"),
    ("café", "gastei 37,50 em cafe"),          # criou com acento, digitou sem
    ("cafe", "gastei 37,50 em café"),          # criou sem acento, digitou com
])
def test_categoria_criada_na_tela_vence_a_regra_generica(nome_criado, frase):
    """O caso que falhou na auditoria: `cafe` existe no catálogo e o gasto some.

    Sem a categoria criada, `cafe` cai em alimentação pelas LOCAL_RULES — e está
    certo. Com ela criada, a personalização explícita do usuário tem de ganhar:
    é a razão de `custom_category_match` vir antes das LOCAL_RULES em
    `category_service.py:214`.
    """
    uid = _uid()
    create_user_category(uid, nome_criado)

    resposta = _diga(uid, frase)
    assert resposta, "o bot não respondeu ao lançamento"

    categoria = _categoria_do_ultimo_lancamento(uid)
    assert categoria is not None, f"nenhum lançamento foi gravado; resposta: {resposta!r}"
    assert "cafe" in (categoria or "").lower() or "café" in (categoria or "").lower(), (
        f"o gasto foi para {categoria!r} em vez da categoria criada {nome_criado!r}"
    )


def test_controle_sem_categoria_criada_cai_na_regra_generica():
    """Controle positivo do grupo: sem categoria criada, o comportamento antigo
    continua valendo. Sem isto, um conserto que mandasse TUDO para uma categoria
    nova passaria no teste de cima e quebraria o produto."""
    uid = _uid()
    _diga(uid, "gastei 37,50 em cafe")
    categoria = _categoria_do_ultimo_lancamento(uid)
    assert categoria is not None, "nenhum lançamento foi gravado"
    assert "cafe" not in categoria.lower(), (
        f"sem categoria criada, {categoria!r} deveria ser a genérica das LOCAL_RULES"
    )


def test_categoria_criada_DEPOIS_de_o_bot_ja_ter_aprendido(caplog):
    """A ordem real dos acontecimentos — e a que reproduz o defeito relatado.

    Os testes acima criam a categoria e mandam a mensagem em seguida. Ninguém
    usa o produto assim: o usuário gasta com "cafe" por meses (o bot aprende
    `cafe -> alimentação` pelas LOCAL_RULES e GRAVA como regra dele), e só
    depois cria a categoria na tela. A partir daí a regra aprendida vence no
    passo B de `infer_category`, antes de o `custom_category_match` (B2) ser
    consultado — e a categoria nova nunca é usada.

    É o estado que OUTRO fluxo deixou no banco: a classe de bug que o CLAUDE.md
    §3 diz que teste de função isolada nunca vê.
    """
    uid = _uid()

    _diga(uid, "gastei 50 em cafe")               # 1) o bot aprende
    create_user_category(uid, "cafe")              # 2) o usuário cria a categoria
    _diga(uid, "gastei 37,50 em cafe")             # 3) o gasto seguinte

    categoria = _categoria_do_ultimo_lancamento(uid)
    assert categoria is not None, "nenhum lançamento foi gravado"
    assert "cafe" in categoria.lower() or "café" in categoria.lower(), (
        f"o gasto foi para {categoria!r}: a regra aprendida antes venceu a "
        f"categoria criada depois"
    )


# ── As regressões da correção em dois pontos ────────────────────────────────
# O conserto NÃO inverte a precedência global entre a regra aprendida (passo B)
# e a categoria custom (B2). A regra continua ganhando quando é válida; ela só
# perde quando ficou OBSOLETA — o usuário criou depois uma categoria que, pelo
# mesmo `custom_category_match` que o B2 usa, passou a ser dona do keyword.

from db.categories import (                                       # noqa: E402
    delete_user_category,
    list_user_category_rules,
    update_user_category,
    upsert_category_rule,
)
from core.services.category_service import infer_category, learn_from_inference  # noqa: E402


def _regras(uid: int) -> dict[str, str]:
    return {k: v for k, v in (list_user_category_rules(uid) or [])}


@pytest.mark.parametrize("grafia_categoria,grafia_gasto", [
    ("Cafe", "cafe"), ("café", "cafe"), ("CAFÉ", "café"), ("cafe", "CAFE"),
])
def test_caixa_e_acento_usam_a_mesma_normalizacao(grafia_categoria, grafia_gasto):
    """Caixa e acento não podem produzir vereditos diferentes: o critério é o
    `normalize_text` que o resto do sistema já usa."""
    uid = _uid()
    _diga(uid, "gastei 20 com cafe")
    create_user_category(uid, grafia_categoria)

    resultado = infer_category(uid, f"gastei 15 com {grafia_gasto}", allow_ai=False)
    assert "cafe" in normalize_text(resultado.category), (
        f"categoria {grafia_categoria!r} + gasto {grafia_gasto!r} → {resultado.category!r}"
    )


def test_regra_aprendida_sem_conflito_continua_valendo():
    """Controle positivo do grupo — o que NÃO pode mudar.

    Sem ele, um conserto que simplesmente ignorasse todas as regras aprendidas
    passaria em todos os testes acima e quebraria o aprendizado do bot inteiro.
    """
    uid = _uid()
    upsert_category_rule(uid, "padoca do ze", "alimentacao")
    create_user_category(uid, "cafe")          # categoria que NÃO casa com a regra

    resultado = infer_category(uid, "gastei 12 na padoca do ze", allow_ai=False)
    assert normalize_text(resultado.category) == "alimentacao", (
        f"a regra sem conflito deveria continuar vencendo, veio {resultado.category!r}"
    )
    assert resultado.reason == "user_rule"
    assert _regras(uid).get("padoca do ze") == "alimentacao", "a regra não podia ser tocada"


def test_categoria_existente_continua_bloqueando_aprendizado_conflitante():
    """O guard do #123 não foi enfraquecido: com a categoria já criada, o bot
    não pode gravar regra roubando o token dela."""
    uid = _uid()
    create_user_category(uid, "cafe")
    learn_from_inference(uid, "gastei 20 com cafe", "alimentacao", reason="local_rule")

    assert normalize_text(_regras(uid).get("cafe", "")) != "alimentacao", (
        f"o guard deixou gravar regra envenenada: {_regras(uid)}"
    )


def test_renomear_categoria_nao_deixa_regra_incoerente():
    uid = _uid()
    _diga(uid, "gastei 20 com cafe")
    nova = create_user_category(uid, "cafe")
    assert normalize_text(_regras(uid).get("cafe", "")) == "cafe", _regras(uid)

    update_user_category(uid, nova["id"], new_name="cafezinho")
    destino = _regras(uid).get("cafe", "")
    assert normalize_text(destino) == "cafezinho", (
        f"depois do rename a regra aponta para {destino!r}, que não existe mais"
    )


def test_apagar_categoria_nao_deixa_regra_apontando_pro_vazio():
    uid = _uid()
    _diga(uid, "gastei 20 com cafe")
    nova = create_user_category(uid, "cafe")
    assert "cafe" in _regras(uid)

    delete_user_category(uid, nova["id"])
    destino = _regras(uid).get("cafe")
    assert normalize_text(destino or "") != "cafe", (
        f"a regra sobrou apontando para a categoria apagada: {destino!r}"
    )


def test_conta_legada_regra_de_meses_atras_perde_sem_script():
    """O estado que já existe em produção HOJE, e o requisito explícito:
    consertar sem rodar script manual.

    Aqui a regra é semeada DIRETO na tabela, com a categoria já criada — é a
    conta de quem usou o bot por meses antes deste código existir. A
    reconciliação da criação (ponto 1) não alcança esse usuário: ele criou a
    categoria antes. Quem o conserta é o guard de LEITURA (ponto 2), na
    primeira classificação depois do deploy.
    """
    uid = _uid()
    # A ordem importa e é a do mundo real: a regra nasceu ANTES da categoria.
    # Depois desfaço a reconciliação da criação por SQL, para simular a conta
    # que criou a categoria quando este código ainda não existia — é o único
    # jeito de isolar o guard de LEITURA do conserto de escrita.
    upsert_category_rule(uid, "cafe", "alimentacao")
    create_user_category(uid, "cafe")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            # A data velha faz parte da simulação: é uma regra de MESES atrás.
            # Sem ela a reconciliação da criação (que renova o created_at) teria
            # deixado a regra mais nova que a categoria, e o guard a trataria
            # como escolha deliberada — que é o comportamento certo para uma
            # regra nova, e errado para a conta legada que este teste representa.
            "update user_category_rules "
            "   set category='alimentacao', created_at = now() - interval '90 days' "
            " where user_id=%s and keyword='cafe'",
            (uid,),
        )
        conn.commit()
    assert _regras(uid).get("cafe") == "alimentacao", "o estado legado não foi montado"

    resultado = infer_category(uid, "gastei 15 com cafe", allow_ai=False)
    assert normalize_text(resultado.category) == "cafe", (
        f"a regra legada venceu: {resultado.category!r} (reason={resultado.reason})"
    )
    assert _regras(uid).get("cafe") == "alimentacao", (
        "o guard de leitura não pode reescrever a tabela — ele só decide"
    )


def test_override_deliberado_depois_da_categoria_vence():
    """O usuário muda de ideia DEPOIS de criar a categoria — a escolha dele vale.

    Sequência: regra aprendida antiga → cria a categoria (a regra é reapontada)
    → o usuário manda explicitamente linkar `cafe` a outra categoria. Isso passa
    por `upsert_category_rule` (é o que a tool de categorias da IA usa), e a
    regra tem de nascer NOVA — senão herda a data velha, o guard a julga
    obsoleta, e o bot confirma a mudança enquanto continua classificando como
    antes: o pior tipo de falha, silenciosa e contrária ao que foi confirmado.
    """
    uid = _uid()
    _diga(uid, "gastei 20 com cafe")
    create_user_category(uid, "cafe")
    create_user_category(uid, "lazer do fim de semana")

    upsert_category_rule(uid, "cafe", "lazer do fim de semana")   # a escolha explícita

    resultado = infer_category(uid, "gastei 15 com cafe", allow_ai=False)
    assert normalize_text(resultado.category) == "lazer do fim de semana", (
        f"o override explícito foi ignorado: veio {resultado.category!r} "
        f"(reason={resultado.reason})"
    )


def test_falha_na_limpeza_nao_apaga_a_categoria(monkeypatch):
    """Delete é atômico: se a limpeza das regras estourar, a categoria continua lá.

    O contrário deixaria a categoria apagada, o endpoint devolvendo erro, e a
    retentativa dando CATEGORIA_NAO_ENCONTRADA — sem nenhum caminho de API para
    limpar as regras órfãs que sobraram.
    """
    import psycopg

    uid = _uid()
    _diga(uid, "gastei 20 com cafe")
    nova = create_user_category(uid, "cafe")

    original = psycopg.Cursor.execute

    def _explode(self, query, *a, **k):
        q = str(query).lower()
        if "delete from user_category_rules" in " ".join(q.split()):
            raise RuntimeError("falha simulada na limpeza")
        return original(self, query, *a, **k)

    monkeypatch.setattr(psycopg.Cursor, "execute", _explode)
    with pytest.raises(RuntimeError):
        delete_user_category(uid, nova["id"])
    monkeypatch.undo()

    from db.categories import get_user_category
    assert get_user_category(uid, nova["id"]) is not None, (
        "a categoria foi apagada mesmo com a limpeza falhando — não é atômico"
    )


def test_reconciliacao_nao_sobrescreve_override_concorrente():
    """A criação da categoria commita antes da reconciliação rodar.

    Nesse intervalo um comando explícito pode gravar a mesma keyword — e ele já
    foi confirmado ao usuário. A reconciliação só pode alcançar regra ANTERIOR à
    categoria; aqui a regra é posterior e tem de sobreviver.
    """
    from core.services.category_service import reconciliar_regras_com_categoria

    uid = _uid()
    create_user_category(uid, "cafe")
    create_user_category(uid, "lazer do fim de semana")
    upsert_category_rule(uid, "cafe", "lazer do fim de semana")   # posterior às duas

    reconciliar_regras_com_categoria(uid, "cafe")                 # roda atrasada

    assert _regras(uid).get("cafe") == "lazer do fim de semana", (
        f"a reconciliação atropelou a escolha explícita: {_regras(uid)}"
    )


def test_seed_de_conta_legada_nao_reverte_regra_deliberada():
    """A conta que nunca abriu a tela: o seed importa o histórico como categoria.

    Se essas linhas nascessem com a data de hoje, ficariam "mais novas" que
    todas as regras da conta e o guard descartaria até a regra deliberada — o
    usuário veria sua escolha revertida no primeiro acesso ao dashboard, sem
    nada ter mudado. Elas não são uma escolha feita agora; são o espelho do
    histórico, e por isso entram com data de época.
    """
    from db.categories import ensure_user_categories_seeded

    uid = _uid()
    # Histórico direto na tabela: um lançamento JÁ categorizado como "cafe",
    # que é o que o seed vai importar. Passar pelo `_diga` não serve — ele
    # categoriza como "alimentação" pelas LOCAL_RULES, o seed importaria
    # "alimentação", não haveria conflito nenhum e o teste passaria sozinho
    # (medido: passava com e sem o conserto).
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into launches (user_id, tipo, valor, categoria) "
            "values (%s, 'gasto', 20, 'cafe')",
            (uid,),
        )
        conn.commit()
    upsert_category_rule(uid, "cafe", "lazer")        # escolha deliberada, antes do seed

    ensure_user_categories_seeded(uid)                # primeiro acesso à tela
    assert "cafe" in [normalize_text(n) for n in
                      __import__("db").list_custom_category_names(uid)], (
        "o seed não importou a categoria do histórico — o teste não mede nada"
    )

    resultado = infer_category(uid, "gastei 15 com cafe", allow_ai=False)
    assert normalize_text(resultado.category) == "lazer", (
        f"o seed reverteu a escolha do usuário: {resultado.category!r}"
    )


def test_reconciliacao_nao_consulta_categorias_por_regra():
    """Criar categoria não pode custar uma consulta POR REGRA acumulada.

    Quem usa o bot há tempo tem centenas de keywords; uma consulta por regra
    (cada uma abrindo conexão) faz o POST do dashboard estourar o tempo.
    """
    import psycopg
    from core.services.category_service import reconciliar_regras_com_categoria

    uid = _uid()
    for i in range(12):
        upsert_category_rule(uid, f"termo{i}", "outros")
    create_user_category(uid, "cafe")

    consultas: list[str] = []
    original = psycopg.Cursor.execute

    def _spy(self, query, *a, **k):
        consultas.append(" ".join(str(query).lower().split()))
        return original(self, query, *a, **k)

    psycopg.Cursor.execute = _spy
    try:
        reconciliar_regras_com_categoria(uid, "cafe")
    finally:
        psycopg.Cursor.execute = original

    leituras_de_categoria = [q for q in consultas if "from user_categories" in q]
    assert len(leituras_de_categoria) <= 2, (
        f"{len(leituras_de_categoria)} leituras de user_categories para 12 regras — "
        f"está consultando por regra"
    )


def test_regra_obsoleta_nao_apaga_a_regra_valida_mais_curta():
    """Descartar a primeira regra não pode pular o passo B inteiro.

    As regras são consultadas por comprimento de keyword. Se a mais longa está
    obsoleta e uma mais curta é escolha deliberada, quem tem de ganhar é a
    deliberada — não o passo B2. Antes, o passo inteiro era pulado.
    """
    uid = _uid()
    upsert_category_rule(uid, "cafe especial", "alimentacao")   # antiga, vai obsoletar
    create_user_category(uid, "cafe")                            # passa a ser dona do termo
    create_user_category(uid, "lazer do fim de semana")
    upsert_category_rule(uid, "cafe", "lazer do fim de semana")  # deliberada, posterior

    # desfaz a reconciliação da regra longa, para ela continuar obsoleta
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update user_category_rules "
            "   set category='alimentacao', created_at = now() - interval '90 days' "
            " where user_id=%s and keyword='cafe especial'",
            (uid,),
        )
        conn.commit()

    resultado = infer_category(uid, "gastei 15 com cafe especial", allow_ai=False)
    assert normalize_text(resultado.category) == "lazer do fim de semana", (
        f"a regra deliberada mais curta perdeu a vez: {resultado.category!r} "
        f"(reason={resultado.reason})"
    )


def test_falha_ao_checar_obsolescencia_nao_derruba_o_lancamento(monkeypatch):
    """Hiccup na consulta de metadado não pode impedir o gasto de ser gravado.

    O passo B2 já degrada devolvendo None em exceção. O guard novo tem de ter o
    mesmo contrato: na dúvida, vale a regra — nunca estourar pelo handler.
    """
    import core.services.category_service as cs

    uid = _uid()
    upsert_category_rule(uid, "cafe", "alimentacao")

    def _explode(*a, **k):
        raise RuntimeError("falha transitória de banco")

    monkeypatch.setattr(cs, "list_custom_category_names", _explode)
    resultado = cs.infer_category(uid, "gastei 15 com cafe", allow_ai=False)
    assert normalize_text(resultado.category) == "alimentacao", (
        f"a falha de metadado mudou o resultado: {resultado.category!r}"
    )


def test_rename_faz_a_categoria_adquirir_os_termos_novos():
    """Renomear é o momento em que a categoria passa a ser dona do termo novo.

    Cenário: existe a categoria "viagens"; o bot aprende `cafe -> alimentação`;
    o usuário renomeia "viagens" para "cafe". Com o `created_at` original da
    categoria, a regra pareceria posterior — escolha deliberada — e os gastos
    com café continuariam em alimentação, exatamente depois de o usuário ter
    renomeado a categoria para recebê-los.
    """
    uid = _uid()
    cat = create_user_category(uid, "viagens")
    upsert_category_rule(uid, "cafe", "alimentacao")

    update_user_category(uid, cat["id"], new_name="cafe")

    resultado = infer_category(uid, "gastei 15 com cafe", allow_ai=False)
    assert normalize_text(resultado.category) == "cafe", (
        f"depois do rename o gasto foi para {resultado.category!r} "
        f"(reason={resultado.reason})"
    )


def test_editar_so_emoji_e_cor_nao_estoura():
    """Edição que NÃO renomeia continua funcionando.

    A primeira versão do conserto de rename derivava a condição de uma variável
    que só existe dentro do ramo do rename — então uma edição de emoji ou cor
    estourava com UnboundLocalError e o PATCH devolvia erro de servidor. Nenhum
    teste do grupo cobria esse caminho, que é o mais comum da tela.
    """
    uid = _uid()
    cat = create_user_category(uid, "cafe")

    def _criada_em():
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("select created_at from user_categories where id=%s", (cat["id"],))
            return cur.fetchone()["created_at"]

    antes = _criada_em()

    atualizada = update_user_category(uid, cat["id"], emoji="☕", color="#123456")

    assert atualizada["emoji"] == "☕"
    assert atualizada["color"] == "#123456"
    assert atualizada["name"] == "cafe"
    assert _criada_em() == antes, (
        "edição sem rename não pode mexer no created_at — ele marca desde quando "
        "a categoria é dona do termo, e o termo não mudou"
    )
