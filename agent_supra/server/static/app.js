/* Small progressive enhancements; forms and navigation work without JavaScript. */
const workspaceNav = document.querySelector('.app-sidebar .nav');
const currentLink = workspaceNav?.querySelector('[aria-current="page"]');
if (currentLink && workspaceNav.scrollWidth > workspaceNav.clientWidth) {
  workspaceNav.scrollLeft = currentLink.offsetLeft - workspaceNav.offsetLeft
    - (workspaceNav.clientWidth - currentLink.offsetWidth) / 2;
}

document.querySelectorAll('[data-copy-key]').forEach(button => {
  button.addEventListener('click', async () => {
    const key = document.getElementById('new-key');
    const status = document.getElementById('copy-key-status');
    if (!key || !status) return;
    try {
      await navigator.clipboard.writeText(key.textContent.trim());
      status.textContent = 'Key copied. Store it privately.';
      button.textContent = 'Copied';
    } catch {
      status.textContent = 'Select the key below and copy it manually.';
    }
  });
});
