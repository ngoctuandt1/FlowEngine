document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('f');
  const passwordInput = document.getElementById('pw');
  const error = document.getElementById('err');

  if (!form || !passwordInput || !error) return;

  form.addEventListener('submit', async (event) => {
    event.preventDefault();

    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({password: passwordInput.value})
    });

    if (res.ok) {
      const params = new URLSearchParams(window.location.search);
      const next = params.get('next') || '/';
      let safeNext = '/';
      if (!/[\x00-\x1F\x7F\\]/.test(next)) {
        try {
          const u = new URL(next, window.location.origin);
          if (u.origin === window.location.origin) safeNext = u.pathname + u.search + u.hash;
        } catch (_) {}
      }
      window.location.href = safeNext;
    } else {
      error.style.display = 'block';
    }
  });
});
