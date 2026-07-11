/**
 * tesstaiwan-analytics Worker
 *
 * Cron: 每天凌晨 2 點（UTC+8 = 18:00 UTC 前一天）
 *   wrangler.toml 設定的是 UTC 時間，台灣時間凌晨 2 點 = UTC 18:00
 *
 * 功能: 從 Cloudflare CDN Analytics API 抓取昨日頁面流量，累積存入 KV
 *
 * Secrets（用 wrangler secret put 設定）:
 *   ANALYTICS_TOKEN  - Cloudflare API token（需 Zone Analytics:Read 權限）
 *   CF_ZONE_ID       - Zone ID
 *
 * KV Binding（wrangler.toml 設定）:
 *   ANALYTICS_KV     - Workers KV namespace
 */

const CF_GRAPHQL = "https://api.cloudflare.com/client/v4/graphql";
const KV_KEY = "analytics-cache";
const KEEP_DAYS = 90; // 保留近 90 天，提供更長期的排名參考

// 過濾非頁面路徑
const SKIP_EXTENSIONS = new Set([
  ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp",
  ".svg", ".ico", ".woff", ".woff2", ".ttf", ".otf",
  ".map", ".xml", ".json", ".txt", ".php", ".zip",
]);
const SKIP_PREFIXES = [
  "/wp-", "/feed/", "/xmlrpc", "/sitemap", "/?", "/page/", "/wp-json/",
];

// 判斷路徑是否為「文章/頁面」瀏覽，排除靜態資源與後台路徑
function isPagePath(path) {
  // 抓路徑最後一段的副檔名（若有），例如 /a/b.jpg?x=1 → .jpg
  const m = path.match(/(\.[^/?#]+)(\?|#|$)/);
  if (m && SKIP_EXTENSIONS.has(m[1].toLowerCase())) return false;
  return !SKIP_PREFIXES.some((p) => path.startsWith(p));
}

const TAIWAN_OFFSET_MS = 8 * 60 * 60 * 1000;

function getDateString(daysAgo = 0) {
  // 先加 8 小時換算成台灣時間視角，再用 UTC 方法取整/減天數，等同於用台灣曆日計算
  const d = new Date(Date.now() + TAIWAN_OFFSET_MS);
  d.setUTCHours(0, 0, 0, 0);
  d.setUTCDate(d.getUTCDate() - daysAgo);
  return d.toISOString().split("T")[0];
}

// 呼叫 Cloudflare GraphQL Analytics API，取得指定單一日期(date)每個路徑的瀏覽次數
// 回傳 { [path]: visits }；若該日期已超出 Cloudflare 保留期則回傳 null
async function fetchCDNDay(token, zoneId, date) {
  const query = `{
    viewer {
      zones(filter: {zoneTag: "${zoneId}"}) {
        httpRequestsAdaptiveGroups(
          filter: {
            date_geq: "${date}", date_leq: "${date}",
            requestSource: "eyeball",
            clientRequestHTTPMethodName: "GET"
          },
          limit: 10000,
          orderBy: [date_ASC]
        ) {
          dimensions { date clientRequestPath }
          sum { visits }
        }
      }
    }
  }`;

  const resp = await fetch(CF_GRAPHQL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ query }),
  });

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  }

  const data = await resp.json();
  const errors = Array.isArray(data.errors) ? data.errors : [];

  if (errors.length) {
    const msg = errors[0]?.message || "";
    if (msg.includes("older than") || msg.includes("wider than")) {
      return null; // 資料已超出保留期
    }
    throw new Error(`GraphQL error: ${msg}`);
  }

  const records =
    data?.data?.viewer?.zones?.[0]?.httpRequestsAdaptiveGroups ?? [];

  const result = {};
  for (const r of records) {
    const path = r.dimensions.clientRequestPath;
    if (!isPagePath(path)) continue;
    result[path] = r.sum.visits;
  }
  return result;
}

export default {
  // Cron 觸發進入點：抓昨日流量、寫入 KV、清除過期資料
  async scheduled(event, env, ctx) {
    // getDateString 內部已換算成台灣時間視角，這裡取的就是台灣時間的昨天
    const yesterday = getDateString(1);
    // 早於這個日期的資料視為過期，會在下面被清除
    const cutoffDate = getDateString(KEEP_DAYS);

    console.log(`[Analytics] 抓取 ${yesterday} 的資料...`);

    // 抓昨日 CDN 流量
    let dayData;
    try {
      dayData = await fetchCDNDay(env.ANALYTICS_TOKEN, env.CF_ZONE_ID, yesterday);
    } catch (err) {
      console.error(`[Analytics] API 錯誤: ${err.message}`);
      return;
    }

    if (dayData === null) {
      console.log(`[Analytics] ${yesterday} 資料已過期，跳過`);
      return;
    }

    const pathCount = Object.keys(dayData).length;
    console.log(`[Analytics] 取得 ${pathCount} 個頁面路徑`);

    // 讀取現有 KV 資料
    const existing = (await env.ANALYTICS_KV.get(KV_KEY, "json")) ?? {
      last_updated: null,
      data: {},
    };

    // 合併：新資料覆蓋同日舊資料（當日多次觸發時取最新值）
    existing.data[yesterday] = dayData;
    existing.last_updated = yesterday;

    // 清除超過 90 天的資料
    for (const date of Object.keys(existing.data)) {
      if (date < cutoffDate) {
        delete existing.data[date];
      }
    }

    const totalDays = Object.keys(existing.data).length;
    await env.ANALYTICS_KV.put(KV_KEY, JSON.stringify(existing));
    console.log(`[Analytics] KV 更新完成，共 ${totalDays} 天資料`);
  },
};
