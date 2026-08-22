(() => {
  const button = document.getElementById('dshgw-logout-button');
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const response = await fetch('/api/gateway/v1/auth/logout', { method: 'POST' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      location.replace('/login');
    } catch (error) {
      button.disabled = false;
      alert(`退出失败：${error && error.message ? error.message : error}`);
    }
  });
})();
