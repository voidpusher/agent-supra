const scene = document.querySelector('.showcase');
const demo = document.querySelector('.mode-demo');
if (scene && demo) {
  demo.hidden = false;
  const state = scene.querySelector('[data-demo-state]');
  const description = scene.querySelector('[data-demo-description]');
  const buttons = [...scene.querySelectorAll('[data-mode]')];
  buttons.forEach(button => button.addEventListener('click', () => {
    const observing = button.dataset.mode === 'observe';
    scene.classList.toggle('is-observing', observing);
    buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    state.textContent = observing ? 'Flagged' : 'Paused';
    description.textContent = observing
      ? 'The signal is recorded. Your agent continues running.'
      : 'Further supervised steps wait for you to decide.';
  }));
  state.parentElement.setAttribute('aria-live', 'polite');
}
if ('IntersectionObserver' in window) {
  const reveal = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-revealed');
        reveal.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  document.querySelectorAll('.landing .band').forEach(section => reveal.observe(section));
}
