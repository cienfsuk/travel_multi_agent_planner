// Translation mapping for frontend display only
// 底层JSON数据保持英文，此文件仅用于前端显示翻译

export const TAG_TRANSLATIONS: Record<string, string> = {
  // Interest / POI tags
  culture: "文化",
  food: "美食",
  nature: "自然",
  history: "历史",
  photography: "摄影",
  citywalk: "城市漫步",
  shopping: "购物",
  nightlife: "夜生活",
  "shopping-mall": "购物中心",
  "theme-park": "主题公园",
  // Transport types
  intercity: "城际交通",
  taxi: "打车",
  metro: "地铁",
  bus: "公交",
  walk: "步行",
};

export const AREA_TRANSLATIONS: Record<string, string> = {
  // Area names
  "Nanjing City Center": "南京市中心",
  "Qinhuai River Area": "秦淮河区域",
  "Zhonghua Gate Area": "中华门区域",
  "Wuchang": "武昌",
  "Hankou": "汉口",
  "Hanyang": "汉阳",
  "Xincheng": "新城",
  "Laoshan": "崂山",
  "Shinan": "市南",
  "Shibei": "市北",
  "Chengyang": "城阳",
  "Licang": "李沧",
  "Huangdao": "黄岛",
  "Guangzhou City Center": "广州市中心",
  "Tianhe": "天河",
  "Yuexiu": "越秀",
  "Liwan": "荔湾",
  "Haizhu": "海珠",
  "Shanghai City Center": "上海市中心",
  "Huangpu": "黄浦",
  "Xuhui": "徐汇",
  "Changning": "长宁",
  "Putuo": "普陀",
  "Hongkou": "虹口",
  "Yangpu": "杨浦",
  "Pudong": "浦东",
  "Beijing City Center": "北京市中心",
  "Dongcheng": "东城",
  "Xicheng": "西城",
  "Chaoyang": "朝阳",
  "Haidian": "海淀",
  "Xicheng district": "西城区",
  "Dongcheng district": "东城区",
};

export const SEASON_TRANSLATIONS: Record<string, string> = {
  spring: "春季",
  summer: "夏季",
  autumn: "秋季",
  winter: "冬季",
};

export const STYLE_TRANSLATIONS: Record<string, string> = {
  relaxed: "宽松",
  balanced: "均衡",
  dense: "紧凑",
};

export const BUDGET_PREF_TRANSLATIONS: Record<string, string> = {
  budget: "预算优先",
  balanced: "均衡",
  premium: "高端",
};

export const BEST_TIME_TRANSLATIONS: Record<string, string> = {
  morning: "上午",
  afternoon: "下午",
  evening: "傍晚",
  night: "夜间",
};

// Segment type translations (for transport display)
export const SEGMENT_TYPE_TRANSLATIONS: Record<string, string> = {
  intercity: "高铁/飞机/城际",
  taxi: "打车",
  metro: "地铁",
  bus: "公交",
  walk: "步行",
};

// Evidence type translations
export const EVIDENCE_TYPE_TRANSLATIONS: Record<string, string> = {
  网页检索: "网页检索",
  "web search": "网页检索",
  API: "API",
  database: "数据库",
};

// Travel note style translations
export const NOTE_STYLE_TRANSLATIONS: Record<string, string> = {
  小红书风格: "小红书风格",
  "xiaohongshu": "小红书风格",
  "budget": "预算友好",
  "citywalk": "城市漫游",
};

// Main translation functions
export function translateTags(tags: string[] | undefined): string[] {
  if (!tags) return [];
  return tags.map((tag) => TAG_TRANSLATIONS[tag] ?? tag);
}

export function translateAreas(areas: string[] | undefined): string[] {
  if (!areas) return [];
  return areas.map((area) => AREA_TRANSLATIONS[area] ?? area);
}

export function translateSeasons(seasons: string[] | undefined): string[] {
  if (!seasons) return [];
  return seasons.map((s) => SEASON_TRANSLATIONS[s] ?? s);
}

export function translateStyle(style: string | undefined): string {
  if (!style) return style ?? "";
  return STYLE_TRANSLATIONS[style] ?? style;
}

export function translateBudgetPref(
  pref: string | undefined,
): string {
  if (!pref) return pref ?? "";
  return BUDGET_PREF_TRANSLATIONS[pref] ?? pref;
}

export function translateBestTime(time: string | undefined): string {
  if (!time) return time ?? "";
  return BEST_TIME_TRANSLATIONS[time] ?? time;
}

export function translateSegmentType(type: string | undefined): string {
  if (!type) return type ?? "";
  return SEGMENT_TYPE_TRANSLATIONS[type] ?? type;
}

export function translateEvidenceType(type: string | undefined): string {
  if (!type) return type ?? "";
  return EVIDENCE_TYPE_TRANSLATIONS[type] ?? type;
}

export function translateNoteStyle(style: string | undefined): string {
  if (!style) return style ?? "";
  return NOTE_STYLE_TRANSLATIONS[style] ?? style;
}

/**
 * 翻译 pacing_note 字段中的关键英文词汇
 * pacing_note 是 LLM 生成的英文文本，直接翻译其中常见的关键词
 */
export function translatePacingNote(note: string | undefined): string {
  if (!note) return note ?? "";

  // 常见关键词映射
  const wordMap: Record<string, string> = {
    relaxed: "宽松",
    balanced: "均衡",
    dense: "紧凑",
    morning: "上午",
    afternoon: "下午",
    evening: "傍晚",
    metro: "地铁",
    walking: "步行",
    transfer: "换乘",
    presentation: "演示",
    demo: "演示",
    hotel: "酒店",
    Confucius: "夫子庙",
    Xinjiekou: "新街口",
    buffer: "缓冲",
    priority: "优先",
    central: "中心",
    "city center": "市中心",
    "route": "路线",
    "routes": "路线",
  };

  let result = note;
  // 逐词替换（不区分大小写）
  for (const [eng, chn] of Object.entries(wordMap)) {
    const regex = new RegExp(eng, "gi");
    result = result.replace(regex, chn);
  }
  return result;
}
