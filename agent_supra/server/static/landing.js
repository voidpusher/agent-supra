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
      ? 'Evidence recorded. Your agent keeps running.'
      : 'The next step waits for your decision.';
  }));
  state.parentElement.setAttribute('aria-live', 'polite');
}
const video = document.querySelector('.hero-video');
const motionToggle = document.querySelector('.motion-toggle');
if (video && motionToggle) {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const saveData = Boolean(navigator.connection?.saveData);
  let manuallyPaused = false;
  let visible = true;
  const syncPlayback = () => {
    if (reducedMotion.matches || saveData || manuallyPaused || !visible || document.hidden) {
      video.pause();
      motionToggle.hidden = reducedMotion.matches || saveData || !video.currentSrc;
      return;
    }
    if (!video.getAttribute('src')) video.src = video.dataset.src;
    video.play().catch(() => { motionToggle.hidden = true; });
  };
  video.addEventListener('playing', () => { motionToggle.hidden = false; });
  video.addEventListener('error', () => { motionToggle.hidden = true; });
  motionToggle.addEventListener('click', () => {
    manuallyPaused = !manuallyPaused;
    motionToggle.textContent = manuallyPaused ? 'Play motion' : 'Pause motion';
    motionToggle.setAttribute('aria-pressed', String(manuallyPaused));
    syncPlayback();
  });
  reducedMotion.addEventListener('change', syncPlayback);
  document.addEventListener('visibilitychange', syncPlayback);
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      syncPlayback();
    }).observe(scene);
  } else {
    syncPlayback();
  }
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
