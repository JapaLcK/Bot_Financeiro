"""Portão de ROTA: as TRÊS rotas do Pix estão registradas, e só elas (PR 1b-B).

**Este arquivo mudou de direção, e o nome mudou junto.** Ele era
`tests/test_pix_rota_inerte.py` e PROIBIA rota de Pix; com o router registrado,
um arquivo chamado "inerte" afirmando o contrário seria a mentira verde que o
próprio docstring abaixo manda evitar. Agora ele EXIGE — conjunto exato, rota a
mais também reprova.

Assunto próprio, e não uma seção de `tests/test_pix_inerte.py`, porque fechá-lo
exigiu **três** introspecções — e o porquê de duas não bastarem é a parte que
alguém vai precisar ler antes de "simplificar" isto de volta.

## O histórico, porque ele é a razão de cada linha

1. **`app.routes`** foi a primeira versão. Cega em FastAPI 0.116+ para rota
   vinda de `include_router` — e o `requirements.txt:58` pinga **0.141.1**,
   enquanto o `.venv` local tem **0.115.6**. O portão era verde na versão que o
   CI e a produção instalam. O repositório já sabia disso
   (`tests/test_routes_pockets_cards.py:31-36`).
2. **`openapi()["paths"]`** foi o conserto, e trocou uma cegueira por três:
   `include_in_schema=False`, `Mount` e `Route` cru somem do schema. O Tester
   plantou no monólito REAL um `@router.post("/billing/pix/webhook",
   include_in_schema=False)` — 15 testes verdes, e a rota respondendo 403 de
   CSRF, ou seja existindo e casando.
3. **A UNIÃO das duas ainda é cega** em 0.141.1 para `include_router` +
   `include_in_schema=False`, que é exatamente a forma plantada. Medido.

Por isso a árvore (`_andar_nas_rotas`) entrou: ela não depende de o FastAPI
achatar nada. `paths_expostos` é a união dela com o `openapi()`, que fica como
piso estável.

## A atenuante que NÃO cobre a metade perigosa

Um webhook escondido precisaria de isenção de CSRF, e
`test_csrf_exempt_paths_nao_tem_entrada_de_asaas` o pegaria. Mas o **checkout** —
quem de fato emite a cobrança — é POST autenticado com token de CSRF e não
precisa de isenção nenhuma. O portão de rota tem de enxergar sozinho.

## A CEGUEIRA DECLARADA VIROU DETECÇÃO — é o que a inversão comprou

Este portão mede o app **IMPORTADO**, nunca o que **RODA**. Na direção antiga
("proíbe"), duas formas de registrar passavam verdes com a rota respondendo em
produção, e as duas têm precedente neste próprio monólito:

  * **(a) registro condicionado a env** —
    `if os.getenv("ASAAS_PIX_ANNUAL_ENABLED") == "1": app.include_router(pix)`.
    Sem a env a rota não existe; com ela existe. O monólito já faz isso
    literalmente: `if ENABLE_DEV_ENDPOINTS: app.add_api_route(...)`.
  * **(b) registro no `lifespan`** — antes do startup não está nas rotas, depois
    está. Este portão importa o módulo e nunca sobe o app.

Na direção **"exige"**, as duas ficam **VERMELHAS** no CI, que não tem a env e
não sobe o app. É a mesma medição lendo o outro lado, e por isso
`test_a_rota_existe_sem_a_flag_no_ambiente` é o caso que fecha (a) por nome.

**A troca de risco, e ela é a direção segura:** introspecção quebrada antes dava
falso **VERDE** (portão cego, rota escondida respondendo); agora dá falso
**VERMELHO**. `test_a_introspeccao_ve_as_quatro_classes_de_rota` continua sendo
quem distingue "a rota sumiu" de "a árvore quebrou" — sem ele, uma quebra do
FastAPI viraria "alguém apagou o checkout".

CEGUEIRA DECLARADA: rota DENTRO de um sub-app montado é encontrada; o prefixo do
`Mount` é concatenado. O repositório não tem nenhum `Mount(` nem `app.mount(`
hoje (medido).
"""

