import { api, postJson } from './nav/api.js?v=special-profile-15';
import { el, escapeHtml } from './nav/dom.js?v=special-profile-15';
import { store } from './nav/state.js?v=special-profile-15';
import { refreshOrphans, render, renderOverlay } from './nav/render.js?v=special-single-ui-19';

const popupTypeButtons = [...document.querySelectorAll('[data-popup-type]')];
const popupTypes = new Set(popupTypeButtons.map((button) => button.dataset.popupType));

const specialOperateButton = el('special_opeartebutton');
const finishSpecialButton = el('finishSpecialOperateBtn');
const cancelSpecialButton = el('cancelSpecialOperateBtn');
let specialCapture = null;

function currentPageName() {
  return String(
    store.data?.active_page
    || store.data?.active_state?.page_name
    || store.data?.state?.page_name
    || '',
  ).trim();
}

function specialEffect(sessionId, stepIndex) {
  return `special_capture::${sessionId}::step${stepIndex}`;
}

function refreshSpecialButtons() {
  const armed = Boolean(specialCapture);
  specialOperateButton.classList.toggle('isArmed', armed);
  specialOperateButton.setAttribute('aria-pressed', String(armed));
  specialOperateButton.disabled = armed;
  finishSpecialButton.disabled = !armed;
  cancelSpecialButton.disabled = !armed;
}

function renderSpecialHint(message = '') {
  if (!specialCapture) return;
  const status = el('overlayStatus');
  status.textContent = message || (
    `正在录制 special_operate：${specialCapture.pageName}，已记录 ${specialCapture.stepCount} 步。`
    + '继续点击截图录制下一步；结束请点“完成特殊操作”，放弃请点“取消特殊操作”。'
  );
  status.classList.remove('hidden');
}

