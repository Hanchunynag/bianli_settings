import { el } from './dom.js';
import { store } from './state.js';

function readableError(data, response) {
  if (typeof data?.error === 'string') return data.error;
  if (typeof data?.detail === 'string') return data.detail;
  if (Array.isArray(data?.detail)) {
    return data.detail.map((item) => item.msg || JSON.stringify(item)).join('；');
  }
  return `请求失败（HTTP ${response.status}）`;
}

async function requestJson(path, options = {}, blocking = true) {
  if (blocking && store.busy) return null;
  if (blocking) {
    store.busy = true;
    el('loading').classList.remove('hidden');
    document.querySelectorAll('button').forEach((button) => { button.disabled = true; });
  }
  const errorBox = el('error');
  try {
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok || data?.ok === false) {
      throw new Error(readableError(data, response));
    }
    errorBox.textContent = '';
    errorBox.classList.add('hidden');
    return data;
  } catch (err) {
    errorBox.textContent = err.message || String(err);
    errorBox.classList.remove('hidden');
    return null;
  } finally {
    if (blocking) {
      store.busy = false;
      el('loading').classList.add('hidden');
      document.querySelectorAll('button').forEach((button) => { button.disabled = false; });
    }
  }
}

export function api(path, options = {}) {
  return requestJson(path, options, true);
}

export function queryJson(path, options = {}) {
  return requestJson(path, options, false);
}

export function postJson(path, body = {}) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
