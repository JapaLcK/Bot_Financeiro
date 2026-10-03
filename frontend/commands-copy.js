/* Copiar um exemplo nunca envia mensagem nem altera registros. */
(function () {
  let timer;
  document.querySelectorAll('[data-copy]').forEach(function (button) {
    button.addEventListener('click', async function () {
      const example = document.getElementById(button.dataset.copy);
      const notice = document.getElementById('copy-notice');
      clearTimeout(timer);
      try {
        await navigator.clipboard.writeText(example.textContent.trim());
        notice.textContent = 'Exemplo copiado. Adapte os dados antes de colar no WhatsApp.';
      } catch (_) {
        const range = document.createRange(); range.selectNodeContents(example);
        const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
        notice.textContent = 'Não foi possível copiar automaticamente. O exemplo está selecionado para você copiar.';
      }
      notice.hidden = false;
      timer = setTimeout(function () { notice.hidden = true; }, 6500);
    });
  });
})();
