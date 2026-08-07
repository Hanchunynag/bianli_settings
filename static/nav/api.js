import { el } from './dom.js?v=special-profile-15';
import { store } from './state.js?v=special-profile-15';

function readableError(data, response) {
  if (typeof data?.error === 'string') return data.error;
  if (typeof data?.detail === 'string') return data.detail;
  if (Array.isArray(data?.detail)) {
    return data.detail.map((item) => item.msg || JSON.stringify(item)).join('；');
  }
  return `请求失败（HTTP ${response.status}）`;
}

function withSettingsProfile(path) {
  const url = new URL(path, window.location.origin);
  if (
    url.pathname.startsWith('/api/')
    && !url.searchParams.has('profile_id')
  ) {
    url.searchParams.set(
      'profile_id',
      store.activeSettingsProfileId || 'default',
    );
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

function withoutSpecialStepMarker(path, options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  if (
    method !== 'POST'
    || !String(path).startsWith('/api/record_action')
    || typeof options.body !== 'string'
  ) {
    return options;
  }
  try {
    const body = JSON.parse(options.body);
    if (body?.action !== 'special_tap') return options;
    const effect = String(body?.payload?.effect || '').trim();
    const matched = /^special_capture::([^:]+)(?:::step\d+)?$/.exec(effect);
    if (!matched) return options;
    body.payload.effect = `special_capture::${matched[1]}`;
    return { ...options, body: JSON.stringify(body) };
  } catch (_error) {
    return options;
  }
}

async function requestJson(path, options = {}, blocking = true) {
  if (blocking && store.busy) return null;
  const previouslyDisabledControls = new Set();
  if (blocking) {
    store.busy = true;
    el('loading').classList.remove('hidden');
    document.querySelectorAll('button, select').forEach((control) => {
      if (control.disabled) previouslyDisabledControls.add(control);
      control.disabled = true;
    });
  }
  const errorBox = el('error');
  try {
    const response = await fetch(
      withSettingsProfile(path),
      withoutSpecialStepMarker(path, options),
    );
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
      document.querySelectorAll('button, select').forEach((control) => {
        control.disabled = previouslyDisabledControls.has(control);
      });
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
