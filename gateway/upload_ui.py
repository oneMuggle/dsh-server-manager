from __future__ import annotations

from .web import render_template


UPLOAD_WIDGET = render_template("upload-widget.html").encode("utf-8")

ADMIN_WIDGET = """
<style>
  #dshgw-admin-button {
    position: fixed; right: 24px; bottom: 132px; z-index: 2147483000;
    border: 1px solid #465066; border-radius: 999px; padding: 11px 17px;
    color: #f5f7fb; background: #181d27; box-shadow: 0 8px 28px #0005;
    font: 600 14px/1 system-ui, sans-serif; text-decoration: none;
  }
</style>
<a id="dshgw-admin-button" href="/admin">管理后台</a>
""".encode("utf-8")

LOGOUT_WIDGET = """
<style>
  #dshgw-logout-button {
    position: fixed; right: 24px; bottom: 78px; z-index: 2147483000;
    border: 1px solid var(--dsw-alias-border-l2, rgba(0, 0, 0, .1));
    border-radius: 999px; padding: 10px 17px;
    color: var(--dsw-alias-label-primary, #0f1115);
    background: var(--dsw-alias-bg-layer-2, #fff);
    box-shadow: var(--dsw-shadow-lv2, 0 8px 28px rgba(0, 0, 0, .18));
    font: 500 14px/1 system-ui, sans-serif; cursor: pointer;
  }
  #dshgw-logout-button:hover { background: var(--dsw-alias-interactive-bg-hover, #f2f3f5); }
  #dshgw-logout-button:disabled { cursor: wait; opacity: .55; }
</style>
<button id="dshgw-logout-button" type="button">退出登录</button>
<script>
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
</script>
""".encode("utf-8")
