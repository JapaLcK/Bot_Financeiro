"""Apresentação v2 sobre uma snapshot: nenhuma escrita ou regra financeira nova."""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json

from core.services import cashflow_snapshot
from core.services.cashflow import _projection
from core.services.cashflow_contract import Ocorrencia, Snapshot
from core.services.cashflow_forecast import _horizons, _trajectory
from db.connection import get_conn
from utils_date import now_tz


def _chave(uid: int, namespace: str, *identidade) -> str:
    return sha256(json.dumps([uid, namespace, *identidade], ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def _motivos(motivos) -> list[dict]:
    pares = dict.fromkeys((m["codigo"], m["direcao_do_erro"]) for m in motivos)
    return [{"codigo": codigo, "direcao_do_erro": direcao} for codigo, direcao in pares]


def _ocorrencia(uid: int, e: Ocorrencia) -> dict:
    return {"chave": _chave(uid, "ocorrencia", e.fonte, e.origem_id, e.ciclo),
            "ciclo": e.ciclo, "data": e.data.isoformat() if e.data else None,
            "fonte": e.fonte, "tipo": e.tipo, "nome": e.nome, "valor": e.valor,
            "direcao": e.direcao, "qualidade_valor": e.qualidade_valor,
            "qualidade_data": e.qualidade_data, "realizacao": e.realizacao,
            "incluida_no_calculo": e.incluida,
            "motivos": _motivos([{"codigo": m.codigo, "direcao_do_erro": m.direcao_do_erro}
                                 for m in e.motivos])}


def apresentar(snapshot: Snapshot, uid: int, dias: int,
               permitidos: tuple[int, ...], detalhes: bool) -> dict:
    hoje = snapshot.hoje
    base = snapshot.base
    horizontes = tuple(n for n in permitidos if n <= dias)
    marcos = _horizons(hoje, base, snapshot.ocorrencias, horizontes)["horizons"]
    qualidade = snapshot.qualidade()
    dados = {**qualidade, "motivos": _motivos(qualidade["motivos"]),
             "hoje": hoje.isoformat(), "dias": dias, "dias_permitidos": list(permitidos),
             "capacidades": ["marcos"],
             "base": {"saldo": base["saldo"], "origem": base["balance_source"],
                      "contas_bancarias": base["of_bank_count"],
                      "bancos_excluidos": base["banks_excluded"], "motivos": base["motivos"]},
             "marcos": [{"dias": n, "data": marcos[str(n)]["target"],
                         "saldo": marcos[str(n)]["projetado"]} for n in horizontes],
             "periodo": None, "ancora": None, "trajetoria": None,
             "pior_dia": None, "compromissos": None}
    if not detalhes:
        return dados

    dados["capacidades"] += ["trajetoria", "pior_dia", "compromissos"]
    # O motor seleciona/ordena detalhes já arredondados; o valor público vem do
    # objeto original da mesma snapshot, preservando Decimal e escala da fonte.
    originais = {e.chave: _ocorrencia(uid, e) for e in snapshot.ocorrencias}
    percurso = _trajectory(hoje, base, snapshot.ocorrencias, dias, Decimal(0))
    dados["periodo"] = {"inicio": percurso["period"]["start"], "fim": percurso["period"]["end"]}
    dados["ancora"] = {"data": hoje.isoformat(),
                       "saldo": _projection(hoje, base, snapshot.ocorrencias, hoje)["projetado"]}
    dados["trajetoria"] = [{"data": p["date"], "saldo": p["saldo_projetado"],
                           "compromissos": [originais[e["chave"]] for e in p["compromissos"]]}
                          for p in percurso["trajectory"]]
    pior = percurso["worst_day"]
    if pior is not None:
        dados["pior_dia"] = {"data": pior["date"], "saldo": pior["saldo_projetado"],
                             "desde": pior["desde"],
                             "causas": [originais[e["chave"]] for e in pior["causas"]]}
    grupos = {}
    for e in snapshot.ocorrencias:
        if e.realizacao == "realizada":
            continue
        chave = _chave(uid, "grupo", e.fonte, e.origem_id)
        # A snapshot não oferece vínculo de instância ao recorrente; instância
        # permanece em grupo próprio, sem outra consulta ou inferência por nome.
        grupo = grupos.setdefault(chave, {"chave": chave, "fonte": e.fonte, "nome": e.nome,
                                          "primeira_data": None, "ultima_data": None,
                                          "ocorrencias": []})
        grupo["ocorrencias"].append(originais[e.chave])
    for grupo in grupos.values():
        grupo["ocorrencias"].sort(key=lambda e: (e["data"] is None, e["data"] or "", e["chave"]))
        datas = [e["data"] for e in grupo["ocorrencias"] if e["data"] is not None]
        if datas:
            grupo["primeira_data"], grupo["ultima_data"] = min(datas), max(datas)
    dados["compromissos"] = sorted(grupos.values(), key=lambda g:
                                  (g["primeira_data"] is None, g["primeira_data"] or "", g["chave"]))
    return dados


def ler_previsao(uid: int, dias: int, permitidos: tuple[int, ...], detalhes: bool) -> dict:
    agora = now_tz()
    hoje = agora.date()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        snapshot = cashflow_snapshot.ler(cur, uid, hoje, hoje + timedelta(days=dias), agora, True)
        conn.rollback()
    return apresentar(snapshot, uid, dias, permitidos, detalhes)
