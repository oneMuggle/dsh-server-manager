(() => {
  const logout = document.getElementById('dshgw-logout-button');
  const actionIds = [
    'dshgw-upload-button',
    'dshgw-admin-button',
    'dshgw-logout-button',
  ];
  const knownActions = new Map();
  let mountScheduled = false;
  let mountedArea = null;

  const mount = () => {
    mountScheduled = false;
    const settings = document.querySelector('[data-slot="sidebar.settings"]');
    const settingsButton = settings && settings.querySelector('button');
    if (!settings || !settingsButton || !settings.parentElement) return;
    const settingsArea = settings.parentElement;
    if (mountedArea && mountedArea !== settingsArea) {
      mountedArea.classList.remove('dshgw-has-sidebar-actions');
    }
    mountedArea = settingsArea;
    settingsArea.classList.add('dshgw-has-sidebar-actions');

    let actions = document.getElementById('dshgw-sidebar-actions');
    if (!actions) {
      actions = document.createElement('div');
      actions.id = 'dshgw-sidebar-actions';
      actions.setAttribute('aria-label', 'DSM 操作');
    }
    if (actions.parentElement !== settingsArea || actions.nextSibling !== settings) {
      settings.before(actions);
    }

    const settingsLabel = settingsButton.querySelector('span');
    for (const id of actionIds) {
      const action = document.getElementById(id) || knownActions.get(id);
      if (!action) continue;
      knownActions.set(id, action);
      action.className = `${settingsButton.className} dshgw-sidebar-action`.trim();
      const label = action.querySelector('[data-dshgw-label]');
      if (label && settingsLabel) {
        label.className = `${settingsLabel.className} dshgw-sidebar-label`.trim();
      }
      if (action.parentElement !== actions) actions.appendChild(action);
    }
  };

  const scheduleMount = () => {
    if (mountScheduled) return;
    mountScheduled = true;
    queueMicrotask(mount);
  };

  logout.addEventListener('click', async () => {
    logout.disabled = true;
    try {
      const response = await fetch('/api/v1/auth/sessions/current', { method: 'DELETE' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      location.replace('/login');
    } catch (error) {
      logout.disabled = false;
      alert(`退出失败：${error && error.message ? error.message : error}`);
    }
  });

  new MutationObserver(scheduleMount).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  } else {
    mount();
  }
})();
