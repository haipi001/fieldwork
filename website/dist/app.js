const tabs = [...document.querySelectorAll('[role="tab"]')];

function selectTab(tab) {
  tabs.forEach((item) => {
    const active = item === tab;
    item.setAttribute('aria-selected', String(active));
    item.tabIndex = active ? 0 : -1;
    const panel = document.getElementById(item.getAttribute('aria-controls'));
    panel.hidden = !active;
    if (active) {
      panel.classList.remove('is-entering');
      void panel.offsetWidth;
      panel.classList.add('is-entering');
    }
  });
}

tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => selectTab(tab));
  tab.addEventListener('keydown', (event) => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault();
    selectTab(tabs[next]);
    tabs[next].focus();
  });
});

const header = document.querySelector('.site-header');
let lastY = 0;
addEventListener('scroll', () => {
  const y = scrollY;
  header.style.transform = y > lastY && y > 140 ? 'translateY(-100%)' : 'translateY(0)';
  lastY = y;
}, { passive: true });

if (matchMedia('(prefers-reduced-motion: no-preference)').matches && 'IntersectionObserver' in window) {
  const elements = document.querySelectorAll(
    '.v5-intro-grid, .v5-flow, .v5-product-head, .v5-shot, .v5-product-copy, .v5-domains-head, .domain-tabs, .v5-trust-grid, .v5-status-head, .v5-status-list'
  );
  elements.forEach((element) => element.classList.add('v5-reveal'));
  const reveal = new IntersectionObserver((entries, observer) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: '0px 0px 40px 0px' });
  elements.forEach((element) => reveal.observe(element));
}

const navLinks = [...document.querySelectorAll('.site-header nav a')];
if ('IntersectionObserver' in window) {
  const sections = navLinks.map((link) => document.querySelector(link.getAttribute('href'))).filter(Boolean);
  const current = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      navLinks.forEach((link) => link.classList.toggle('is-current', link.getAttribute('href') === '#' + entry.target.id));
    });
  }, { rootMargin: '-28% 0px -58% 0px' });
  sections.forEach((section) => current.observe(section));
}
