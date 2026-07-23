export const store = {
  data: null,
  busy: false,
  highlighted: null,
  samePageMode: false,
  pageOperationMode: false,
  popupMode: false,
  selectedPage: null,
  directoryQuery: '',
  expandedPages: new Set(),
};

export function currentCandidates() {
  return store.data?.current_candidates || store.data?.candidates || [];
}
