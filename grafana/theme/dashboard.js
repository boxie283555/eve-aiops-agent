(() => {
  'use strict';
  // Grafana navigation is client-side. Observe navigation-related DOM changes
  // instead of replacing its history methods or running a polling timer.
  let current;
  const syncScope = () => {
    const path = window.location.pathname;
    if (path === current) return;
    current = path;
    const active = /\/(?:d|d-solo)\/eve-aiops-overview(?:-en)?(?:\/|$)/.test(path);
    if (active) document.body.dataset.aiopsDashboard = 'eve-aiops-overview';
    else delete document.body.dataset.aiopsDashboard;
  };
  const start = () => {
    syncScope();
    new MutationObserver(syncScope).observe(document.getElementById('reactRoot') || document.body, {
      childList: true,
      subtree: true,
    });
    window.addEventListener('popstate', syncScope);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
