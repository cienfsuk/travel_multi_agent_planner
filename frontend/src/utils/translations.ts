export const TAG_TRANSLATIONS: Record<string, string> = {
  culture: "文化",
  food: "美食",
  nature: "自然",
  history: "历史",
  photography: "摄影",
  citywalk: "城市漫游",
  shopping: "购物",
  nightlife: "夜生活",
  "shopping-mall": "购物中心",
  "theme-park": "主题公园",
  intercity: "城际交通",
  taxi: "打车",
  metro: "地铁",
  bus: "公交",
  walk: "步行",
};

export const AREA_TRANSLATIONS: Record<string, string> = {
  "Nanjing City Center": "南京市中心",
  "Qinhuai River Area": "秦淮河区域",
  "Zhonghua Gate Area": "中华门区域",
  Wuchang: "武昌",
  Hankou: "汉口",
  Hanyang: "汉阳",
  Xincheng: "新城",
  Laoshan: "崂山",
  Shinan: "市南",
  Shibei: "市北",
  Chengyang: "城阳",
  Licang: "李沧",
  Huangdao: "黄岛",
  "Guangzhou City Center": "广州市中心",
  Tianhe: "天河",
  Yuexiu: "越秀",
  Liwan: "荔湾",
  Haizhu: "海珠",
  "Shanghai City Center": "上海市中心",
  Huangpu: "黄浦",
  Xuhui: "徐汇",
  Changning: "长宁",
  Putuo: "普陀",
  Hongkou: "虹口",
  Yangpu: "杨浦",
  Pudong: "浦东",
  "Beijing City Center": "北京市中心",
  Dongcheng: "东城",
  Xicheng: "西城",
  Chaoyang: "朝阳",
  Haidian: "海淀",
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
  relaxed: "轻松",
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

export const SEGMENT_TYPE_TRANSLATIONS: Record<string, string> = {
  intercity: "高铁/飞机/城际",
  taxi: "打车",
  metro: "地铁",
  bus: "公交",
  walk: "步行",
};

export const EVIDENCE_TYPE_TRANSLATIONS: Record<string, string> = {
  "web search": "网页检索",
  API: "API",
  database: "数据库",
};

export const NOTE_STYLE_TRANSLATIONS: Record<string, string> = {
  小红书风格: "小红书风格",
  xiaohongshu: "小红书风格",
  budget: "预算友好",
  citywalk: "城市漫游",
};

export const PATCH_OPERATION_TRANSLATIONS: Record<string, string> = {
  create: "新建",
  modify: "修改",
  delete: "删除",
  replace: "替换",
};

export const MODIFICATION_TYPE_TRANSLATIONS: Record<string, string> = {
  code: "代码",
  config: "配置",
  agent: "智能体",
  new_module: "新模块",
};

export const RISK_LEVEL_TRANSLATIONS: Record<string, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

export const ROUTE_STATUS_TRANSLATIONS: Record<string, string> = {
  ok: "正常",
  partial: "部分成功",
  failed: "失败",
  missing: "缺失",
};

export const AGENT_STATUS_TRANSLATIONS: Record<string, string> = {
  ok: "正常",
  fallback: "降级",
  warning: "警告",
};

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
  return seasons.map((season) => SEASON_TRANSLATIONS[season] ?? season);
}

export function translateStyle(style: string | undefined): string {
  if (!style) return "";
  return STYLE_TRANSLATIONS[style] ?? style;
}

export function translateBudgetPref(pref: string | undefined): string {
  if (!pref) return "";
  return BUDGET_PREF_TRANSLATIONS[pref] ?? pref;
}

export function translateBestTime(time: string | undefined): string {
  if (!time) return "";
  return BEST_TIME_TRANSLATIONS[time] ?? time;
}

export function translateSegmentType(type: string | undefined): string {
  if (!type) return "";
  return SEGMENT_TYPE_TRANSLATIONS[type] ?? type;
}

export function translateEvidenceType(type: string | undefined): string {
  if (!type) return "";
  return EVIDENCE_TYPE_TRANSLATIONS[type] ?? type;
}

export function translateNoteStyle(style: string | undefined): string {
  if (!style) return "";
  return NOTE_STYLE_TRANSLATIONS[style] ?? style;
}

export function translatePatchOperation(operation: string | undefined): string {
  if (!operation) return "未知操作";
  return PATCH_OPERATION_TRANSLATIONS[operation] ?? operation;
}

export function translateModificationType(type: string | undefined): string {
  if (!type) return "代码";
  return MODIFICATION_TYPE_TRANSLATIONS[type] ?? type;
}

export function translateRiskLevel(level: string | undefined): string {
  if (!level) return "未知风险";
  return RISK_LEVEL_TRANSLATIONS[level] ?? level;
}

export function translateRouteStatus(status: string | undefined): string {
  if (!status) return "未知";
  return ROUTE_STATUS_TRANSLATIONS[status] ?? status;
}

export function translateAgentStatus(status: string | undefined): string {
  if (!status) return "正常";
  return AGENT_STATUS_TRANSLATIONS[status] ?? status;
}

export function formatDistanceKm(distance: number | undefined | null): string {
  if (typeof distance !== "number" || Number.isNaN(distance)) {
    return "0.0 公里";
  }
  return `${distance.toFixed(1)} 公里`;
}

export function formatDurationMinutes(minutes: number | undefined | null): string {
  if (typeof minutes !== "number" || Number.isNaN(minutes)) {
    return "0 分钟";
  }
  return `${Math.max(0, Math.round(minutes))} 分钟`;
}

export function translatePacingNote(note: string | undefined): string {
  if (!note) return "";

  const wordMap: Record<string, string> = {
    relaxed: "轻松",
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
    buffer: "缓冲",
    priority: "优先",
    central: "中心",
    "city center": "市中心",
    route: "路线",
    routes: "路线",
  };

  let result = note;
  for (const [english, chinese] of Object.entries(wordMap)) {
    result = result.replace(new RegExp(english, "gi"), chinese);
  }
  return result;
}
