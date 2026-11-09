/* Reader chrome only; source titles and text always retain their original spelling. */
const annaReaderLabels = {
  zh: {
    title:'不全书阅读', chapters:'章节导航', query:'条目查询', skip:'跳到正文', navigation:'阅读导航',
    openNavigation:'打开阅读导航', closeNavigation:'收起阅读导航', contents:'目录', outline:'本页', search:'搜索',
    filter:'筛选目录', filterPlaceholder:'查找书籍或章节…', clearFilter:'清空目录筛选', collapse:'全部收起', locate:'定位当前章节',
    expandNode:'展开 {name}', collapseNode:'收起 {name}', missingNode:'原文页面缺失', emptyTree:'没有匹配的目录。',
    searchLabel:'全文搜索', searchPlaceholder:'搜索规则正文…', searchSubmit:'搜索', titleOnly:'只搜索页面标题',
    searchHint:'搜索完整原文，包括尚未整理成条目的页面。', searchEntries:'在条目查询中筛选',
    searching:'正在搜索…', found:'找到 {count} 页', searchEmpty:'没有匹配页面。试试更短的词语。', searchError:'搜索失败，请重试。',
    anyMatch:'没有全部词语同时命中的页面；以下为任一词语命中。', searchPage:'第 {page} / {pages} 页',
    previousResults:'上一批结果', nextResults:'下一批结果', noOutline:'本页尚未提取到可定位条目；原文仍完整展示。',
    entry:'在 anna 查看条目', pageEntries:'本页条目', previous:'上一章', next:'下一章',
    original:'原貌正文', loading:'正在打开原文…', ready:'原文已就绪',
    notInContents:'未编入目录的页面', sourceVersion:'不全书 v{version}', retry:'重试', home:'回到阅读首页',
    keyboard:'/ 搜索', emptyTitle:'原文阅读',
    errors: {
      unavailable:'阅读服务未启动。请运行 make up，启动 anna 查询与阅读。',
      network:'无法读取阅读数据，请检查服务后重试。', invalid_location:'阅读地址无效，请选择一个页面或条目。',
      page_missing:'当前快照中没有这个页面。原文可能已更新，请从目录重新查找。',
      entry_missing:'当前快照中没有这个条目版本。不会自动跳到同名条目，请重新查询。',
      entry_unlocatable:'这个条目没有可靠的原文范围，请在 anna 查看提取证据。',
      bundle_changed:'数据已经重建，请重启 make up，再刷新页面。',
      snapshot_changed:'原文快照已经变化，请刷新页面后重新定位。',
      frame_timeout:'正文未能完成加载，请重试；若已重建数据，请重启 make up。',
      fragment_missing:'正文已打开，但原锚点无法定位。请通过本页条目重新选择位置。'
    }
  },
  en: {
    title:'Book reader', chapters:'Chapter navigation', query:'Entry browser', skip:'Skip to the text', navigation:'Reading navigation',
    openNavigation:'Open reading navigation', closeNavigation:'Close reading navigation', contents:'Contents', outline:'On this page', search:'Search',
    filter:'Filter contents', filterPlaceholder:'Find a book or chapter…', clearFilter:'Clear contents filter', collapse:'Collapse all', locate:'Locate current chapter',
    expandNode:'Expand {name}', collapseNode:'Collapse {name}', missingNode:'Source page missing', emptyTree:'No matching chapters.',
    searchLabel:'Full-text search', searchPlaceholder:'Search the source text…', searchSubmit:'Search', titleOnly:'Page titles only',
    searchHint:'Search complete documents, including pages not yet organized into entries.', searchEntries:'Filter in the entry browser',
    searching:'Searching…', found:'Found {count} pages', searchEmpty:'No matching pages. Try a shorter phrase.', searchError:'Search failed. Please retry.',
    anyMatch:'No page matches every term; showing pages matching any term.', searchPage:'Page {page} / {pages}',
    previousResults:'Previous results', nextResults:'Next results', noOutline:'No locatable entries have been extracted on this page. The complete source is still available.',
    entry:'Open entry in anna', pageEntries:'Entries on this page', previous:'Previous chapter', next:'Next chapter',
    original:'Original layout', loading:'Opening the source…', ready:'Source ready',
    notInContents:'Page outside the contents', sourceVersion:'Collection v{version}', retry:'Retry', home:'Reader home',
    keyboard:'/ Search', emptyTitle:'Source reader',
    errors: {
      unavailable:'The reading service is not running. Run make up to start anna with its reader.',
      network:'Could not load reading data. Check the service and retry.', invalid_location:'Invalid reading address. Select one page or entry.',
      page_missing:'This page is not in the current snapshot. The source may have changed; find it in the contents.',
      entry_missing:'This entry version is not in the current snapshot. No same-name substitution was made; search again.',
      entry_unlocatable:'This entry has no reliable source range. Inspect its extraction evidence in anna.',
      bundle_changed:'The data was rebuilt. Restart make up, then refresh this page.',
      snapshot_changed:'The source snapshot changed. Refresh before navigating again.',
      frame_timeout:'The source did not finish loading. Retry, or restart make up if the data was rebuilt.',
      fragment_missing:'The source is open, but its anchor could not be located. Choose an entry on this page.'
    }
  }
};
