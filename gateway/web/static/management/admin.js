const rows = document.getElementById('users');
const message = document.getElementById('message');
const createForm = document.getElementById('create-user-form');
const resetDialog = document.getElementById('reset-dialog');
const resetForm = document.getElementById('reset-password-form');
const resetUsername = document.getElementById('reset-username');
function cell(row, value) { const item = document.createElement('td'); item.textContent = value; row.appendChild(item); }
function actionButton(label, action, className = '') {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.className = `small ${className}`.trim();
  button.addEventListener('click', action);
  return button;
}
async function requestJSON(url, options = {}) {
  const response = await fetch(url, options);
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
  return result;
}
function showResetDialog(username) {
  resetDialog.dataset.username = username;
  resetUsername.textContent = username;
  resetForm.reset();
  resetDialog.showModal();
}
async function changeUserState(user) {
  const nextDisabled = !user.disabled;
  const verb = nextDisabled ? '停用' : '启用';
  if (!confirm(`确认${verb}用户 ${user.username}？`)) return;
  message.textContent = `正在${verb} ${user.username}…`;
  try {
    await requestJSON(`/api/v1/users/${encodeURIComponent(user.username)}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({disabled: nextDisabled}),
    });
    await loadUsers(`${user.username} 已${verb}`);
  } catch (error) { message.textContent = `${verb}失败：${error.message || error}`; }
}
async function loadUsers(successMessage = '') {
  message.textContent = '正在加载用户列表…';
  try {
    const result = await requestJSON('/api/v1/users');
    rows.replaceChildren();
    for (const user of result.users) {
      const row = document.createElement('tr');
      cell(row, user.username);
      const role = document.createElement('td');
      const roleBadge = document.createElement('span'); roleBadge.className = 'role'; roleBadge.textContent = user.role === 'admin' ? '管理员' : '用户'; role.appendChild(roleBadge); row.appendChild(role);
      const state = document.createElement('td');
      const badge = document.createElement('span'); badge.className = `status${user.disabled ? ' disabled' : ''}`; badge.textContent = user.disabled ? '已停用' : '正常'; state.appendChild(badge); row.appendChild(state);
      cell(row, user.created_at ? new Date(user.created_at).toLocaleString() : '—');
      const actions = document.createElement('td'); actions.className = 'actions';
      actions.appendChild(actionButton('重置密码', () => showResetDialog(user.username)));
      if (user.role !== 'admin') actions.appendChild(actionButton(user.disabled ? '启用' : '停用', () => changeUserState(user), user.disabled ? '' : 'danger'));
      row.appendChild(actions);
      rows.appendChild(row);
    }
    message.textContent = successMessage || `共 ${result.users.length} 个注册用户`;
  } catch (error) { message.textContent = `加载失败：${error.message || error}`; }
}
createForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = createForm.querySelector('button[type="submit"]');
  const data = new FormData(createForm);
  button.disabled = true;
  message.textContent = '正在创建用户…';
  try {
    const user = await requestJSON('/api/v1/users', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: data.get('username'), password: data.get('password')}),
    });
    createForm.reset();
    await loadUsers(`用户 ${user.username} 创建成功`);
  } catch (error) { message.textContent = `创建失败：${error.message || error}`; }
  finally { button.disabled = false; }
});
resetForm.addEventListener('submit', async event => {
  event.preventDefault();
  const username = resetDialog.dataset.username;
  const password = new FormData(resetForm).get('password');
  const button = resetForm.querySelector('button[type="submit"]');
  button.disabled = true;
  message.textContent = `正在重置 ${username} 的密码…`;
  try {
    await requestJSON(`/api/v1/users/${encodeURIComponent(username)}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password}),
    });
    resetDialog.close();
    await loadUsers(`${username} 的密码已重置`);
  } catch (error) { message.textContent = `重置失败：${error.message || error}`; }
  finally { button.disabled = false; }
});
document.getElementById('cancel-reset').addEventListener('click', () => resetDialog.close());
document.getElementById('refresh').addEventListener('click', () => loadUsers());
document.getElementById('logout').addEventListener('click', async event => {
  event.currentTarget.disabled = true;
  try {
    const response = await fetch('/api/v1/auth/sessions/current', {method: 'DELETE'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    location.replace('/login');
  } catch (error) {
    event.currentTarget.disabled = false;
    message.textContent = `退出失败：${error.message || error}`;
  }
});
loadUsers();