import pytest


def _andar_nas_rotas(rotas, prefixo: str = "", visto=None):
    """Desce a ÁRVORE de roteamento, montando o path completo.

    Uma introspecção só não basta, e nenhuma das duas óbvias basta. Medido nas
    duas versões que importam (`.venv` local **0.115.6**, `requirements.txt:58`
    **0.141.1**), com app sintético cobrindo as quatro classes:

    | classe de rota                          | `app.routes` | `openapi()` |
    |-----------------------------------------|--------------|-------------|
    | `include_router`                        | só em 0.115  | sim         |
    | `include_router` + `include_in_schema=False` | só em 0.115 | **não**  |
    | rota direta + `include_in_schema=False` | sim          | **não**     |
    | `Mount` / `Route` cru                   | sim          | **não**     |

    A segunda linha é o furo: em **0.141.1 a UNIÃO das duas ainda é cega** —
    e é exatamente a forma que o Tester plantou no monólito real
    (`@router.post("/billing/pix/webhook", include_in_schema=False)`), com o
    portão em 15 verdes e a rota respondendo 403 de CSRF.

    Esta função fecha as quatro nas duas versões, porque não depende de o
    FastAPI achatar nada: em 0.141.1 as rotas incluídas vivem em
    `_IncludedRouter.original_router.routes`, com o prefixo em
    `include_context.prefix`. A expressão do prefixo cobre os dois casos de uma
    vez — `include_context.prefix` para router incluído, o próprio `path` para
    `Mount`.
    """
    visto = visto if visto is not None else set()
    for rota in rotas:
        # A poda é por **(objeto, prefixo)**, não por objeto — e a diferença é
        # uma rota de venda invisível. Em 0.141.1 as filhas de um
        # `_IncludedRouter` são os MESMOS objetos `APIRoute` (vivem em
        # `original_router.routes`), então incluir o mesmo router duas vezes com
        # prefixos diferentes fazia a SEGUNDA inclusão ser podada inteira:
        #
        #     app.include_router(pix, prefix="/interno/diagnostico")  # inocente
        #     app.include_router(pix, prefix="/billing/pix")          # a venda
        #
        # O portão via só `/interno/diagnostico/checkout` e ficava VERDE,
        # enquanto `/billing/pix/checkout` respondia 200. Alias de path já existe
        # neste monólito (`/wa/webhook` e `/webhook` para o mesmo handler,
        # `frontend/finance_bot_websocket_custom.py:2057-2061`), então a forma
        # não é exótica. O `visto` continua existindo para cortar ciclo, que é
        # o que ele de fato protege.
        chave = (id(rota), prefixo)
        if chave in visto:
            continue
        visto.add(chave)
        path = getattr(rota, "path", None)
        if isinstance(path, str):
            yield prefixo + path
        filhas = None
        for fonte in (getattr(rota, "original_router", None), rota,
                      getattr(rota, "app", None)):
            filhas = getattr(fonte, "routes", None)
            if filhas:
                break
        if filhas:
            ctx = getattr(rota, "include_context", None)
            passo = getattr(ctx, "prefix", "") or (path if isinstance(path, str) else "")
            yield from _andar_nas_rotas(filhas, prefixo + passo, visto)


def paths_expostos(app) -> set[str]:
    """UNIÃO da árvore com o schema OpenAPI.

    A árvore é a rede larga; o `openapi()` é o piso estável. Se um FastAPI
    futuro renomear `original_router`, o `openapi()` ainda pega as rotas
    visíveis e `test_a_introspeccao_ve_as_quatro_classes_de_rota` fica VERMELHO
    dizendo que a árvore quebrou — em vez de o portão cegar em silêncio, que é
    o defeito que esta rodada consertou.
    """
    return {p for p in (set(_andar_nas_rotas(app.routes))
                        | set(app.openapi()["paths"])) if p}


