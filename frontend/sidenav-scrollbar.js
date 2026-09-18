(() => {
  const nav = document.getElementById("sidenav");
  if (!nav) return;

  let idleTimer;
  nav.addEventListener("scroll", () => {
    nav.classList.add("is-scrolling");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => nav.classList.remove("is-scrolling"), 700);
  }, { passive: true });
})();
