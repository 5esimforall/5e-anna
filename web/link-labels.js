/* Shared text for anna and the source-preserving book link layer. */
const annaLinkLabels = {
  zh: {
    newTab: '在新标签页打开',
    definition: '查看 Anna 条目', reference: '查看定义', missing: '条目定位已失效',
    bookDefinition: '在 Stella 中阅读', extractedRange: '查看提取文本与条目范围'
  },
  en: {
    newTab: 'Opens in a new tab',
    definition: 'Open Anna entry', reference: 'Open definition', missing: 'Entry location unavailable',
    bookDefinition: 'Read in Stella', extractedRange: 'Open extracted text and entry range'
  }
};
// Each page belongs to one companion: Anna answers queries and keeps the tour, Stella reads the book.
// Names are not translated.
const annaBrands = {query:'5e Anna', tour:'5e Anna', book:'5e Stella'};
// One title shape for every page: what is open, then the product.
function annaPageTitle(page, subject = '') { return [subject, annaBrands[page]].filter(Boolean).join(' · '); }
