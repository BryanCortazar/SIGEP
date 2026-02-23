document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-toggle-password]");
  if (!btn) return;

  const selector = btn.getAttribute("data-toggle-password");
  const input = document.querySelector(selector);
  if (!input) return;

  input.type = input.type === "password" ? "text" : "password";
});
