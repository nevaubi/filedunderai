(function () {
  'use strict';
  window.FU_PAGES = window.FU_PAGES || {};
  window.FU_PAGES.companies = async query => {
    const requestedRoute = ROUTE_SEQ;
    if (!window.FU_COMPANIES.list().length) await window.FU_COMPANIES.load(INDEX?.companies || []);
    if (requestedRoute !== ROUTE_SEQ) return;
    window.FU_COMPANIES.render(page, { logo: miniLogo, initialQuery: query || '', onOpen: () => { STATE.coTab = 'overview'; } });
  };
})();