def _app_sintetico():
    """App de mentira com as quatro classes de rota, para medir a TÉCNICA.

    Sintético de propósito: o monólito não tem hoje nenhuma rota
    `include_in_schema=False` nem `Mount` (`grep` por ambos: zero), então um
    piso tirado dele provaria só o que ele já usa — e foi assim que os dois
    pisos anteriores ficaram satisfeitos o tempo todo enquanto a rota escondida
    passava.
    """
    from fastapi import APIRouter, FastAPI
    from starlette.routing import Mount, Route

    app = FastAPI()
    incluido = APIRouter()

    @incluido.post("/inc/visivel")
    def _a():  # pragma: no cover - nunca chamada
        return {}

    @incluido.post("/inc/oculta", include_in_schema=False)
    def _b():  # pragma: no cover
        return {}

    app.include_router(incluido, prefix="/pfx")

    @app.get("/direta/oculta", include_in_schema=False)
    def _c():  # pragma: no cover
        return {}

    async def _cru(request):  # pragma: no cover
        return None

    app.router.routes.append(Route("/route/cru", _cru))
    app.router.routes.append(Mount("/montado", routes=[]))

    # O MESMO router incluído DUAS vezes, com prefixos diferentes. A segunda
    # inclusão é a que importa: em 0.141.1 as filhas são os mesmos objetos
    # `APIRoute`, então uma poda por `id(rota)` descarta esta inclusão inteira.
    # `include_in_schema=False` NÃO é enfeite aqui: sem ele o `openapi()` acha as
    # duas inclusões e o caso não mede nada. Medido — com a rota no schema, a
    # poda por `id` sai VERDE nas duas versões. As duas condições juntas são o
    # furo: router repetido E fora do schema.
    repetido = APIRouter()

    @repetido.post("/checkout", include_in_schema=False)
    def _d():  # pragma: no cover
        return {}

    app.include_router(repetido, prefix="/interno/diagnostico")
    app.include_router(repetido, prefix="/segundo/prefixo")
    return app


QUATRO_CLASSES = [
    "/pfx/inc/visivel",
    "/pfx/inc/oculta",     # a que a UNIÃO das duas óbvias não via em 0.141.1
    "/direta/oculta",
    "/route/cru",
    "/montado",
    "/interno/diagnostico/checkout",
    # A SEGUNDA inclusão do mesmo router, fora do schema.
    #
    # **Só DISCRIMINA sob 0.141.1**, e isto precisa estar escrito: lá as filhas
    # de um `_IncludedRouter` são objetos COMPARTILHADOS entre as inclusões, e
    # a poda por `id(rota)` descartava a segunda inteira. Sob o `.venv` local
    # (0.115.6) o FastAPI achata as rotas no momento da inclusão, criando
    # objetos distintos por prefixo — as duas aparecem com e sem o conserto, e
    # o negativo sai VERDE.
    #
    # Medido nas duas, com a poda por `id` reposta:
    #     0.141.1  ->  /billing/pix  NADA   (portão cego, rota respondendo)
    #     0.115.6  ->  /billing/pix  achada (as duas técnicas concordam)
    #
    # Ou seja: rodar `pytest` na máquina do dev NÃO prova este caso. Quem prova
    # é o CI, que instala o `requirements.txt`.
    "/segundo/prefixo/checkout",
]


@pytest.mark.parametrize("caminho", QUATRO_CLASSES)
def test_a_introspeccao_ve_as_quatro_classes_de_rota(caminho):
    """PISO da técnica, e o que substitui os dois pisos que não mediam nada.

    Os anteriores (`/billing/webhook` e `/pockets/…`) provam só que a
    introspecção enxerga `include_router` — estavam satisfeitos enquanto uma
    rota `include_in_schema=False` existia e respondia. Este roda contra um app
    que TEM as quatro classes, então cada uma é medida.
    """
    assert caminho in paths_expostos(_app_sintetico()), (
        f"a introspecção não vê {caminho!r} — o portão de rota está cego para "
        "esta classe, e uma rota de venda escondida nela passaria"
    )


