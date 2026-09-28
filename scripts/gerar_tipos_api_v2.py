"""Gera `webapp/src/dashboard/lib/api-v2.gen.ts` do contrato da /api/v2 (`app.openapi()`).

Gerador próprio porque o `openapi-typescript` pede typescript@^5 e o webapp usa o TS 7
nativo. Aceita só as construções que o contrato usa; qualquer outra levanta
`ValueError` em vez de virar um tipo errado em silêncio.
`tests/test_api_v2_contrato.py` compara o arquivo commitado com a saída daqui.

Uso: python scripts/gerar_tipos_api_v2.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAIDA = ROOT / "webapp" / "src" / "dashboard" / "lib" / "api-v2.gen.ts"
CABECALHO = ("// GERADO — não edite; rode python scripts/gerar_tipos_api_v2.py; "
             "tests/test_api_v2_contrato.py compara.\n")
_META = {"title", "description", "default"}
_PRIMITIVO = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "null": "null"}
_REF = "#/components/schemas/"
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_OPERACAO = {"summary", "description", "operationId", "tags", "responses"}


def _recusa(o) -> ValueError:
    return ValueError(f"construção não suportada: {json.dumps(o, ensure_ascii=False)}")


def _tipo(s: dict) -> str:
    chaves = set(s) - _META
    t = s.get("type")
    if chaves == {"$ref"} and s["$ref"].startswith(_REF):
        return s["$ref"][len(_REF):]
    if chaves == {"anyOf"}:
        return " | ".join(_tipo(x) for x in s["anyOf"])
    if chaves == {"type", "enum"} and t == "string" and all(isinstance(v, str) for v in s["enum"]):
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in s["enum"])
    if chaves == {"type"} and t in _PRIMITIVO:
        return _PRIMITIVO[t]
    if chaves == {"type", "items"} and t == "array":
        return f"Array<{_tipo(s['items'])}>"
    if t == "object" and chaves in ({"type", "properties"}, {"type", "properties", "required"}):
        req = s.get("required", [])
        if not set(req) <= set(s["properties"]):
            raise _recusa(s)
        campos = "; ".join(
            f"{k if _IDENT.fullmatch(k) else json.dumps(k)}{'' if k in req else '?'}: {_tipo(v)}"
            for k, v in s["properties"].items())
        return "{ " + campos + " }"
    raise _recusa(s)


def _resposta_200(path: str, item: dict) -> str:
    if set(item) != {"get"} or set(item["get"]) - _OPERACAO:
        raise _recusa({path: item})
    schema = item["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    if set(schema) != {"$ref"}:
        raise _recusa({path: schema})
    return _tipo(schema)


def gerar(spec: dict) -> str:
    schemas = spec.get("components", {}).get("schemas", {})
    linhas = [CABECALHO]
    for nome in sorted(schemas):
        if not _IDENT.fullmatch(nome):
            raise _recusa(nome)
        linhas.append(f"export type {nome} = {_tipo(schemas[nome])};\n")
    rotas = "; ".join(f"{json.dumps(p)}: {_resposta_200(p, spec['paths'][p])}" for p in sorted(spec["paths"]))
    linhas.append(f"export type RotasGet = {{ {rotas} }};\n")
    return "".join(linhas)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    from api.v2.app import app

    SAIDA.write_text(gerar(app.openapi()), encoding="utf-8")
    print(f"gravado {SAIDA.relative_to(ROOT)}")
