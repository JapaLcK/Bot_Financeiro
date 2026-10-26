// O card `simulador` da IA e o "Atualizar agora" do aviso de banco (webapp: parts/usarAtualizarBanco.ts, lib/eventos.ts `todasMenosFotos`). O fim da rodada recarrega o painel como um aviso do SSE
// (`invalidateQueries`): o card é uma foto (`meta.foto`) e não pode refazer o POST do corte, nem a rodada que começou em `/` e termina em `/piggy` pode custar 2 leituras de perfil por POST.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { RESPOSTAS, encerrar, iniciar } from "./_chat_ia.mjs";
import { ate, prepararSim } from "./_chat_ia_sim.mjs";
import { AVISO, PARCIAL, porta } from "./_atualizar_banco.mjs";
import { servidorBancos } from "./_bancos.mjs";

before(iniciar);
after(encerrar);

test("S21 a rodada do \"Atualizar agora\" que começa em `/` e termina em `/piggy` NÃO refaz o POST do card (foto); o controle positivo é o /me, que o mesmo recarregamento relê", async () => {
  const c = await prepararSim({ uid: 7, bancos: "pilha_desatualizado" });
  const portao = porta();
  const srv = servidorBancos({ conexoes: [PARCIAL()], bancosRes: (r) => r.fulfill({ json: RESPOSTAS.bancos.pilha_desatualizado }),
    refresh: async (r) => { await portao.p; return r.fulfill({ json: { ok: true, sync: { ok: true, items: [], still_updating: 0 }, connections: [] } }); } });
  await srv.antes(c.ctx); // depois da rota do teste: o perfil dele (user_id 7) é o mesmo da conversa
  await c.page.evaluate(() => { location.hash = "#/"; });
  await c.page.locator(AVISO).waitFor();
  await c.page.getByRole("button", { name: "Atualizar agora" }).click();
  await ate(() => srv.posts.length === 1); // o POST do banco está preso: a rodada segue em voo
  await c.page.evaluate(() => { location.hash = "#/piggy"; });
  await c.page.locator(".card-ia").waitFor();
  await c.soltar("Mercado", 30);
  await c.card.locator(".sim-facts").waitFor();
  const posts = c.t.corpos.length, me = c.t.me;
  portao.soltar(); // a rodada termina com o card na tela
  await ate(() => c.t.me > me); // positivo: o recarregamento invalidou o resto
  await c.page.waitForTimeout(1000);
  assert.equal(c.t.corpos.length, posts, "nenhum POST novo do corte");
  assert.match(await c.card.locator(".sim-facts").innerText(), /R\$ 54,00/, "a foto fica");
  await c.page.context().close();
});
