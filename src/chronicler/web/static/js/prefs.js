// Theme and font toggles, remembered per browser. Safe if storage is unavailable.
(function () {
  "use strict";
  var root = document.documentElement;
  var cycles = { theme: ["dark", "light"], font: ["default", "dyslexic", "atkinson"] };

  function save(key, value) {
    try { localStorage.setItem("chronicler-" + key, value); } catch (e) { /* ignore */ }
  }

  document.querySelectorAll("button.pref").forEach(function (button) {
    button.addEventListener("click", function () {
      var key = button.dataset.pref;
      var options = cycles[key];
      var next = options[(options.indexOf(root.dataset[key]) + 1) % options.length];
      root.dataset[key] = next;
      save(key, next);
    });
  });

  // Close the live search dropdown on Escape or when the box is cleared.
  var input = document.querySelector(".search-form input");
  var results = document.getElementById("live-results");
  if (input && results) {
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { results.innerHTML = ""; }
    });
    input.addEventListener("input", function () {
      if (!input.value.trim()) { results.innerHTML = ""; }
    });
    document.addEventListener("click", function (event) {
      if (!event.target.closest(".search-form")) { results.innerHTML = ""; }
    });
  }
})();
