"""`GET`/`POST /api/v2/guia`: o guia do /painel (#728), estado em `db/guia.py`.

`PASSOS` é a fonte única do roteiro (falas, âncoras, ações, `dado`): o cliente desenha
o selo "exemplo" a partir de `dado`. Passo 1 depende do Saiu real (`motivo_resumo`); os outros (Organizar, categorias, Ferramentas, Piggy)
estão sempre disponíveis: o servidor não olha se há categoria. Estado, na ordem:
`concluido` (todos os passos feitos) > `oferecer` (já tinha concluído o roteiro antigo, falta
passo de agora e o convite das novidades, `guia.NOVOS` em `dicas`, ainda não foi visto) > `dispensado`
> `em_andamento` (já oferecido, pelo convite ou pela Ajuda, ou já fez algum, ou viu o convite das
novidades: o cliente não mostra o convite sozinho, só retoma pela Ajuda) > `oferecer` (nada feito,
nunca oferecido, passo 1 disponível) > `indisponivel`. `motivo` do topo = o do passo 1 quando ele está indisponível, só nos dois
que podem tê-lo (`em_andamento` e `indisponivel`).

O POST é escrita: o CSRF do monólito vale antes. `feito` sem `passo` = 422 no
envelope; `passo` fora do roteiro = 422 pelo Literal; `feito` de passo indisponível = 409
`passo_indisponivel`, sem gravar. Nas outras ações `passo` é ignorado. `ordem {aba, n}`
(opcional) em `dispensar`/`reabrir`: o gesto da mesma aba que o último gesto aplicado do usuário, com `n` menor ou igual
ao dele, não grava.

`DICAS`: a dica de primeiro uso de cada tela (o cliente a mostra uma vez; `vista` = já
apareceu). Só vem a dica da tela que o plano dá (`_RECURSO`). `POST /guia/dica` carimba a
1ª vez sem tocar o guia; id fora de `DICAS` = 422 pelo Literal. Dica que o plano não dá
grava e é inofensiva: o GET não a devolve.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from api.v2.sessao import usuario_atual
from core.services.plan_service import painel_chat_ia, plan_gate_ok
from db import guia

router = APIRouter()

PASSOS = (
    {"id": "resumo.saiu", "tela": "resumo", "ancora": "resumo.saiu", "acao": "mes.trocado",
     "dado": "real", "avanca": "cliente",
     "fala": {"titulo": "O que saiu este mês",
              "apresenta": "Esse é o que saiu da sua conta este mês, direto do seu banco.",
              "texto": "Toca aqui pra comparar com o mês passado."}},
    {"id": "resumo.organizar", "tela": "resumo", "ancora": "painel.organizar", "acao": "organizar.ligado",
     "dado": "real", "avanca": "cliente",
     "fala": {"titulo": "Do seu jeito",
              "apresenta": "Esse painel é seu: dá pra trocar os blocos de lugar, tirar e pôr outros.",
              "texto": "Toca em Organizar, mexe no que quiser e toca em Pronto."}},
    {"id": "gastos.categoria", "tela": "gastos", "ancora": "categorias.lista", "acao": "categoria.aberta",
     "dado": "real", "avanca": "cliente",
     "fala": {"titulo": "Pra onde foi o dinheiro",
              "apresenta": "Aqui ficam suas categorias: pra onde foi cada real do mês.",
              "texto": "Toca numa categoria pra ver de onde vem o total."}},
    {"id": "ferramentas.abrir", "tela": "ferramentas", "ancora": "ferramentas.compra", "acao": "ferramenta.aberta",
     "dado": "real", "avanca": "cliente",
     "fala": {"titulo": "Ferramentas",
              "apresenta": "Aqui moram as ferramentas que ajudam a decidir.",
              "texto": "Abre uma pra ver."}},
    {"id": "piggy.pergunta", "tela": "piggy", "ancora": "piggy.pergunta", "acao": "piggy.perguntou",
     "dado": "exemplo", "avanca": "cliente",
     "fala": {"titulo": "Pergunta pro Piggy",
              "apresenta": "Aqui você conversa comigo. A barra de conversa fica embaixo em toda tela: "
                           "pergunta o que quiser. As respostas ainda são de exemplo; "
                           "a conversa de verdade chega em breve.",
              "texto": "Toca numa pergunta pronta e me vê responder."}},
)
IDS = [p["id"] for p in PASSOS]
# Com o freio do chat da IA ligado (PAINEL_CHAT_IA_USER_IDS) o último passo é de verdade: o Piggy responde com a conta do usuário.
PASSO_PIGGY_REAL = {"dado": "real", "fala": {**next(p for p in PASSOS if p["id"] == "piggy.pergunta")["fala"], "apresenta": "Aqui você conversa comigo. A barra de conversa fica embaixo em toda tela: pergunta o que quiser e eu respondo com os dados da sua conta."}}

DICAS = (
    {"id": "assinaturas.marcas", "tela": "assinaturas", "titulo": "Como eu acho suas assinaturas",
     "texto": "Eu acho essas cobranças no extrato que chega do seu banco pelo Open Finance. "
              "“Ignorar” e “É assinatura” só me ensinam o que é o quê: nada é cancelado aqui."},
    {"id": "previsao.saldo", "tela": "previsao", "titulo": "Como eu prevejo seu saldo",
     "texto": "Eu parto do seu saldo de hoje e ando dia a dia: somo suas receitas e tiro boletos, faturas, "
              "gastos fixos e uma estimativa do gasto do dia a dia. É uma estimativa, não uma promessa."},
    {"id": "metas.ritmo", "tela": "metas", "titulo": "Como eu acompanho suas metas",
     "texto": "Eu mostro quanto cada meta já tem, quanto falta e se o seu ritmo chega lá. "
              "O valor vem da conta ou do investimento que você escolher pra ela."},
    {"id": "patrimonio.soma", "tela": "patrimonio", "titulo": "De onde vem seu patrimônio",
     "texto": "Eu somo a Carteira, as contas, as caixinhas e os investimentos, e tiro uma foto por dia, no fim da tarde. "
              "A linha aparece no dia seguinte à primeira foto."},
    {"id": "simulador.corte", "tela": "simulador", "titulo": "Aqui você só testa",
     "texto": "Corte um gasto do dia a dia e eu mostro quanto sobra por mês e como fica seu saldo nos próximos "
              "90 dias. É só simulação: nada muda na sua conta."},
)
DICA_IDS = [d["id"] for d in DICAS]
# A tela → o recurso do plano, o mesmo do gate do endpoint dela (assinaturas.py, previsao.py, simulador.py).
# Metas e Patrimônio não têm tier: "generic" (a base da escada) é Essencial ou acima: em carência (tier free) a dica não vem, limite aceito.
_RECURSO = {"assinaturas": "subscriptions", "previsao": "forecast", "simulador": "simulator",
            "metas": "generic", "patrimonio": "generic"}

PassoId = Literal[tuple(IDS)]
DicaId = Literal[tuple(DICA_IDS)]
Motivo = Literal["sem_dados", "sincronizando", "conexao_com_erro"]


class Fala(BaseModel):
    """`apresenta`: o bloco, antes do "Entendi"; `texto`: o que tocar, depois dele."""
    titulo: str
    apresenta: str
    texto: str


class Passo(BaseModel):
    id: PassoId
    tela: Literal["resumo", "gastos", "ferramentas", "piggy"]
    ancora: str
    acao: str
    dado: Literal["real", "exemplo"]
    fala: Fala
    avanca: Literal["cliente"]
    disponivel: bool
    motivo: Motivo | None
    feito: bool


class Dica(BaseModel):
    id: DicaId
    tela: Literal["assinaturas", "previsao", "metas", "patrimonio", "simulador"]
    titulo: str
    texto: str
    vista: bool


class Guia(BaseModel):
    estado: Literal["oferecer", "em_andamento", "concluido", "dispensado", "indisponivel"]
    motivo: Motivo | None
    passos: list[Passo]
    dicas: list[Dica]


class Ordem(BaseModel):
    """`aba` sorteada por carregamento de página; `n` só cresce dentro da aba."""
    aba: str = Field(min_length=1, max_length=32, pattern=r"^[^\x00]+$")
    n: int = Field(ge=1, le=2**31 - 1)


class AcaoGuia(BaseModel):
    acao: Literal["visto", "feito", "dispensar", "reabrir"]
    passo: PassoId | None = None
    ordem: Ordem | None = None


class DicaIn(BaseModel):
    dica: DicaId


def _disponivel(passo: str, motivo1: str | None) -> bool:
    return passo != IDS[0] or motivo1 is None


def _guia(linha: dict | None, motivo1: str | None, uid: int) -> Guia:
    feitos = (linha or {}).get("feitos") or {}
    vistas = (linha or {}).get("dicas") or {}
    if linha and linha["concluido_em"] and all(i in feitos for i in IDS):
        estado = "concluido"
    elif linha and linha["concluido_em"]:  # concluiu o roteiro antigo: convite único dos passos novos
        estado = "em_andamento" if guia.NOVOS in vistas else "oferecer"
    elif linha and linha["dispensado_em"]:
        estado = "dispensado"
    elif feitos or (linha and linha["oferecido_em"]):
        estado = "em_andamento"
    else:
        estado = "oferecer" if motivo1 is None else "indisponivel"
    real = painel_chat_ia(uid)
    passos = [Passo(**(p | PASSO_PIGGY_REAL if real and p["id"] == "piggy.pergunta" else p),
                    disponivel=_disponivel(p["id"], motivo1), motivo=None if i else motivo1,
                    feito=p["id"] in feitos) for i, p in enumerate(PASSOS)]
    dicas = [Dica(**d, vista=d["id"] in vistas) for d in DICAS if plan_gate_ok(uid, _RECURSO[d["tela"]])]
    return Guia(estado=estado, motivo=motivo1 if estado in ("em_andamento", "indisponivel") else None,
                passos=passos, dicas=dicas)


@router.get("/guia", response_model=Guia)
def ler(uid: int = Depends(usuario_atual)) -> Guia:
    return _guia(guia.ler(uid), guia.motivo_resumo(uid), uid)


@router.post("/guia", response_model=Guia)
def registrar(corpo: AcaoGuia, uid: int = Depends(usuario_atual)) -> Guia:
    if corpo.acao == "feito" and corpo.passo is None:
        raise RequestValidationError([{"loc": ("body", "passo"), "msg": "Falta o passo.",
                                       "type": "value_error"}])
    motivo1 = guia.motivo_resumo(uid)
    if corpo.acao == "feito" and not _disponivel(corpo.passo, motivo1):
        raise HTTPException(status_code=409, detail={"error": "passo_indisponivel"})
    return _guia(guia.registrar(uid, corpo.acao, corpo.passo, IDS,
                                  corpo.ordem and (corpo.ordem.aba, corpo.ordem.n)), motivo1, uid)


@router.post("/guia/dica", response_model=Guia)
def dica(corpo: DicaIn, uid: int = Depends(usuario_atual)) -> Guia:
    return _guia(guia.registrar(uid, "dica", corpo.dica, IDS), guia.motivo_resumo(uid), uid)
