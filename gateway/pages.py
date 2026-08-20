from __future__ import annotations

from html import escape


def auth_page(mode: str) -> str:
    is_register = mode == "register"
    title = "注册" if is_register else "登录"
    alternate = "返回登录" if is_register else "注册新账号"
    alternate_path = "/login" if is_register else "/register"
    endpoint = f"/api/gateway/v1/auth/{mode}"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title)} · DeepSeek Harness</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #10131a; color: #f4f6fb; }}
    main {{ width: min(360px, calc(100vw - 48px)); padding: 32px; border: 1px solid #303746; border-radius: 16px; background: #181d27; }}
    h1 {{ margin: 0 0 24px; font-size: 24px; }}
    label {{ display: grid; gap: 8px; margin: 16px 0; font-size: 14px; }}
    input, button, .secondary-button {{ box-sizing: border-box; width: 100%; padding: 12px; border-radius: 8px; font: inherit; }}
    input {{ border: 1px solid #465066; background: #10131a; color: inherit; }}
    button {{ margin-top: 8px; border: 0; background: #4f7cff; color: white; cursor: pointer; }}
    .secondary-button {{ display: block; border: 1px solid #465066; color: #dce4ff; text-align: center; text-decoration: none; }}
    #error {{ min-height: 20px; color: #ff8a8a; font-size: 14px; }}
    p {{ margin-bottom: 0; }}
  </style>
</head>
<body><main>
  <h1>DeepSeek Harness {escape(title)}</h1>
  <form id="auth-form">
    <label>用户名<input name="username" autocomplete="username" required minlength="3" maxlength="24" pattern="[a-z][a-z0-9_-]{{2,23}}"></label>
    <label>密码<input name="password" type="password" autocomplete="{'new-password' if is_register else 'current-password'}" required minlength="8" maxlength="256"></label>
    <div id="error"></div>
    <button type="submit">{escape(title)}</button>
  </form>
  <p><a class="secondary-button" href="{alternate_path}">{escape(alternate)}</a></p>
</main>
<script>
document.getElementById('auth-form').addEventListener('submit', async (event) => {{
  event.preventDefault();
  const error = document.getElementById('error');
  error.textContent = '';
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try {{
    const response = await fetch('{endpoint}', {{
      method: 'POST',
      headers: {{'content-type': 'application/json'}},
      body: JSON.stringify(data),
    }});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || '请求失败');
    location.replace('/');
  }} catch (reason) {{ error.textContent = reason.message || '请求失败'; }}
}});
</script></body></html>"""


def admin_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>管理后台 · DeepSeek Harness</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #10131a; color: #f4f6fb; }
    main { width: min(1000px, calc(100vw - 40px)); margin: 48px auto; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 24px; }
    h1 { margin: 0; font-size: 26px; }
    nav { display: flex; gap: 10px; }
    a, button { border: 1px solid #465066; border-radius: 8px; padding: 10px 14px; color: #dce4ff; background: #181d27; font: inherit; text-decoration: none; cursor: pointer; }
    button.primary { border-color: #315efb; background: #315efb; color: white; }
    .panel { overflow: hidden; border: 1px solid #303746; border-radius: 14px; background: #181d27; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 14px 16px; border-bottom: 1px solid #303746; text-align: left; }
    th { color: #aeb9d2; font-size: 13px; }
    tbody tr:last-child td { border-bottom: 0; }
    .role, .status { display: inline-block; border-radius: 999px; padding: 4px 9px; font-size: 12px; }
    .role { background: #263d80; color: #dce5ff; }
    .status { background: #174c37; color: #c6f7e2; }
    .status.disabled { background: #5a2929; color: #ffd2d2; }
    #message { min-height: 24px; margin: 12px 2px; color: #b9c8f6; }
    @media (max-width: 680px) {
      header { align-items: flex-start; flex-direction: column; }
      nav { width: 100%; flex-wrap: wrap; }
      th:nth-child(4), td:nth-child(4) { display: none; }
      th, td { padding: 12px 10px; }
    }
  </style>
</head>
<body><main>
  <header>
    <div><h1>用户管理</h1><div id="message">正在加载用户列表…</div></div>
    <nav><a href="/">返回 DSH</a><a href="/docs">Swagger</a><button id="refresh" class="primary" type="button">刷新</button></nav>
  </header>
  <section class="panel">
    <table>
      <thead><tr><th>用户名</th><th>角色</th><th>状态</th><th>注册时间</th></tr></thead>
      <tbody id="users"></tbody>
    </table>
  </section>
</main>
<script>
const rows = document.getElementById('users');
const message = document.getElementById('message');
function cell(row, value) { const item = document.createElement('td'); item.textContent = value; row.appendChild(item); }
async function loadUsers() {
  message.textContent = '正在加载用户列表…';
  try {
    const response = await fetch('/api/gateway/v1/admin/users');
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    rows.replaceChildren();
    for (const user of result.users) {
      const row = document.createElement('tr');
      cell(row, user.username);
      const role = document.createElement('td'); role.innerHTML = `<span class="role">${user.role === 'admin' ? '管理员' : '用户'}</span>`; row.appendChild(role);
      const state = document.createElement('td');
      const badge = document.createElement('span'); badge.className = `status${user.disabled ? ' disabled' : ''}`; badge.textContent = user.disabled ? '已停用' : '正常'; state.appendChild(badge); row.appendChild(state);
      cell(row, user.created_at ? new Date(user.created_at).toLocaleString() : '—');
      rows.appendChild(row);
    }
    message.textContent = `共 ${result.users.length} 个注册用户`;
  } catch (error) { message.textContent = `加载失败：${error.message || error}`; }
}
document.getElementById('refresh').addEventListener('click', loadUsers);
loadUsers();
</script></body></html>"""
