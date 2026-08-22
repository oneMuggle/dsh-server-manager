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
document.getElementById('logout').addEventListener('click', async event => {
  event.currentTarget.disabled = true;
  try {
    const response = await fetch('/api/gateway/v1/auth/logout', {method: 'POST'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    location.replace('/login');
  } catch (error) {
    event.currentTarget.disabled = false;
    message.textContent = `退出失败：${error.message || error}`;
  }
});
loadUsers();
