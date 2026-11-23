'use client';

import { Languages } from 'lucide-react';
import { useEffect, useSyncExternalStore } from 'react';
import { translate } from './i18n-vi';

// shortcut: Vietnamese is applied to the rendered DOM by exact English-text lookup instead of t() calls in
// every component, so new or reworded English text stays English until VI gets the entry. Move to t() calls
// if the UI keeps more than two languages.
export type Language = 'en' | 'vi';
const storageKey = 'thorax-language';
const changeEvent = 'thorax-language-change';
const attributes = ['aria-label', 'title', 'placeholder'] as const;

function readLanguage(): Language {
  try { return window.localStorage.getItem(storageKey) === 'vi' ? 'vi' : 'en'; } catch { return 'en'; }
}

function subscribe(onChange: () => void) {
  window.addEventListener(changeEvent, onChange);
  window.addEventListener('storage', onChange);
  return () => { window.removeEventListener(changeEvent, onChange); window.removeEventListener('storage', onChange); };
}

export function useLanguage(): Language {
  return useSyncExternalStore(subscribe, readLanguage, () => 'en');
}

export function setLanguage(language: Language) {
  try { window.localStorage.setItem(storageKey, language); } catch { /* the choice then lasts for this page only */ }
  window.dispatchEvent(new Event(changeEvent));
}

export function LanguageToggle() {
  const language = useLanguage();
  const next = language === 'vi' ? 'en' : 'vi';
  return <button type="button" className="button ghost language-toggle" onClick={() => setLanguage(next)}
    aria-label={language === 'vi' ? 'Switch to English' : 'Chuyển sang tiếng Việt'} data-no-translate>
    <Languages size={16} aria-hidden /> {language === 'vi' ? 'EN' : 'VI'}
  </button>;
}

/** Rewrites UI text to Vietnamese while selected and restores the English originals when switched back. */
export function Translator() {
  const language = useLanguage();
  useEffect(() => {
    document.documentElement.lang = language;
    if (language !== 'vi') return;
    const texts = new Map<Text, { english: string; shown: string }>();
    const attrs = new Map<Element, Map<string, { english: string; shown: string }>>();
    const skip = (node: Node | null) => !!node?.parentElement?.closest('textarea, script, style, [contenteditable="true"], [data-no-translate]');

    const textNode = (node: Text) => {
      if (skip(node)) return;
      const seen = texts.get(node);
      if (seen && node.data === seen.shown) return;  // our own write
      const shown = translate(node.data);
      if (shown === null) { texts.delete(node); return; }
      texts.set(node, { english: node.data, shown });
      node.data = shown;
    };
    const element = (el: Element) => {
      if (el.closest('[data-no-translate]')) return;
      for (const name of attributes) {
        const value = el.getAttribute(name);
        if (value === null) continue;
        const saved = attrs.get(el)?.get(name);
        if (saved && value === saved.shown) continue;
        const shown = translate(value);
        if (shown === null) continue;
        if (!attrs.has(el)) attrs.set(el, new Map());
        attrs.get(el)!.set(name, { english: value, shown });
        el.setAttribute(name, shown);
      }
    };
    const walk = (root: Node) => {
      if (root.nodeType === Node.TEXT_NODE) return textNode(root as Text);
      if (root.nodeType !== Node.ELEMENT_NODE) return;
      element(root as Element);
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (node.nodeType === Node.TEXT_NODE) textNode(node as Text); else element(node as Element);
      }
    };

    walk(document.head.querySelector('title') ?? document.head);
    walk(document.body);
    const observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'characterData') textNode(record.target as Text);
        else if (record.type === 'attributes') element(record.target as Element);
        else record.addedNodes.forEach(walk);
      }
    });
    observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: [...attributes] });
    return () => {
      observer.disconnect();
      // Put back English only where React has not replaced the text in the meantime.
      texts.forEach(({ english, shown }, node) => { if (node.isConnected && node.data === shown) node.data = english; });
      attrs.forEach((names, el) => names.forEach(({ english, shown }, name) => { if (el.getAttribute(name) === shown) el.setAttribute(name, english); }));
    };
  }, [language]);
  return null;
}
