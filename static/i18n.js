/* Lightweight UI-copy translation. User content and evidence remain untouched. */
(() => {
  const catalog = window.FIELDWORK_EN || {};
  const textSource = new WeakMap();
  const attributeSource = new WeakMap();
  const supportedAttributes = ['placeholder', 'title', 'aria-label'];
  const storageKey = 'fieldwork-language';
  let language = localStorage.getItem(storageKey) === 'en' ? 'en' : 'zh-CN';
  let applying = false;
  const patterns = [
    [/^(\d+) 个可见进程$/, '$1 visible processes'],
    [/^(\d+) 个活动任务$/, '$1 active tasks'],
    [/^(\d+) 个运行中$/, '$1 running'],
    [/^(\d+) 个运行中 · 上限 (\d+)$/, '$1 running · limit $2'],
    [/^(\d+) 个项目$/, '$1 projects'],
    [/^(\d+) 个审计$/, '$1 audits'],
    [/^(\d+) 个行为 · (\d+) 条自述 · (\d+) 次导入$/, '$1 actions · $2 claims · $3 imports'],
    [/^剩余 (\d+) 阶段：(.+)$/, '$1 stages remaining: $2'],
    [/^(\d+) 个阶段已结束$/, '$1 stages completed'],
    [/^心跳 (\d+)秒前$/, 'Heartbeat $1s ago'],
    [/^心跳 (\d+)分前$/, 'Heartbeat $1m ago'],
    [/^独立证据 (\d+) · (.+) · 反证待检查$/, '$1 independent evidence · $2 · counterevidence pending'],
    [/^验证状态 (.+) · 独立证据 (\d+) · 反证待检查$/, 'Verification status $1 · $2 independent evidence · counterevidence pending'],
    [/^时间范围 (.+) · (.+) · 签名真实性已验证$/, 'Time range $1 · $2 · signature authenticity verified'],
    [/^时间范围 (.+) · (.+) · 操作员来源声明$/, 'Time range $1 · $2 · operator source attestation'],
    [/^时间范围 (.+)$/, 'Time range $1']
  ];
  function translateValue(value) {
    if (!value) return value;
    if (catalog[value]) return catalog[value];
    for (const [pattern, replacement] of patterns) if (pattern.test(value)) return value.replace(pattern, replacement);
    return value;
  }
  function splitWhitespace(value) {
    const match = value.match(/^(\s*)([\s\S]*?)(\s*)$/);
    return {before: match[1], core: match[2], after: match[3]};
  }
  function shouldSkip(node) {
    const parent = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    return !parent || !!parent.closest('[data-i18n-ignore], script, style, code, pre');
  }
  function translateText(node) {
    if (shouldSkip(node)) return;
    const parts = splitWhitespace(node.nodeValue || '');
    if (!parts.core) return;
    let source = textSource.get(node);
    if (!source || (node.nodeValue !== source.raw && node.nodeValue !== source.rendered)) {
      source = {raw: node.nodeValue, core: parts.core, before: parts.before, after: parts.after, rendered: node.nodeValue};
      textSource.set(node, source);
    }
    const core = language === 'en' ? translateValue(source.core) : source.core;
    const rendered = source.before + core + source.after;
    source.rendered = rendered;
    if (node.nodeValue !== rendered) node.nodeValue = rendered;
  }
  function translateAttributes(element) {
    if (shouldSkip(element)) return;
    let sources = attributeSource.get(element);
    if (!sources) { sources = {}; attributeSource.set(element, sources); }
    for (const name of supportedAttributes) {
      if (!element.hasAttribute(name)) continue;
      const current = element.getAttribute(name);
      const previous = sources[name];
      if (!previous || (current !== previous.raw && current !== previous.rendered)) sources[name] = {raw: current, rendered: current};
      const source = sources[name];
      const rendered = language === 'en' ? translateValue(source.raw) : source.raw;
      source.rendered = rendered;
      if (current !== rendered) element.setAttribute(name, rendered);
    }
  }
  function walk(root = document.body) {
    if (!root || shouldSkip(root)) return;
    applying = true;
    try {
      if (root.nodeType === Node.TEXT_NODE) { translateText(root); return; }
      if (root.nodeType === Node.ELEMENT_NODE) translateAttributes(root);
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) node.nodeType === Node.TEXT_NODE ? translateText(node) : translateAttributes(node);
    } finally { applying = false; }
  }
  function updateControl() {
    const button = document.getElementById('languageToggle');
    if (!button) return;
    const english = language === 'en';
    button.textContent = english ? '中' : 'EN';
    button.setAttribute('aria-label', english ? '切换至中文' : 'Switch to English');
    button.setAttribute('title', english ? '切换至中文' : 'Switch to English');
    button.setAttribute('aria-pressed', String(english));
    document.documentElement.lang = english ? 'en' : 'zh-CN';
    document.title = english ? (catalog['Fieldwork · 安全研究工作台'] || document.title) : 'Fieldwork · 安全研究工作台';
  }
  function setLanguage(next) {
    language = next === 'en' ? 'en' : 'zh-CN';
    localStorage.setItem(storageKey, language);
    walk(document.body);
    updateControl();
    document.dispatchEvent(new CustomEvent('fieldwork:languagechange', {detail: {language}}));
  }
  function init() {
    const button = document.getElementById('languageToggle');
    if (button) button.addEventListener('click', () => setLanguage(language === 'en' ? 'zh-CN' : 'en'));
    walk(document.body); updateControl();
    const observer = new MutationObserver(records => {
      if (applying) return;
      for (const record of records) {
        if (record.type === 'characterData') translateText(record.target);
        else for (const node of record.addedNodes) walk(node);
        if (record.type === 'attributes') translateAttributes(record.target);
      }
    });
    observer.observe(document.body, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:supportedAttributes});
  }
  window.FIELDWORK_I18N = {get language(){return language;},t(value){return language==='en'?translateValue(value):value;},setLanguage,refresh:()=>walk(document.body)};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true}); else init();
})();
