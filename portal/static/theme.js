/* Applique le thème avant l'affichage (chargé de façon bloquante dans <head>) : OLED par défaut. */
(function () {
  var p = "oled";
  try { if (localStorage.getItem("lr-theme") === "light") p = "light"; } catch (e) { /* stockage indisponible */ }
  document.documentElement.setAttribute("data-theme", p);
})();
