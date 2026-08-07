function storedSettingsProfileId() {
  try {
    return window.sessionStorage.getItem('settingsProfileId') || 'default';
  } catch (_error) {
    return 'default';
  }
}

export const store = {
  data: null,
  busy: false,
  popupType: null,
  selectedPage: null,
  showingOrphans: false,
  directoryQuery: '',
  expandedPages: new Set(),
  settingsProfiles: [],
  activeSettingsProfileId: storedSettingsProfileId(),
};
