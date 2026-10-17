"""
core/services/ai_chat/tools/metas_escrita.py — escrever meta pelo chat (PR C4), só com a chave das metas.

  - create_meta  GRAVA NA HORA quando a MENSAGEM DO TURNO manda criar (`pede_criar_meta_ja`, em código e não
                 só no prompt: gramática fechada em `core/services/metas_gate.py`). Desejo, hipótese, pergunta, relato ou lançamento
                 que cita meta viram "Só confirmando" (gancho `confirmar_se` do runner: pendência de 10 min,
                 o "sim" grava 1 vez). A resposta é a da própria tool, do Decimal gravado. A trava contra
                 criar duas vezes é o nome repetido recusado (igualdade normalizada, por usuário).
  - edit_meta / delete_meta  pedem confirmação ("sim"/"não"); a pendência leva o NOME, não o id.

Escreve SÓ pelas funções da rota (`api.v2.metas`): a validação de fronteira mora lá. Aqui só se acha a meta
pelo nome (igualdade, nunca contenção: lição do `nome_do_alvo`) e se escreve a frase. `user_id` vem do
runner; `id`/`user_id` em `args` são ignorados. Erro de usuário vira frase, nunca exceção.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from core.observability import _log_falha
from core.services import metas_conversa as mc
from utils_date import today_tz

from .._context import metas_do_turno
from ._base import Tool
from .metas_fonte import Recusa as _Recusa, chave as _chave, fonte as _fonte

SEM_METAS = "🐷 Metas ainda não estão disponíveis pra você por aqui."
_PAINEL = "pigbankai.com/painel (seção Metas)"
_HTTP = {  # `detail["error"]` da rota -> frase
    "pro_required": "🐷 Seu plano atual não inclui investimentos. Me diz de qual conta a meta acompanha o saldo.",
    "reserva_ja_existe": "🐷 Você já tem uma reserva de emergência.",
    "meta_nao_encontrada": "🐷 Não achei essa meta (pode ter sido apagada). Me diz o nome de novo.",
}
_CAMPOS = {  # campo do pydantic -> o que dizer (a mensagem do pydantic é em inglês)
    "nome": "o nome (1 a 60 caracteres, sem quebra de linha)", "tipo": "o tipo (meta ou reserva)",
    "alvo": "o valor (até R$ 999.999.999,99, no máximo 2 casas)", "meses": "os meses (de 1 a 24)",
    "data_alvo": "a data (AAAA-MM-DD)",
}


def _guarda(tool: str, falha: str):
    """Gate da chave (a chave pode cair entre armar e o "sim"; N só chega aqui imitando o histórico)
    e erro de usuário em frase. Sem a frase: loga a causa e responde genérico."""
    def deco(f):
        def g(user_id: int, args: dict[str, Any]) -> str | None:
            if not metas_do_turno(user_id):
                return SEM_METAS
            try:
                return f(user_id, args)
            except _Recusa as e:
                return str(e)
            except (ValidationError, RequestValidationError) as e:
                x = e.errors()[0]
                if isinstance(e, RequestValidationError):
                    return f"🐷 {x['msg']}"
                return f"🐷 Não consegui: {_CAMPOS.get((x['loc'] or [None])[0], 'um dos dados')} não vale. Confere e me manda de novo."
            except Exception as e:
                cod = e.detail.get("error") if isinstance(e, HTTPException) and isinstance(e.detail, dict) else None
                if cod in _HTTP:
                    return _HTTP[cod]
                _log_falha(tool, user_id, e)
                return f"🐷 Não consegui {falha} agora. Tenta de novo em instantes."
        return g
    return deco


def _txt(v: Any) -> str | None:
    # O valor vira TEXTO sem aritmética: `str(5000.0)` = "5000.0"; bool, NaN, 1e300 e "5.000,00" caem no `_VALOR` da rota.
    return None if v is None else str(v).strip()


def _sem_controle(nome: Any) -> None:
    # No texto CRU: o `NovaMeta` já trocou NUL por U+FFFD, mas o JSONB da pendência recusa o NUL (DataError no runner).
    if any(ord(c) < 32 for c in str(nome or "")):
        raise _Recusa("🐷 O nome não pode ter caracteres de controle (quebra de linha, tab...). Use só texto.")


def _dmy(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"


def _iguais(metas: list[dict], nome: Any) -> list[dict]:
    return [m for m in metas if _chave(m["nome"]) == _chave(nome)]


def _acha(metas: list[dict], nome: Any) -> dict:
    """A meta de nome IGUAL (normalizado) entre as do usuário; 0 ou 2+ viram frase."""
    mostra = " ".join(str(nome or "").replace("\x00", "").split())[:60]
    if not mostra:
        raise _Recusa("🐷 Me diz o nome da meta.")
    achou = _iguais(metas, mostra)
    if len(achou) == 1:
        return achou[0]
    if achou:
        raise _Recusa(f"🐷 Você tem mais de uma meta chamada *{mostra}*: mude ou apague no painel (pigbankai.com/painel).")
    nomes = ", ".join(m["nome"] for m in metas[:mc.MAX_METAS])
    raise _Recusa(f"🐷 Não achei a meta *{mostra}*." + (f" Suas metas: {nomes}." if nomes else " Você ainda não tem metas."))


# ─── create_meta (grava na hora) ─────────────────────────────────────────────

def _confere(corpo) -> Any:
    """O que a rota confere DEPOIS do formato e o `validate`/`confirmar_se` precisam saber antes do "sim":
    valor > 0, meses 1–24, data legível. Devolve a data (ou None)."""
    from api.v2.lancamentos import _valor
    from api.v2.metas import _data_alvo, _meses  # privados de propósito: uma só leitura de cada regra
    if corpo.alvo is not None:
        _valor(corpo.alvo)
    _meses(corpo.meses)
    return _data_alvo(corpo.data_alvo)


def _prepara_criar(user_id: int, args: dict[str, Any]):
    """Tudo que o `execute` confere antes de gravar, sem escrever: `(NovaMeta, frase da fonte)` ou levanta."""
    from api.v2.metas import NovaMeta
    _sem_controle(args.get("nome"))
    fonte, como = _fonte(user_id, args.get("fonte"))
    corpo = NovaMeta(nome=args.get("nome"), tipo=args.get("tipo"), alvo=_txt(args.get("alvo")),
                     meses=args.get("meses"), data_alvo=args.get("data_alvo"), fonte=fonte)
    if corpo.tipo == "meta" and (corpo.alvo is None or corpo.meses is not None) or corpo.tipo == "reserva" and corpo.alvo is not None:
        raise _Recusa("🐷 Meta pede o valor (e não tem meses); a reserva não tem valor, só meses.")
    if (prazo := _confere(corpo)) and prazo < today_tz():
        raise _Recusa("🐷 O prazo não pode estar no passado.")
    if (ja := _iguais(mc.ler(user_id)["metas"], corpo.nome)):
        alvo = f" (alvo {mc._brl(Decimal(ja[0]['alvo']))})" if ja[0]["alvo"] is not None else ""
        raise _Recusa(f'🐷 Você já tem a meta "{ja[0]["nome"]}"{alvo}. Pra mudar o valor, me diga '
                      f'"muda a meta {ja[0]["nome"]} pra R$ X"; pra outra meta, use outro nome.')
    return corpo, como


@_guarda("create_meta", "criar a meta")
def _criar(user_id: int, args: dict[str, Any]) -> str:
    from api.v2.metas import criar
    from db.metas import MESES_RESERVA
    corpo, como = _prepara_criar(user_id, args)
    criar(corpo, uid=user_id)
    if corpo.tipo == "reserva":
        o = f'sua reserva de emergência "{corpo.nome}": {corpo.meses or MESES_RESERVA} meses das suas contas fixas, {como}'
    else:
        prazo = f" até {_dmy(corpo.data_alvo)}" if corpo.data_alvo else ""
        o = f'a meta "{corpo.nome}": juntar {mc._brl(Decimal(corpo.alvo))}{prazo}, {como}'
    return f'✅ Criei {o}. Ela aparece em {_PAINEL}. Se não era isso, me diga "apaga a meta {corpo.nome}".'


def _confirmar_criar(user_id: int, args: dict[str, Any]) -> bool:
    """`confirmar_se` do runner: só GRAVA DIRETO quando a mensagem do turno manda criar. Em qualquer outra
    (desejo, hipótese, pergunta, relato, lançamento que cita meta) o modelo pode ter se enganado: o runner arma
    a pendência e pergunta "Só confirmando". Pedido já inválido (valor, nome repetido...) não é perguntado: o
    `execute` responde a frase do erro."""
    from core.services.metas_gate import pede_criar_meta_ja
    from .._context import CURRENT_USER_MESSAGE
    if not metas_do_turno(user_id) or pede_criar_meta_ja(CURRENT_USER_MESSAGE.get(), args):
        return False
    try:
        _prepara_criar(user_id, args)
    except (_Recusa, ValidationError, RequestValidationError):
        return False  # erro de usuário: o `execute` repete a conferência e responde a frase
    except Exception as e:
        if isinstance(e, HTTPException) and isinstance(e.detail, dict) and e.detail.get("error") in _HTTP:
            return False  # idem, só os códigos que o `_guarda` também traduz
        # Falha inesperada (banco, 500/503): NUNCA gravar direto, e NÃO levantar (o tool_call ficaria sem resposta no
        # histórico e a OpenAI devolveria 400 em todo turno seguinte). Arma o "Só confirmando"; o "sim" passa pelo `_guarda`.
        _log_falha("create_meta", user_id, e)
    return True


def _resumo_criar(args: dict[str, Any]) -> str:
    """O que o "Só confirmando" mostra. Nunca levanta (o runner chama fora de qualquer try): arg ilegível vira texto cru."""
    from db.metas import MESES_RESERVA
    nome = " ".join(str(args.get("nome") or "").split())
    try:
        if args.get("tipo") == "reserva":
            return f'a reserva de emergência "{nome}" ({args.get("meses") or MESES_RESERVA} meses de contas fixas)'
        prazo = f", até {_dmy(args['data_alvo'])}" if args.get("data_alvo") else ""
        fonte = f", acompanhando: {args['fonte']}" if args.get("fonte") else ""
        return f'a meta "{nome}": juntar {mc._brl(Decimal(_txt(args["alvo"])))}{prazo}{fonte}'
    except Exception:
        return f'a meta "{nome}": juntar {args.get("alvo")}'


# ─── edit_meta / delete_meta (com confirmação) ───────────────────────────────

def _campos(args: dict[str, Any]) -> dict[str, Any]:
    """O que muda, com a chave do `EdicaoMeta` (`data_alvo: None` limpa o prazo). Valor em texto."""
    c = {}
    for k, de in (("nome", "novo_nome"), ("alvo", "alvo"), ("data_alvo", "data_alvo"), ("meses", "meses")):
        if args.get(de) is not None:
            c[k] = _txt(args[de]) if k == "alvo" else args[de]
    if args.get("sem_prazo") is True:
        if "data_alvo" in c:
            raise _Recusa("🐷 Prazo novo ou sem prazo: não os dois.")
        c["data_alvo"] = None
    return c


def _partes(c: dict[str, Any]) -> str:
    p = []
    if "nome" in c:
        p.append(f'nome para "{c["nome"]}"')
    if "alvo" in c:
        p.append(f"alvo para {mc._brl(Decimal(c['alvo']))}")
    if "data_alvo" in c:
        p.append("sem prazo" if c["data_alvo"] is None else f"prazo para {_dmy(c['data_alvo'])}")
    if "meses" in c:
        p.append(f"reserva para {c['meses']} meses de contas fixas")
    return "; ".join(p)


def _prepara(user_id: int, args: dict[str, Any]):
    """Tudo que o "sim" repete, sem escrever: acha a meta, monta e confere o pedido."""
    from api.v2.metas import EdicaoMeta
    todas = mc.ler(user_id)["metas"]
    meta = _acha(todas, args.get("nome"))
    campos = _campos(args)
    _sem_controle(campos.get("nome"))
    if not campos:
        raise _Recusa("🐷 Me diz o que mudar: nome, valor, prazo ou meses.")
    if "alvo" in campos and meta["tipo"] == "reserva":
        raise _Recusa("🐷 O valor da reserva vem das suas contas fixas. Dá pra mudar os meses.")
    if "meses" in campos and meta["tipo"] == "meta":
        raise _Recusa("🐷 Meses é só da reserva de emergência. Na meta, mude o valor ou o prazo.")
    corpo = EdicaoMeta(id=meta["id"], **campos)
    if corpo.nome is not None and _iguais([m for m in todas if m["id"] != meta["id"]], corpo.nome):
        raise _Recusa(f'🐷 Você já tem outra meta chamada "{corpo.nome}". Use outro nome.')
    novo = _confere(corpo)  # valor > 0, meses 1–24 e data legível: o que a rota diria só depois do "sim"
    if novo and corpo.data_alvo != meta["data_alvo"] and novo < today_tz():
        raise _Recusa("🐷 O prazo não pode estar no passado.")
    return corpo, meta, campos


@_guarda("edit_meta", "mudar a meta")
def _valida_editar(user_id: int, args: dict[str, Any]) -> None:
    _prepara(user_id, args)


@_guarda("edit_meta", "mudar a meta")
def _editar(user_id: int, args: dict[str, Any]) -> str:
    from api.v2.metas import editar
    corpo, meta, campos = _prepara(user_id, args)  # o mundo muda em até 10 min: repete tudo
    editar(corpo, uid=user_id)
    return f'✅ Meta "{meta["nome"]}" atualizada: {_partes(campos)}.'


def _resumo_editar(args: dict[str, Any]) -> str:
    return f'mudar a meta "{args.get("nome")}": {_partes(_campos(args))}'


@_guarda("delete_meta", "apagar a meta")
def _valida_apagar(user_id: int, args: dict[str, Any]) -> None:
    _acha(mc.ler(user_id)["metas"], args.get("nome"))


@_guarda("delete_meta", "apagar a meta")
def _apagar(user_id: int, args: dict[str, Any]) -> str:
    from api.v2.metas import MetaId, apagar
    meta = _acha(mc.ler(user_id)["metas"], args.get("nome"))
    apagar(MetaId(id=meta["id"]), uid=user_id)
    return f'✅ Meta "{meta["nome"]}" apagada. O dinheiro no banco não mudou.'


def _resumo_apagar(args: dict[str, Any]) -> str:
    return f'apagar a meta "{args.get("nome")}" (o dinheiro no banco não muda)'


def _schema(nome: str, descricao: str, props: dict[str, Any], obrigatorios: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": nome, "description": descricao,
            "parameters": {"type": "object", "properties": props, "required": obrigatorios}}}


_NOME = {"type": "string", "description": "Nome da meta."}
_ALVO = {"type": "number", "minimum": 0.01, "description": "Valor em reais, como o usuário disse."}
_DATA = {"type": "string", "description": "Prazo AAAA-MM-DD. Prazo em mês = último dia do mês."}
_MESES = {"type": "integer", "minimum": 1, "maximum": 24, "description": "Meses de contas fixas da reserva."}

TOOLS: list[Tool] = [
    Tool(
        schema=_schema(
            "create_meta",
            "Cria uma META (objetivo sobre o dinheiro do banco; NÃO é caixinha) ou a RESERVA DE EMERGÊNCIA. "
            "GRAVA NA HORA quando o usuário MANDOU criar (cria, faz, registra...); se foi só desejo ('queria "
            "uma meta de 5000'), o sistema pede o 'sim' dele antes (chame a tool, não pergunte em texto). Com o valor dito "
            "pelo usuário. NUNCA para receita, depósito, resgate ou lançamento que só cita uma meta (regras do prompt).",
            {"nome": {**_NOME, "description": "Nome curto da meta (ex: 'Viagem')."},
             "tipo": {"type": "string", "enum": ["meta", "reserva"]},
             "alvo": {**_ALVO, "description": "Só tipo meta: o valor dito pelo usuário. Nunca invente."},
             "data_alvo": _DATA,
             "meses": {**_MESES, "description": "Só tipo reserva (padrão 6)."},
             "fonte": {"type": "string", "description": "Só se o usuário disser de onde vem: 'Tudo investido', o "
                       "nome do banco ou da conta. Se não disse, NÃO envie."}},
            ["nome", "tipo"]),
        is_write=True,
        requires_confirmation=False,
        execute=_criar,
        confirmar_se=_confirmar_criar,
        summary=_resumo_criar,
        # ponytail: o criar DIRETO nunca arma pendência, mas o campo serve a "nunca roda junto de outra
        # escrita" (a rodada com 2+ escritas vira `_UM_POR_VEZ`; sem pendência, o runner rodaria as duas e
        # mostraria só a primeira mensagem). O nome não descreve o uso; se aparecer um 2º caso, renomear o campo.
        arma_pendencia_no_execute=True,
    ),
    Tool(
        schema=_schema(
            "edit_meta",
            "Muda uma meta ou a reserva (nome, valor, prazo, meses). Ação de ESCRITA — pede confirmação. "
            "Fonte e conversão de reserva em meta: só no painel.",
            {"nome": {**_NOME, "description": "Nome ATUAL e exato da meta."},
             "novo_nome": _NOME, "alvo": _ALVO, "data_alvo": _DATA,
             "sem_prazo": {"type": "boolean", "description": "true = tirar o prazo."},
             "meses": {**_MESES, "description": "Só reserva."}},
            ["nome"]),
        is_write=True,
        validate=_valida_editar,
        summary=_resumo_editar,
        execute=_editar,
    ),
    Tool(
        schema=_schema(
            "delete_meta",
            "Apaga uma meta ou a reserva de vez (o dinheiro no banco não muda). Ação de ESCRITA — pede confirmação.",
            {"nome": {**_NOME, "description": "Nome exato da meta."}},
            ["nome"]),
        is_write=True,
        validate=_valida_apagar,
        summary=_resumo_apagar,
        execute=_apagar,
    ),
]
