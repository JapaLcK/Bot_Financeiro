import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from './agent_chat_fixture.mjs';

const investments = [
  { name: 'CDB Nu Financeira', institution_name: 'Nubank', subtype: 'CDB', type: 'FIXED_INCOME', currency: 'BRL', balance: 1100.55 },
  { name: 'Tesouro IPCA+', institution_name: 'Corretora', type: 'FIXED_INCOME', currency: 'BRL', balance: 800 },
  { name: 'XPML11', institution_name: 'Nubank', type: 'EQUITY', currency: 'BRL', balance: 400 },
  { name: 'USD', institution_name: 'Corretora', type: 'EQUITY', currency: 'USD', balance: 5000 },
  { name: '<img src=x onerror=alert(1)>', institution_name: 'Nubank', type: 'EQUITY', currency: 'BRL', balance: 100 },
];

for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
  test(`carteira React abre grupos e mantém o chat navegável em ${viewport.width}px`, async () => {
    const { page, errors, piggyRequests } = await setup({ openAgent: false, viewport, investments });
    try {
      await page.click('#piggy-fab');
      await page.fill('#piggy-input', 'Quanto tenho nas caixinhas?');
      await page.click('#piggy-send');
      await page.locator('.pc-portfolio').waitFor();
      assert.match(await page.locator('.pc-portfolio').textContent(), /R\$\s*2\.400,55/);
      assert.match(await page.locator('.pc-portfolio').textContent(), /4 ativos/);
      assert.equal(await page.locator('.pc-portfolio-row:visible').count(), 0);
      await page.locator('.pc-portfolio-group').first().locator('summary').click();
      assert.equal(await page.locator('.pc-portfolio-row:visible').count(), 2);
      assert.equal(await page.locator('.pc-portfolio img[onerror]').count(), 0);
      await page.getByText('Como ler estes dados', { exact: true }).click();
      assert.match(await page.locator('.pc-portfolio-help').textContent(), /não movimenta seu dinheiro/);
      assert.match(await page.locator('.pc-portfolio-help').textContent(), /1 a 3 dias/);
      assert.match(await page.locator('.pc-portfolio-help').textContent(), /Nu Financeira/);
      assert.equal(await page.locator('#piggy-body').evaluate(el => el.scrollWidth <= el.clientWidth), true);
      await page.getByRole('button', { name: 'Quais são meus maiores CDBs?' }).click();
      await page.getByText(/Seus maiores CDBs compartilhados/).waitFor();
      assert.match(await page.locator('#piggy-body').textContent(), /R\$\s*1\.100,55/);
      assert.equal(await page.locator('.pc-portfolio').count(), 1);
      assert.equal(piggyRequests.length, 1, 'o atalho deve responder com o snapshot, sem consultar a carteira manual');
      assert.deepEqual(errors, []);
    } finally { await page.close(); }
  });
}

test('bloqueia perguntas da carteira enquanto a resposta principal está pendente', async () => {
  const { page, releasePiggy, piggyRequests } = await setup({ openAgent: false, investments, holdPiggy: true });
  try {
    await page.click('#piggy-fab');
    await page.fill('#piggy-input', 'Quanto tenho nas caixinhas?');
    await page.click('#piggy-send');
    await page.locator('.pc-portfolio').waitFor();
    const followup = page.getByRole('button', { name: 'Quais são meus maiores CDBs?' });
    assert.equal(await followup.isDisabled(), true, 'a pergunta não pode aceitar um clique que será descartado');
    releasePiggy();
    await page.waitForFunction(() => !document.getElementById('piggy-input').disabled);
    assert.equal(await followup.isEnabled(), true, 'a pergunta deve ser liberada quando o envio termina');
    await followup.click();
    await page.getByText(/Seus maiores CDBs compartilhados/).waitFor();
    assert.equal(await page.locator('.pc-portfolio').count(), 1);
    assert.equal(piggyRequests.length, 1);
  } finally { await page.close(); }
});

for (const question of ['Meus investimentos', 'Meus investimentos do Open Finance']) {
  test(`reconhece pedido direto de carteira: ${question}`, async () => {
    const { page, errors } = await setup({ openAgent: false, investments });
    try {
      await page.click('#piggy-fab');
      await page.fill('#piggy-input', question);
      await page.click('#piggy-send');
      await page.locator('.pc-portfolio').waitFor();
      assert.match(await page.locator('.pc-portfolio').textContent(), /R\$\s*2\.400,55/);
      assert.deepEqual(errors, []);
    } finally { await page.close(); }
  });
}
