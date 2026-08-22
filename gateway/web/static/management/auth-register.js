document.getElementById('auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const error = document.getElementById('error');
  error.textContent = '';
  const data = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const response = await fetch('/api/gateway/v1/auth/register', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || '请求失败');
    location.replace('/');
  } catch (reason) { error.textContent = reason.message || '请求失败'; }
});