def _paths_do_app() -> set[str]:
    import frontend.finance_bot_websocket_custom as app_mod

    return paths_expostos(app_mod.app)


# As três, e SÓ estas três. Conjunto exato: rota a mais reprova junto com rota a
# menos. Uma quarta rota de Pix aparecendo sem passar por aqui é exatamente o
# que este portão existe para não deixar acontecer em silêncio.
ROTAS_DO_PIX = {
    "/billing/pix/checkout",
    "/billing/pix/{public_token}",
    "/billing/asaas/webhook",
}


def _rotas_de_pix_no_app() -> set[str]:
    return {p for p in _paths_do_app()
            if p.startswith("/billing/pix") or "asaas" in p.lower()}


def test_as_tres_rotas_do_pix_estao_registradas():
    """A INVERSÃO. O 1b-B ligou o Pix, e o portão passa a exigir as três.

    Conjunto EXATO nos dois sentidos:

      * faltando → o checkout, o poll ou o webhook não foram registrados, e o
        deploy sobe com o Asaas mandando evento para um 404 (15 falhas seguidas
        pausam a fila do painel deles);
      * sobrando → rota nova de Pix que ninguém revisou. `/billing/pix` é
        prefixo de dinheiro; cada path aqui foi olhado no commit que o trouxe.
    """
    caminhos = _paths_do_app()
    assert "/billing/webhook" in caminhos, "introspecção quebrada no app real"
    assert _rotas_de_pix_no_app() == ROTAS_DO_PIX, (
        "o conjunto de rotas do Pix não é o esperado — faltando: "
        f"{sorted(ROTAS_DO_PIX - _rotas_de_pix_no_app())}; sobrando: "
        f"{sorted(_rotas_de_pix_no_app() - ROTAS_DO_PIX)}"
    )


def test_a_rota_existe_sem_a_flag_no_ambiente(monkeypatch):
    """O caso que fecha a cegueira (a): registro atrás de env.

    O CI não tem `ASAAS_PIX_ANNUAL_ENABLED`, então o teste acima já roda nessa
    condição — mas isso é acidente do ambiente, não asserção. Aqui a ausência é
    EXPLÍCITA: quem puser o `include_router` atrás de um `if os.getenv(...)`
    para "ligar só em produção" vê vermelho com o nome do problema.

    A env some do processo, o módulo do app é **recarregado**, e as três rotas
    têm de continuar lá. Sem o reload o módulo já importado esconderia o
    registro condicional, e o teste passaria por vácuo.
    """
    import importlib

    import frontend.finance_bot_websocket_custom as app_mod

    monkeypatch.delenv("ASAAS_PIX_ANNUAL_ENABLED", raising=False)
    recarregado = importlib.reload(app_mod)
    assert ROTAS_DO_PIX <= paths_expostos(recarregado.app), (
        "as rotas do Pix sumiram sem a env — o registro está condicionado a "
        "flag, e é exatamente o que este portão passou a proibir"
    )


def test_csrf_exempt_paths_tem_so_o_webhook_do_asaas():
    """A isenção de CSRF é do webhook, e de mais NADA do Pix.

    O webhook é server-to-server e precisa dela. O checkout e o poll são
    chamadas do navegador logado, com cookie e token de CSRF — isenção ali seria
    um POST de emissão de cobrança aberto a qualquer origem. É o par do teste
    acima: um mede que a rota existe, este mede que ela não veio com privilégio
    a mais.
    """
    import frontend.finance_bot_websocket_custom as app_mod

    isentos = app_mod.CSRF_EXEMPT_PATHS
    assert "/open-finance/pluggy/webhook" in isentos, "CSRF_EXEMPT_PATHS mudou de forma"
    do_pix = {p for p in isentos
              if "asaas" in p.lower() or p.startswith("/billing/pix")}
    assert do_pix == {"/billing/asaas/webhook"}, (
        f"isenção de CSRF do Pix não é só o webhook: {sorted(do_pix)}"
    )


