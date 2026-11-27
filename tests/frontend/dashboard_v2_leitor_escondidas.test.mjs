/**
 * Leitor (credencial de visualização) no /painel: cada coisa que o leitor NÃO vê, uma a uma, com o dono no mesmo cenário como controle
 * POSITIVO (se o cenário não mostrasse o elemento nem para o dono, o "não vê" não mediria nada). Cobre o que o varrido de botões de
 * dashboard_v2_leitor.test.mjs não alcança: o que depende de dado (renda informada, banco velho, dica da tela), do ⌘K, do clique na
 * linha do extrato e da Ajuda. NEGATIVOS (medidos, cada um só no seu caso): tirar o `edita` de OrcamentoDomestico.tsx ("Usar a calculada");
 * o ramo `leitor ?` do corpo em AvisosDoTopo.tsx; o `!leitor` do "guia" e o do "dica" em Command.tsx; o `leitor ? undefined` de Lancamentos.tsx;
 * o `!leitor &&` do <Feedback /> em AjudaTela.tsx.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { abrirPainel, exigeArtefatoEmDia } from "./_painel.mjs";
import { abrirBanco, AVISO } from "./_atualizar_banco.mjs";
import { rotasDoLeitor } from "./_leitor.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abrir = async (leitor, rota, opts = {}) => {
  const p = await abrirPainel(browser, { espera: null, ...opts });
  if (leitor) await rotasDoLeitor(p.ctx);
  await p.ir(rota);
  return p;
};
/** Um caso = o que se observa na página (`ver`) e o que cada um deve ver: o leitor e, de controle, o dono. */
const caso = (nome, opcoes, ver, { leitor, dono }) => {
  for (const [quem, ehLeitor, esperado] of [["leitor", true, leitor], ["controle positivo: dono", false, dono]]) {
    test(`${quem}: ${nome}`, async () => {
      const p = await (opcoes.abrir ?? abrir)(ehLeitor, opcoes.rota, opcoes.opts);
      try { assert.deepEqual(await ver(p.page), esperado); assert.deepEqual(p.erros, []); } finally { await p.ctx.close(); }
    });
  }
};
const n = (page, sel) => page.locator(sel).count();
const botao = (page, nome) => page.getByRole("button", { name: nome, exact: true }).count();

caso("orçamento com renda informada: a origem aparece, 'Usar a calculada' e os editores não", { rota: "/orcamento", opts: { orcamento: "informada" } }, async (page) => {
  await page.locator(".orc-tabela tbody tr").first().waitFor({ state: "attached" });
  return { origem: /Informada por você/.test(await page.locator(".page-grid").innerText()), usar: await botao(page, "Usar a calculada"), editarRenda: await n(page, ".orc-editar"), percentuais: await n(page, "button:has-text('Editar percentuais')") };
}, { leitor: { origem: true, usar: 0, editarRenda: 0, percentuais: 0 }, dono: { origem: true, usar: 1, editarRenda: 1, percentuais: 1 } });

caso("banco velho: o aviso aparece, sem 'Atualizar agora', sem botão e sem o link para Bancos (tela fechada)", {
  abrir: async (leitor) => {
    const p = await abrirBanco(browser, {});
    if (leitor) await rotasDoLeitor(p.ctx);
    await p.abrir(!leitor);
    return p;
  },
}, async (page) => ({ aviso: await n(page, AVISO), texto: /estão desatualizados/.test(await page.locator(`${AVISO} .pilha-corpo`).innerText()), atualizar: await botao(page, "Atualizar agora"), botoes: await n(page, `${AVISO} .pilha-corpo button`), links: await n(page, `${AVISO} .pilha-corpo a`) }),
{ leitor: { aviso: 1, texto: true, atualizar: 0, botoes: 0, links: 0 }, dono: { aviso: 1, texto: false, atualizar: 1, botoes: 1, links: 0 } });

caso("⌘K: sem 'Mostrar o guia do painel' e sem 'Como funciona esta tela'", { rota: "/assinaturas", opts: { guia: "oferecer" } }, async (page) => {
  await page.locator("#page-title").waitFor();
  await page.waitForTimeout(800); // a dica da tela vem do GET /guia
  await page.keyboard.press("Meta+k");
  await page.locator(".cmdk input").waitFor();
  const t = await page.locator(".cmdk").innerText();
  return { guia: t.includes("Mostrar o guia do painel"), dica: t.includes("Como funciona esta tela") };
}, { leitor: { guia: false, dica: false }, dono: { guia: true, dica: true } });

caso("extrato: a linha não vira botão e o clique não abre o formulário de edição", { rota: "/lancamentos", opts: { lancamentos: "estados" } }, async (page) => {
  const linha = page.locator('[data-id="l901"]');
  await linha.waitFor();
  const botoes = await linha.locator("button").count();
  await linha.locator(".lanc-abrir").click();
  await page.waitForTimeout(300);
  return { botoes, formulario: await n(page, ".lanc-form[open]") };
}, { leitor: { botoes: 0, formulario: 0 }, dono: { botoes: 1, formulario: 1 } });

caso("Ajuda: sem o formulário de Feedback; o contato segue", { rota: "/ajuda" }, async (page) => {
  await page.locator(".ajuda-contato").waitFor();
  return { feedback: await n(page, ".ajuda-feedback"), enviar: await botao(page, "Enviar feedback") };
}, { leitor: { feedback: 0, enviar: 0 }, dono: { feedback: 1, enviar: 1 } });
