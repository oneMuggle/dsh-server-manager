from __future__ import annotations

from html import escape


def auth_page(mode: str) -> str:
    is_register = mode == "register"
    title = "注册" if is_register else "登录"
    alternate = "已有账号？登录" if is_register else "没有账号？注册"
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
    input, button {{ box-sizing: border-box; width: 100%; padding: 12px; border-radius: 8px; font: inherit; }}
    input {{ border: 1px solid #465066; background: #10131a; color: inherit; }}
    button {{ margin-top: 8px; border: 0; background: #4f7cff; color: white; cursor: pointer; }}
    a {{ color: #8eacff; }}
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
  <p><a href="{alternate_path}">{escape(alternate)}</a></p>
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
