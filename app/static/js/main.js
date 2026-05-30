document.addEventListener("submit", function (event) {
  const form = event.target;
  const message = form.getAttribute("data-confirm");
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

document.addEventListener("click", function (event) {
  const trigger = event.target.closest("[data-confirm-click]");
  if (trigger && !window.confirm(trigger.getAttribute("data-confirm-click"))) {
    event.preventDefault();
  }
});
