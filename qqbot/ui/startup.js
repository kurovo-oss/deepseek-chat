(function () {
  const overlay = document.getElementById('startup');
  if (!overlay) return;

  const params = new URLSearchParams(window.location.search);
  const reduceMotion = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let finished = false;
  let timer = 0;

  function finish() {
    if (finished) return;
    finished = true;
    window.clearTimeout(timer);
    document.removeEventListener('keydown', onKey);
    overlay.setAttribute('aria-hidden', 'true');
    overlay.classList.add('is-leaving');
    document.body.classList.remove('startup-running');
    window.setTimeout(function () {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }, 520);
  }

  function onKey(event) {
    if (event.key === 'Escape' || event.key === ' ' || event.key === 'Enter') {
      event.preventDefault();
      finish();
    }
  }

  // 减少动态效果、以及外壳 --screenshot 取图时不播放，避免遮罩挡住页面。
  if (reduceMotion || params.get('screenshot') === '1') {
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    return;
  }

  document.body.classList.add('startup-running');
  const skip = document.getElementById('startup-skip');
  if (skip) {
    skip.addEventListener('click', function (event) {
      event.stopPropagation();
      finish();
    });
  }
  overlay.addEventListener('click', finish);
  document.addEventListener('keydown', onKey);
  timer = window.setTimeout(finish, 5100);
})();
