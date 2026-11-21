// Small progressive-enhancement helpers (no framework).
document.addEventListener("submit", function (event) {
  var form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  var busyLabel = form.getAttribute("data-busy");
  if (!busyLabel) return;
  var button = form.querySelector("button:not([type='button'])");
  if (button && !button.disabled) {
    button.disabled = true;
    button.textContent = busyLabel;
  }
});
