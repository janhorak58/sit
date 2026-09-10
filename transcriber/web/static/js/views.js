const ROUTES = {
  '#dashboard': 'dashboard',
  '#new': 'new',
  '#workspace': 'workspace',
  '#library': 'library',
  '#archive': 'library',
  '#meeting': 'meeting',
  '#settings': 'settings',
  '#customizations': 'customizations',
};

export function showView(name) {
  document.querySelectorAll('.view').forEach(view => {
    view.hidden = view.dataset.view !== name;
  });
  document.querySelectorAll('.side-nav a').forEach(link => link.classList.remove('active'));
  const navName = name === 'workspace' ? null : name;
  if (navName) document.querySelector(`.side-nav a[href="#${navName}"]`)?.classList.add('active');
  window.scrollTo({top: 0});
}

function route() {
  const name = ROUTES[location.hash] || 'dashboard';
  showView(name);
  return name;
}

export function initViews(onEnter) {
  window.addEventListener('hashchange', () => onEnter(route()));
  onEnter(route());
}
