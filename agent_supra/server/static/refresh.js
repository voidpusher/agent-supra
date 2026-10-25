// Preserve unsaved action reasons and beta feedback while a run is active.
var unsavedRunForm = false;
['input', 'change'].forEach(function (eventName) {
  document.addEventListener(eventName, function (event) {
    if (event.target.closest('form')) unsavedRunForm = true;
  });
});
setInterval(function () {
  var el = document.activeElement;
  var editing = el && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
  if (!unsavedRunForm && !editing && document.visibilityState === "visible") location.reload();
}, 15000);
