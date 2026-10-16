/* Le serveur valide aussi les droits et ajoute leurs prérequis ; ce formulaire reste utilisable sans JS. */
(() => {
  const form = document.querySelector('[data-permission-form]');
  if (!form) return;
  const boxes = new Map([...form.querySelectorAll('[data-permission]')].map(box => [box.value, box]));
  const required = box => JSON.parse(box.dataset.requires);
  function select(box) {
    if (!box || box.disabled) return;
    box.checked = true;
    required(box).forEach(key => select(boxes.get(key)));
  }
  form.addEventListener('change', event => {
    const box = event.target;
    if (!box.matches('[data-permission]')) return;
    if (box.checked) select(box);
    else {
      let changed;
      do {
        changed = false;
        for (const candidate of boxes.values()) {
          if (candidate.checked && !candidate.disabled && required(candidate).some(key => !boxes.get(key)?.checked)) {
            candidate.checked = false; changed = true;
          }
        }
      } while (changed);
    }
  });
})();