function startSpecialOperate() {
  const pageName = currentPageName();
  if (!pageName) {
    window.alert('当前页面尚未确定，请先采集或绑定当前页面。');
    return;
  }
  if (store.popupType) selectPopupType(null, false);
  specialCapture = {
    id: `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
    pageName,
    stepCount: 0,
  };
  refreshSpecialButtons();
  renderSpecialHint();
}

function finishSpecialOperate() {
  if (!specialCapture) return;
  if (!specialCapture.stepCount) {
    window.alert('当前 special_operate 还没有录制任何步骤。');
    return;
  }
  const count = specialCapture.stepCount;
  specialCapture = null;
  refreshSpecialButtons();
  const status = el('overlayStatus');
  status.textContent = `已完成一组 special_operate，共 ${count} 步。`;
  status.classList.remove('hidden');
  render(store.data);
}

async function cancelSpecialOperate() {
  if (!specialCapture) return;
  const capture = specialCapture;
  const data = await postJson('/api/console_action', {
    action: 'cancel_special_capture',
    payload: { page_name: capture.pageName, session_id: capture.id },
  });
  if (!data) {
    renderSpecialHint('取消失败，采集状态仍保留。');
    return;
  }
  specialCapture = null;
  refreshSpecialButtons();
  renderFollowingActivePage(data);
}

function selectPopupType(popupType = null, rerender = true) {
  if (popupType && specialCapture) {
    window.alert('当前正在录制多步 special_operate，请先完成或取消本组采集。');
    return;
  }
  store.popupType = popupType && popupTypes.has(popupType) ? popupType : null;
  popupTypeButtons.forEach((button) => {
    const selected = button.dataset.popupType === store.popupType;
    button.classList.toggle('isArmed', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  if (rerender) render(store.data);
}

function renderFollowingActivePage(data) {
  if (!data) return;
  store.selectedPage = null;
  store.showingOrphans = false;
  render(data);
  refreshSpecialButtons();
  if (specialCapture) renderSpecialHint();
}

function settingsProfileLabel(profile) {
  const identity = [
    profile.settings_version,
    profile.device_model,
  ].filter(Boolean).join(' · ');
  const name = profile.name || '';
  if (!identity) return name || '默认配置';
  return name && name !== identity ? `${name}（${identity}）` : identity;
}

function renderSettingsProfiles(data) {
  if (!data?.profiles) return;
  store.settingsProfiles = data.profiles;
  const profileById = new Map(
    data.profiles.map((profile) => [profile.profile_id, profile]),
  );
  if (!profileById.has(store.activeSettingsProfileId)) {
    store.activeSettingsProfileId = 'default';
  }
  try {
    window.sessionStorage.setItem(
      'settingsProfileId',
      store.activeSettingsProfileId,
    );
  } catch (_error) {
    // 浏览器禁用 sessionStorage 时仍使用当前标签页内存状态。
  }
  const active = profileById.get(store.activeSettingsProfileId);
  const profileSelect = el('settingsProfileSelect');
  profileSelect.innerHTML = data.profiles.map((profile) => `
    <option value="${escapeHtml(profile.profile_id)}">
      ${escapeHtml(settingsProfileLabel(profile))}
    </option>`).join('');
  profileSelect.value = store.activeSettingsProfileId;

  const parentSelect = el('parentSettingsProfileSelect');
  const previousParent = parentSelect.value;
  parentSelect.innerHTML = data.profiles.map((profile) => `
    <option value="${escapeHtml(profile.profile_id)}">
      ${escapeHtml(settingsProfileLabel(profile))}
    </option>`).join('');
  parentSelect.value = profileById.has(previousParent)
    ? previousParent
    : store.activeSettingsProfileId;

  if (active?.is_default) {
    el('profileInheritance').textContent = '默认配置 · 继续读取原始 work_dir 配置文件';
  } else if (active) {
    const parent = profileById.get(active.parent_profile_id);
    el('profileInheritance').textContent = [
      active.settings_version,
      active.device_model,
      `继承自 ${parent?.name || active.parent_profile_id || '默认配置'}`,
    ].filter(Boolean).join(' · ');
  }

  el('settingsProfileList').innerHTML = data.profiles.map((profile) => {
    const parent = profileById.get(profile.parent_profile_id);
    const isActive = profile.profile_id === store.activeSettingsProfileId;
    return `
      <article class="profileCard${isActive ? ' isActive' : ''}">
        <div class="profileCardMain">
          <div class="profileCardTitle">
            <strong>${escapeHtml(profile.name || '未命名配置')}</strong>
            ${isActive ? '<span class="statusBadge isManual">当前标签页</span>' : ''}
            ${profile.is_default ? '<span class="statusBadge">原始配置</span>' : ''}
          </div>
          <div class="profileCardMeta">
            <span>${escapeHtml(profile.settings_version || '未标注版本')}</span>
            <span>${escapeHtml(profile.device_model || '未标注机型')}</span>
            <span>${profile.page_count || 0} 页面</span>
            <span>${profile.transition_count || 0} 跳转</span>
          </div>
          <div class="profileCardSource">
            ${profile.is_default
              ? '直接使用启动参数指定的原始 work_dir'
              : `创建时继承：${escapeHtml(parent?.name || profile.parent_profile_id || '默认配置')}`}
          </div>
        </div>
        <button class="secondary compact" type="button"
          data-profile-action="switch"
          data-profile-id="${escapeHtml(profile.profile_id)}"
          ${isActive ? 'disabled' : ''}>
          ${isActive ? '使用中' : '切换'}
        </button>
      </article>`;
  }).join('');
}

async function refreshSettingsProfiles() {
  const data = await api('/api/settings_profiles');
  if (data) renderSettingsProfiles(data);
  return data;
}

function resetDirectoryForProfileSwitch() {
  store.selectedPage = null;
  store.showingOrphans = false;
  store.directoryQuery = '';
  store.expandedPages.clear();
  el('pageSearch').value = '';
  el('graphBox').classList.add('hidden');
}

async function switchSettingsProfile(profileId) {
  if (!profileId || profileId === store.activeSettingsProfileId) return;
  if (specialCapture) {
    window.alert('当前正在录制 special_operate，请先完成或取消后再切换版本/机型。');
    el('settingsProfileSelect').value = store.activeSettingsProfileId;
    return;
  }
  const target = store.settingsProfiles.find(
    (profile) => profile.profile_id === profileId,
  );
  if (!window.confirm(`切换到“${target?.name || profileId}”？当前页面目录和详情将重新加载。`)) {
    el('settingsProfileSelect').value = store.activeSettingsProfileId;
    return;
  }
  store.activeSettingsProfileId = profileId;
  try {
    window.sessionStorage.setItem('settingsProfileId', profileId);
  } catch (_error) {
    // 浏览器禁用 sessionStorage 时仍使用当前标签页内存状态。
  }
  resetDirectoryForProfileSwitch();
  renderSettingsProfiles({ profiles: store.settingsProfiles });
  renderFollowingActivePage(await api('/api/state'));
  el('overlayStatus').textContent = `当前标签页已切换到：${target?.name || profileId}`;
  el('overlayStatus').classList.remove('hidden');
}

el('captureBtn').onclick = async () => renderFollowingActivePage(
  await postJson('/api/console_action', { action: 'capture_current', payload: {} }),
);
el('backBtn').onclick = async () => renderFollowingActivePage(
  await postJson('/api/console_action', { action: 'system_back', payload: {} }),
);
el('clearPendingBtn').onclick = async () => render(await postJson('/api/console_action', { action: 'clear_pending', payload: {} }));
el('orphanBtn').onclick = refreshOrphans;
popupTypeButtons.forEach((button) => {
  button.onclick = () => selectPopupType(button.dataset.popupType);
});
specialOperateButton.onclick = startSpecialOperate;
finishSpecialButton.onclick = finishSpecialOperate;
cancelSpecialButton.onclick = cancelSpecialOperate;

el('bindCurrentPageBtn').onclick = async () => {
  const pageName = String(store.selectedPage || '').trim();
  if (!pageName) {
    window.alert('请先在页面目录中点击“详情”选择一个页面，再执行绑定。');
    return;
  }
  const data = await postJson('/api/console_action', {
    action: 'bind_current_page',
    payload: { page_name: pageName },
  });
  renderFollowingActivePage(data);
};

el('addAndBindCurrentPageBtn').onclick = async () => {
  const pageNameInput = window.prompt('请输入新的内部页面 ID，例如 Pages_高级设置：', 'Pages_');
  if (!pageNameInput) return;
  const pageName = pageNameInput.startsWith('Pages_') ? pageNameInput.trim() : `Pages_${pageNameInput.trim()}`;
  const pageDescription = window.prompt('请输入页面显示名称：', pageName.replace(/^Pages_/, ''));
  if (!pageDescription) return;
  const data = await postJson('/api/console_action', {
    action: 'create_manual_page',
    payload: { page_name: pageName, page_description: pageDescription },
  });
  if (!data) return;
  renderFollowingActivePage(data);
};
el('graphBtn').onclick = async () => {
  const data = await api('/api/graph');
  if (!data) return;
  const box = el('graphBox');
  box.textContent = JSON.stringify(data, null, 2);
  box.classList.toggle('hidden');
};
el('settingsProfileSelect').onchange = (event) => {
  switchSettingsProfile(event.target.value);
};
el('openProfileManagerBtn').onclick = async () => {
  await refreshSettingsProfiles();
  el('settingsProfileDialog').showModal();
};
el('closeProfileManagerBtn').onclick = () => {
  el('settingsProfileDialog').close();
};
el('settingsProfileList').onclick = (event) => {
  const button = event.target.closest('[data-profile-action="switch"]');
  if (button) switchSettingsProfile(button.dataset.profileId);
};
el('settingsProfileForm').onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const result = await postJson('/api/settings_profiles', {
    name: form.elements.name.value,
    settings_version: form.elements.settings_version.value,
    device_model: form.elements.device_model.value,
    parent_profile_id: form.elements.parent_profile_id.value,
  });
  if (!result) return;
  store.activeSettingsProfileId = result.profile.profile_id;
  try {
    window.sessionStorage.setItem(
      'settingsProfileId',
      result.profile.profile_id,
    );
  } catch (_error) {
    // 浏览器禁用 sessionStorage 时仍使用当前标签页内存状态。
  }
  resetDirectoryForProfileSwitch();
  form.reset();
  await refreshSettingsProfiles();
  el('settingsProfileDialog').close();
  renderFollowingActivePage(await api('/api/state'));
  el('overlayStatus').textContent = result.message;
  el('overlayStatus').classList.remove('hidden');
};

el('screen').addEventListener('click', async (event) => {
  if (!store.data?.screen_metrics?.screen_size || store.busy) return;
  const rect = el('screen').getBoundingClientRect();
  const [screenWidth, screenHeight] = store.data.screen_metrics.screen_size;
  const point = {
    x: Math.round((event.clientX - rect.left) / rect.width * screenWidth),
    y: Math.round((event.clientY - rect.top) / rect.height * screenHeight),
    manual_label: '',
  };
  const popupType = store.popupType;
  const special = specialCapture;
  const nextSpecialStep = special ? special.stepCount + 1 : 0;
  const action = popupType ? 'popup_tap' : special ? 'special_tap' : 'tap_point';
  const payload = popupType
    ? { ...point, popup_type: popupType }
    : special
      ? { ...point, operate: 'tap', effect: specialEffect(special.id, nextSpecialStep) }
      : { ...point, expect: 'new_page', effect: '' };
  let data = null;
  try {
    data = await postJson('/api/record_action', { action, payload });
    if (data?.needs_manual_label) {
      const label = window.prompt(data.message || '请填写该控件的稳定描述');
      if (label) data = await postJson('/api/record_action', { action, payload: { ...payload, manual_label: label } });
    }
  } finally {
    if (popupType) selectPopupType(null, false);
  }
  if (!data) {
    if (specialCapture) renderSpecialHint('本步未保存；special 采集仍保持，可继续操作或取消。');
    return;
  }
  if (specialCapture) {
    specialCapture.stepCount = nextSpecialStep;
    renderFollowingActivePage(data);
    return;
  }
  renderFollowingActivePage(data);
});

window.addEventListener('resize', renderOverlay);
refreshSettingsProfiles()
  .then(() => api('/api/state'))
  .then((data) => {
    render(data);
    refreshSpecialButtons();
  });
