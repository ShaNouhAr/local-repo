/* Applique le thème avant l'affichage (chargé de façon bloquante dans <head>). */
(function () {
  var p = "auto";
  try { p = localStorage.getItem("lr-theme") || "auto"; } catch (e) { /* stockage indisponible */ }
  if (p === "auto") p = window.matchMedia("(prefers-color-scheme: dark)").matches ? "oled" : "light";
  document.documentElement.setAttribute("data-theme", p);
})();
