document.addEventListener("submit", function (e) {
  const form = e.target;
  if (form.dataset.confirm) {
    if (!window.confirm(form.dataset.confirm)) {
      e.preventDefault();
    }
  }
});
