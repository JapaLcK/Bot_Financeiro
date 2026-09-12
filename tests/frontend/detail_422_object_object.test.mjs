/**
 * O 422 do FastAPI virava "[object Object]" na cara do usuário.
 *
 * `HTTPException(400, detail="frase")` manda `detail` STRING; o 422 de
 * validação manda uma LISTA de objetos — MEDIDO em `POST /auth/login` sem o
 * campo `password`, no `origin/main` deste PR:
 *
 *   422 {"detail":[{"type":"missing","loc":["body","password"],
 *                  "msg":"Field required","input":{"email":"a@x.com"}}]}
 *
 * A lista é truthy, então `d.detail || 'E-mail ou senha incorretos.'` escolhia
 * a LISTA, e `e.textContent = <Array>` a renderiza como "[object Object]".
 * Seis páginas públicas faziam isso; o conserto é o mesmo em todas — só string
 * passa, o resto cai no fallback que a própria chamada já tinha escrito.
 *
 * E o que NÃO pode acontecer: o `loc`/`msg`/`input` chegar à tela. Eles trazem
 * nome de campo interno (`password`), o tipo do erro (`missing`) e o VALOR
 * recebido — o mesmo motivo pelo qual `tests/test_error_pages.py:718` proíbe
 * isso na página de erro. Por isso cada caso mede as três coisas: não tem
 * "[object Object]", tem a frase de fallback, e não tem nada do corpo do 422.
 *
 * Os dois controles do CLAUDE.md §3, no GRUPO:
 *
 *   · negativo — desfaça o conserto e o grupo fica VERMELHO. Em `login.html` e
 *     `cadastro.html`, tire o `if(d&&typeof d==='object'&&...)d.detail=null` do
 *     `payload()`; nas outras quatro, troque `strDetail(data)` de volta por
 *     `data.detail`. MEDIDO: com o conserto desfeito nos 6 sites, os 5 casos
 *     "422" ficam vermelhos com `texto: "[object Object]"` e os 5 "400 string"
 *     continuam VERDES — ou seja, a injeção discrimina, e discrimina em caso
 *     que estava verde (§3);
 *   · positivo — os casos "400 string" provam que a frase que o SERVIDOR
 *     escreve continua chegando inteira à tela. Sem eles, um conserto que
 *     jogasse TODO `detail` fora (e mostrasse sempre o genérico) passaria —
 *     e isso é pior que o bug, porque apaga "E-mail já cadastrado."
 *
 * Desktop (1440×900) e mobile (390×844) no mesmo grupo: a caixa de erro é
 * `.auth-error`/`.contact-msg`, que muda de largura entre os dois, e o teste
 * confere que o texto está VISÍVEL (não só no DOM) nas duas larguras.
 *
 * O que este arquivo NÃO alcança: `home.html` (o 6º site — precisa do bootstrap
 * autenticado do dashboard, fora do alcance de um servidor de arquivos), o
 * servidor de verdade (aqui o 422 é `page.route`) e `dashboard.js`/
 * `admin-dashboard.html`, que têm a MESMA classe de bug e ficaram fora deste PR
 * de propósito — ver o relato.
 *
 * Rodar:  node --test tests/frontend/detail_422_object_object.test.mjs
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

/** O corpo EXATO que o FastAPI devolve — medido, não inventado. */
const CORPO_422 = JSON.stringify({
  detail: [{ type: "missing", loc: ["body", "password"],
             msg: "Field required", input: { email: "a@x.com" } }],
});
/** Fragmentos do 422 que não podem aparecer na tela (nome de campo interno,
 *  tipo do erro e o valor recebido). */
const VAZAMENTOS = ["[object Object]", "Field required", "missing", "password"];

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };

/**
 * Cada página: como abrir, o que mockar, como disparar o envio, e onde a
 * mensagem aparece. `fallback` é a frase que a própria chamada já escrevia.
 */
