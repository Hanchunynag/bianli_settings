export const store = {
  data: null,
  busy: false,
  highlighted: null,
  samePageMode: false,
  pageOperationMode: false,
  selectedPage: null,
};

export function currentCandidates() {
  return store.data?.current_candidates || store.data?.candidates || [];
}
