import { el } from './dom.js';
import { store } from './state.js';

export function setBusy(value) {
  store.busy = value;
  el('loading').classList.toggle('hidden', !value);
  document.querySelectorAll('button').forEach((button) => {
    button.disabled = value;
  });
}

export function showError(message) {
  el('error').textContent = message || '';
  el('error').classList.toggle('hidden', !message);
}

export async function requestJson(path, options = {}) {
  const res = await fetch(path, options);
  return res.json();
}

export async function api(path, options = {}) {
  if (store.busy) return null;
  setBusy(true);
  showError('');
  try {
    const data = await requestJson(path, options);
    if (!data.ok && data.error) throw new Error(data.error);
    return data;
  } catch (err) {
    showError(err.message || String(err));
    return null;
  } finally {
    setBusy(false);
  }
}

export function postJson(path, body = {}) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
