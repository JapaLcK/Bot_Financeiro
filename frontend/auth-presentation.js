/* Controles visuais: a autenticação continua nos formulários oficiais. */
(function () {
  document.querySelectorAll('[data-password]').forEach(function (button) {
    button.addEventListener('click', function () {
      const input = document.getElementById(button.dataset.password);
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(visible));
      button.setAttribute('aria-label', visible ? 'Ocultar senha' : 'Mostrar senha');
      button.querySelector('i').className = visible ? 'ph ph-eye-slash' : 'ph ph-eye';
    });
  });
})();
