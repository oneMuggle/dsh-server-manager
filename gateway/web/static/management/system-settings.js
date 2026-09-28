(() => {
  const form = document.getElementById('pip-index-form');
  const urlInput = document.getElementById('pip-index-url');
  const saveButton = form.querySelector('button[type="submit"]');
  const resetButton = document.getElementById('pip-index-reset');
  const status = document.getElementById('pip-index-status');

  const sourceLabels = {
    configured: '已配置',
    default: '系统默认',
  };

  async function api(url, options) {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
    return body;
  }

  function showStatus(message, isError = false) {
    status.textContent = message;
    status.className = isError ? 'settings-status error' : 'settings-status success';
    if (!isError) {
      setTimeout(() => {
        status.className = 'muted';
      }, 3000);
    }
  }

  async function loadConfiguration() {
    try {
      const configuration = await api('/api/v1/system/settings/pip-index');
      urlInput.value = configuration.pipIndexUrl || '';
      resetButton.disabled = configuration.source !== 'configured';
      const source = sourceLabels[configuration.source] || configuration.source;
      const prefix = configuration.pipIndexUrl ? '' : '未配置；';
      status.textContent = `${prefix}当前生效：${configuration.pipIndexUrl || configuration.defaultPipIndexUrl}；来源：${source}；默认：${configuration.defaultPipIndexUrl}`;
      status.className = 'muted';
    } catch (error) {
      status.textContent = `配置加载失败：${error.message}`;
      status.className = 'settings-status error';
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    saveButton.disabled = true;
    resetButton.disabled = true;
    try {
      const result = await api('/api/v1/system/settings/pip-index', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pipIndexUrl: urlInput.value.trim() || null }),
      });
      showStatus('镜像源保存成功');
      urlInput.value = result.pipIndexUrl || '';
      resetButton.disabled = result.source !== 'configured';
    } catch (error) {
      showStatus(`保存失败：${error.message}`, true);
    } finally {
      saveButton.disabled = false;
      if (urlInput.value) resetButton.disabled = false;
    }
  });

  resetButton.addEventListener('click', async () => {
    if (!confirm('确认恢复默认的 pip 镜像源？')) return;
    saveButton.disabled = true;
    resetButton.disabled = true;
    try {
      await api('/api/v1/system/settings/pip-index', { method: 'DELETE' });
      showStatus('已恢复默认镜像源');
      urlInput.value = '';
      await loadConfiguration();
    } catch (error) {
      showStatus(`恢复失败：${error.message}`, true);
      saveButton.disabled = false;
      resetButton.disabled = false;
    }
  });

  loadConfiguration();
})();
