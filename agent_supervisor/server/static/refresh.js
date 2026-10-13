// Reload a running run's page every 15 seconds, unless someone is typing.
setInterval(function () {
  var el = document.activeElement;
  var typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA") && el.value;
  if (!typing && document.visibilityState === "visible") location.reload();
}, 15000);