const PAGINAS = [
  {
    nome: "login",
    url: "/login.html",
    rota: "**/auth/login",
    fallback: "E-mail ou senha incorretos.",
    frase: "Conta bloqueada por tentativas demais.",
    caixa: "#login-error",
    async antes(page) {
      // O IIFE do topo pula o login se a sessão estiver viva — 401 nos dois.
      for (const r of ["**/auth/validate", "**/auth/refresh"]) {
        await page.route(r, (x) => x.fulfill({ status: 401, contentType: "application/json", body: "{}" }));
      }
    },
    async enviar(page) {
      await page.fill("#email", "a@x.com");
      await page.fill("#senha", "12345678");
      await page.click("#btn-login");
    },
  },
  {
    nome: "cadastro",
    url: "/cadastro.html",
    rota: "**/auth/register",
    fallback: "Erro ao enviar código.",
    frase: "Esse e-mail já tem conta.",
    caixa: "#reg-error",
    async enviar(page) {
      await page.fill("#reg-name", "Ana");
      await page.fill("#reg-email", "a@x.com");
      await page.fill("#reg-phone", "11999999999");
      await page.fill("#reg-password", "12345678");
      await page.fill("#reg-confirm", "12345678");
      await page.check("#terms");
      await page.click("#btn-register");
    },
  },
  {
    nome: "suporte",
    url: "/suporte.html",
    rota: "**/contact",
    fallback: "Não foi possível enviar. Escreva pra contato@pigbankai.com.",
    frase: "Assunto muito longo.",
    caixa: "#contact-msg",
    async enviar(page) {
      await page.fill("#nome", "Ana");
      await page.fill("#email", "a@x.com");
      await page.fill("#assunto", "Dúvida");
      await page.fill("#msg", "Oi");
      await page.click("#contact-btn");
    },
  },
  {
    nome: "reset-password",
    url: "/reset-password.html#token=abc123",
    rota: "**/auth/reset-password",
    fallback: "Erro ao salvar a senha.",
    frase: "Esse link já foi usado.",
    caixa: "#msg-error",
    async enviar(page) {
      await page.fill("#password", "12345678");
      await page.fill("#password2", "12345678");
      await page.click("#submit-btn");
    },
  },
  {
    nome: "completar-cadastro",
    url: "/completar-cadastro.html?token=abc123",
    rota: "**/auth/google/pending/**",
    fallback: "Cadastro expirado. Faça login com Google novamente.",
    frase: "Esse convite expirou ontem.",
    caixa: "#msg-error",
    // O `loadPending()` dispara sozinho no fim do script — nada a clicar.
    async enviar() {},
  },
];

/** Abre a página com a rota mockada, dispara o envio e devolve o que a tela
 *  mostra. `texto` é `innerText` da caixa; `visivel` é o estilo COMPUTADO. */
async function tela(p, { status, body, viewport }) {
  const page = await browser.newPage({ viewport });
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));
  if (p.antes) await p.antes(page);
  await page.route(p.rota, (r) =>
    r.fulfill({ status, contentType: "application/json", body }));
  await page.goto(ORIGIN + p.url);
  await p.enviar(page);
  await page.waitForTimeout(400);
  const visto = await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { texto: "(sem caixa)", visivel: false };
    const cs = getComputedStyle(el);
    return {
      texto: el.innerText,
      visivel: cs.display !== "none" && cs.visibility !== "hidden" && Number(cs.opacity) > 0,
    };
  }, p.caixa);
  await page.close();
  return { ...visto, erros };
}

for (const p of PAGINAS) {
  for (const [rotulo, viewport] of [["desktop", DESKTOP], ["mobile", MOBILE]]) {
    test(`${p.nome} (${rotulo}): 422 não vira [object Object] nem vaza o corpo`, async () => {
      const t = await tela(p, { status: 422, body: CORPO_422, viewport });
      assert.deepEqual(t.erros, [], "a página estourou JS");
      for (const proibida of VAZAMENTOS) {
        assert.ok(!t.texto.includes(proibida),
          `a tela mostrou "${proibida}" — texto: ${JSON.stringify(t.texto)}`);
      }
      assert.equal(t.texto.trim(), p.fallback);
      assert.ok(t.visivel, "a caixa de erro ficou invisível");
    });

    // POSITIVO: sem isto, um conserto que descartasse TODO detail passaria.
    test(`${p.nome} (${rotulo}): a frase do servidor (400 string) continua inteira`, async () => {
      const t = await tela(p, {
        status: 400, body: JSON.stringify({ detail: p.frase }), viewport });
      assert.deepEqual(t.erros, [], "a página estourou JS");
      assert.equal(t.texto.trim(), p.frase);
      assert.ok(t.visivel, "a caixa de erro ficou invisível");
    });
  }
}
