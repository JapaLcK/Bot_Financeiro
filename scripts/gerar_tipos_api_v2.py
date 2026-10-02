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


def _resposta_200(path: str, op: dict) -> tuple[str, str]:
    """(`"json"` ou `"sse"`, tipo): o do corpo JSON, ou o do `data` de cada evento SSE."""
    content = op["responses"]["200"]["content"]
    if set(content) == {"application/json"}:
        schema = content["application/json"]["schema"]
        if set(schema) != {"$ref"}:
            raise _recusa({path: schema})
        return "json", _tipo(schema)
    # A forma exata que o FastAPI emite para `EventSourceResponse` com item tipado.
    item_schema = content.get("text/event-stream", {}).get("itemSchema", {})
    props = item_schema.get("properties", {})
    data = props.get("data", {})
    if (set(content) == {"text/event-stream"} and set(item_schema) == {"type", "properties", "required"}
            and set(props) == {"data", "event", "id", "retry"}
            and data.get("contentMediaType") == "application/json"
            and set(data.get("contentSchema", {})) == {"$ref"}):
        return "sse", _tipo(data["contentSchema"])
    raise _recusa({path: content})


def _post(path: str, op: dict) -> str:
    """POST só com corpo JSON obrigatório por `$ref` e resposta 200 JSON por `$ref`."""
    corpo = op.get("requestBody", {})
    content = corpo.get("content", {})
    schema = content.get("application/json", {}).get("schema", {})
    if (set(op) - _OPERACAO != {"requestBody"} or set(corpo) != {"content", "required"}
            or corpo["required"] is not True or set(content) != {"application/json"}
            or set(schema) != {"$ref"}):
        raise _recusa({path: op})
    tipo_resposta, resposta = _resposta_200(path, op)
    if tipo_resposta != "json":
        raise _recusa({path: op})
    return f"{{ corpo: {_tipo(schema)}; resposta: {resposta} }}"


def gerar(spec: dict) -> str:
    schemas = spec.get("components", {}).get("schemas", {})
    linhas = [CABECALHO]
    for nome in sorted(schemas):
        if not _IDENT.fullmatch(nome):
            raise _recusa(nome)
        linhas.append(f"export type {nome} = {_tipo(schemas[nome])};\n")
    rotas = {"json": [], "sse": [], "post": []}
    for p in sorted(spec["paths"]):
        item = spec["paths"][p]
        if set(item) == {"get"} and not set(item["get"]) - _OPERACAO:
            tipo_resposta, tipo = _resposta_200(p, item["get"])
        elif set(item) == {"post"}:
            tipo_resposta, tipo = "post", _post(p, item["post"])
        else:
            raise _recusa({p: item})
        rotas[tipo_resposta].append(f"{json.dumps(p)}: {tipo}")
    linhas.append(f"export type RotasGet = {{ {'; '.join(rotas['json'])} }};\n")
    if rotas["sse"]:
        linhas.append(f"export type RotasSSE = {{ {'; '.join(rotas['sse'])} }};\n")
    if rotas["post"]:
        linhas.append(f"export type RotasPost = {{ {'; '.join(rotas['post'])} }};\n")
    return "".join(linhas)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    from api.v2.app import app

    SAIDA.write_text(gerar(app.openapi()), encoding="utf-8")
    print(f"gravado {SAIDA.relative_to(ROOT)}")
