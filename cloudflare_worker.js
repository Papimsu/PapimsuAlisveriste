/**
 * Cloudflare Worker for Fiyat Radarı (Papimsu Alışverişte)
 *
 * S1: Webhook secret token doğrulaması
 * S2: Dinamik ALLOWED_CHAT_IDS
 * C1: Strict regex, Türkçe sayı dönüştürme, pending_${chatId}_${fromId}
 * C4: GitHub API 409/422 retry (3 deneme) ve kullanıcı hata bildirimleri
 * R3: Dead man's switch (scheduled trigger)
 * R4: /liste parçalama (chunking <= 3800), HTML escape
 * R5: Telegram webhook'una daima HTTP 200 garantisi
 * R6: Sadece watchlist.json (Config) dosyasına yazım
 * A7: Desteklenen URL doğrulaması (Amazon TR, Trendyol, Hepsiburada), deduplication, /sil iyileştirmesi
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK", { status: 200 });
    }

    // S1: Webhook secret_token doğrulaması
    const webhookSecret = env.WEBHOOK_SECRET;
    if (webhookSecret) {
      const secretHeader = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
      if (!timingSafeEqual(secretHeader, webhookSecret)) {
        return new Response("Forbidden: Invalid secret token", { status: 403 });
      }
    }

    try {
      const update = await request.json();
      await handleTelegramUpdate(update, env);
    } catch (err) {
      console.error("Telegram update işlenirken hata:", err);
    }

    // R5: Telegram webhook'una daima HTTP 200 dön (redelivery storm önleme)
    return new Response("OK", { status: 200 });
  },

  async scheduled(event, env, ctx) {
    try {
      await checkDeadMansSwitch(env);
    } catch (err) {
      console.error("Scheduled check hatası:", err);
    }
  }
};

// ==========================================
// ANA GÜNCELLEME YÖNETİCİSİ
// ==========================================
async function handleTelegramUpdate(update, env) {
  const message = update.message;
  if (!message || !message.text) return;

  const chatId = String(message.chat.id);
  const fromId = String(message.from?.id || "unknown");
  const messageId = message.message_id;
  const replyToId = message.reply_to_message?.message_id;
  const text = message.text.trim();

  // S2: Dinamik Chat ID Yetkilendirmesi
  const rawAllowed = env.ALLOWED_CHAT_IDS || "";
  const allowedChats = rawAllowed.split(",").map((id) => id.trim()).filter(Boolean);
  if (allowedChats.length === 0 || !allowedChats.includes(chatId)) {
    console.warn(`Yetkisiz Chat ID: ${chatId}`);
    return;
  }

  // Komut: /start veya /yardim
  if (text.startsWith("/start") || text.startsWith("/yardim")) {
    const welcome =
      "🤖 <b>Fiyat Radarı Botu Hazır!</b>\n\n" +
      "Desteklenen Platformlar: Amazon TR, Trendyol, Hepsiburada\n\n" +
      "📌 <b>Nasıl kullanılır?</b>\n" +
      "Bir ürün linkini gruba atın. Bot sizden hedef fiyat veya indirim yüzdesi isteyecektir.\n\n" +
      "<b>Komutlar:</b>\n" +
      "📋 <code>/liste</code> - Takipteki ürünleri listeler\n" +
      "🗑️ <code>/sil &lt;no veya id&gt;</code> - Ürünü takipten çıkarır (Örn: <code>/sil 1</code>)";
    await sendTelegram(env.BOT_TOKEN, chatId, welcome, { replyToMessageId: messageId, parseMode: "HTML" });
    return;
  }

  // Komut: /liste (R4: Sayfalama & HTML)
  if (text.startsWith("/liste")) {
    await handleListCommand(env, chatId, messageId);
    return;
  }

  // Komut: /sil (A7: No veya ID ile silme & onay)
  if (text.startsWith("/sil")) {
    await handleDeleteCommand(env, chatId, messageId, text);
    return;
  }

  // 1. ADIM: Soruya Yanıt (Fiyat veya İndirim Kuralı Geldi mi?)
  const isUrl = /https?:\/\//i.test(text);
  const sessionKey = `pending_${chatId}_${fromId}`;
  const pendingRaw = env.SESSIONS ? await env.SESSIONS.get(sessionKey) : null;

  if (!isUrl && pendingRaw) {
    const parsedRule = parseUserInputPrice(text);
    const pending = JSON.parse(pendingRaw);
    const isDirectReply = replyToId && pending.promptMessageId === replyToId;

    if (parsedRule && !parsedRule.error) {
      // Geçerli kural girildi -> Watchlist'e kaydet
      await finalizeProductRule(env, chatId, messageId, sessionKey, pending, parsedRule);
      return;
    } else if (isDirectReply) {
      // Doğrudan bota yanıt verildi ama format hatalı
      const errReason = parsedRule?.error === "invalid_range"
        ? "⚠️ Belirtilen fiyat veya yüzde geçerli aralıkta değil (Fiyat: 1 - 10.000.000 TL, Yüzde: %1 - %95)."
        : "❓ Lütfen sadece hedef fiyat (örn: <code>18000</code>) veya yüzde indirim (örn: <code>%30</code>) belirtin.";
      await sendTelegram(env.BOT_TOKEN, chatId, errReason, { replyToMessageId: messageId, parseMode: "HTML" });
      return;
    } else {
      // Normal sohbet konuşması (örn: "50 dakika sonra gelirim") -> Yok say, kural olarak algılama!
      return;
    }
  }

  // 2. ADIM: Yeni Ürün Linki Geldi
  if (isUrl) {
    await handleProductUrl(env, chatId, fromId, messageId, text);
    return;
  }
}

// ==========================================
// KOMUT VE AKIŞ İŞLEYİCİLERİ
// ==========================================

async function handleListCommand(env, chatId, messageId) {
  const items = await getWatchlist(env);
  if (!items || items.length === 0) {
    await sendTelegram(env.BOT_TOKEN, chatId, "📋 Takip listenizde henüz ürün bulunmuyor.", { replyToMessageId: messageId });
    return;
  }

  // State dosyasından son fiyatları al (R6)
  const state = await getState(env);

  let resp = "📋 <b>Takipteki Ürünler:</b>\n\n";
  items.forEach((item, idx) => {
    let displayTitle = htmlEscape(item.name || "Ürün");
    if (displayTitle.length > 60) {
      displayTitle = displayTitle.substring(0, 57) + "...";
    }

    let targetInfo = "";
    if (item.target_price) {
      targetInfo = `🎯 Hedef: ${Number(item.target_price).toLocaleString("tr-TR")} TL`;
    } else if (item.discount_threshold_percent) {
      targetInfo = `🎯 Eşik: %${item.discount_threshold_percent} İndirim`;
    }

    const itemState = state[item.id] || {};
    let currentInfo = "";
    if (itemState.last_price && Number(itemState.last_price) > 0) {
      currentInfo = ` | 💰 Güncel: ${Number(itemState.last_price).toLocaleString("tr-TR")} TL`;
    } else {
      currentInfo = " | 💰 Güncel: Taranıyor...";
    }

    resp += `<b>${idx + 1}.</b> <a href="${htmlEscape(item.url)}">${displayTitle}</a>\n`;
    resp += `   <code>[ID: ${item.id}]</code>\n`;
    resp += `   ${targetInfo}${currentInfo}\n\n`;
  });

  resp += "💡 <i>Silmek için: /sil &lt;no&gt; (Örn: /sil 1)</i>";

  // R4: Mesaj uzunluğu kontrolü ve parçalama
  const chunks = chunkMessage(resp, 3800);
  for (const chunk of chunks) {
    await sendTelegram(env.BOT_TOKEN, chatId, chunk, { parseMode: "HTML", disablePreview: true });
  }
}

async function handleDeleteCommand(env, chatId, messageId, text) {
  const parts = text.split(/\s+/);
  const target = parts[1]?.trim();

  if (!target) {
    await sendTelegram(env.BOT_TOKEN, chatId, "❌ Lütfen silinecek ürün numarasını veya ID'sini belirtin.\nÖrnek: <code>/sil 1</code> veya <code>/sil d8wk9m6x</code>", {
      replyToMessageId: messageId,
      parseMode: "HTML"
    });
    return;
  }

  const { items } = await getWatchlistWithSha(env);
  if (!items || items.length === 0) {
    await sendTelegram(env.BOT_TOKEN, chatId, "❌ Takip listesi boş.", { replyToMessageId: messageId });
    return;
  }

  let removeIndex = -1;
  const num = parseInt(target, 10);
  if (!isNaN(num) && num >= 1 && num <= items.length) {
    removeIndex = num - 1;
  } else {
    removeIndex = items.findIndex((i) => i.id === target);
  }

  if (removeIndex === -1) {
    await sendTelegram(env.BOT_TOKEN, chatId, `❌ '${htmlEscape(target)}' ile eşleşen ürün bulunamadı. Güncel numaralar için <code>/liste</code> yazabilirsiniz.`, {
      replyToMessageId: messageId,
      parseMode: "HTML"
    });
    return;
  }

  const removed = items.splice(removeIndex, 1)[0];
  const saveResult = await saveWatchlistWithRetry(env, items, "Remove item via Telegram");

  if (!saveResult.success) {
    await sendTelegram(env.BOT_TOKEN, chatId, `⚠️ Ürün silinemedi (GitHub API Hatası: ${saveResult.status || saveResult.error}). Lütfen tekrar deneyin.`, {
      replyToMessageId: messageId
    });
    return;
  }

  const successMsg = `🗑️ <b>${htmlEscape(removed.name || "Ürün")}</b> başarıyla takip listesinden çıkarıldı.`;
  await sendTelegram(env.BOT_TOKEN, chatId, successMsg, { replyToMessageId: messageId, parseMode: "HTML" });
}

async function handleProductUrl(env, chatId, fromId, messageId, text) {
  const urlMatch = text.match(/https?:\/\/[^\s]+/i);
  if (!urlMatch) return;

  const rawUrl = urlMatch[0];
  const validated = await validateAndCleanUrl(rawUrl);

  if (validated.error === "unsupported_amazon") {
    await sendTelegram(env.BOT_TOKEN, chatId, "⚠️ Sadece <b>Amazon Türkiye (amazon.com.tr)</b> desteklenmektedir. Yabancı Amazon siteleri desteklenmez.", {
      replyToMessageId: messageId,
      parseMode: "HTML"
    });
    return;
  }

  if (validated.error) {
    await sendTelegram(env.BOT_TOKEN, chatId, "⚠️ Desteklenmeyen link! Yalnızca <b>Amazon TR</b>, <b>Trendyol</b> ve <b>Hepsiburada</b> ürünleri takip edilebilir.", {
      replyToMessageId: messageId,
      parseMode: "HTML"
    });
    return;
  }

  const cleanUrl = validated.url;

  // Watchlist'te zaten var mı kontrolü (A7 Deduplication)
  const existingItems = await getWatchlist(env);
  const duplicate = existingItems.find((i) => i.url === cleanUrl);
  if (duplicate) {
    await sendTelegram(env.BOT_TOKEN, chatId, `ℹ️ Bu ürün zaten takip listenizde kayıtlı:\n<b>${htmlEscape(duplicate.name)}</b>`, {
      replyToMessageId: messageId,
      parseMode: "HTML"
    });
    return;
  }

  // Paylaşılan mesajda ürün başlığı varsa yakala
  let capturedTitle = "Ürün";
  const lines = text.split("\n");
  for (const line of lines) {
    const tr = line.trim();
    if (tr && !tr.startsWith("http") && tr.length > 5) {
      capturedTitle = tr;
      break;
    }
  }

  const promptMsg =
    "📦 <b>Ürün Linki Alındı!</b>\n\n" +
    `🔗 <b>Link:</b> ${htmlEscape(cleanUrl)}\n\n` +
    "Nasıl bir alarm kuralı koymak istersiniz?\n\n" +
    "🔹 <b>Hedef Fiyat:</b> Sadece rakam yazın (Örn: <code>18000</code>)\n" +
    "🔹 <b>İndirim Yüzdesi:</b> Yüzde yazın (Örn: <code>%30</code>)\n\n" +
    "<i>(Yanıtınızı bu mesaja reply yaparak veya doğrudan yazabilirsiniz)</i>";

  const sendRes = await sendTelegram(env.BOT_TOKEN, chatId, promptMsg, { replyToMessageId: messageId, parseMode: "HTML" });
  const sentData = sendRes?.result;
  const promptMessageId = sentData?.message_id || null;

  // C1: pending_${chatId}_${fromId} anahtarı ile sakla
  if (env.SESSIONS) {
    const sessionKey = `pending_${chatId}_${fromId}`;
    await env.SESSIONS.put(
      sessionKey,
      JSON.stringify({
        url: cleanUrl,
        title: capturedTitle,
        promptMessageId: promptMessageId
      }),
      { expirationTtl: 600 }
    );
  }
}

async function finalizeProductRule(env, chatId, messageId, sessionKey, pending, rule) {
  const { items } = await getWatchlistWithSha(env);

  // R6: Sadece config alanlarını yaz
  const newItem = {
    id: Math.random().toString(36).substring(2, 10),
    url: pending.url,
    name: pending.title || "Ürün",
    target_price: rule.targetPrice,
    discount_threshold_percent: rule.discountPercent,
    active: true,
    created_at: new Date().toISOString()
  };

  items.push(newItem);
  const saveResult = await saveWatchlistWithRetry(env, items, "Add product via Telegram");

  if (!saveResult.success) {
    await sendTelegram(env.BOT_TOKEN, chatId, `⚠️ Ürün kaydedilemedi (GitHub API Hatası: ${saveResult.status || saveResult.error}). Lütfen tekrar deneyin.`, {
      replyToMessageId: messageId
    });
    return;
  }

  if (env.SESSIONS) {
    await env.SESSIONS.delete(sessionKey);
  }

  const ruleDesc = rule.targetPrice
    ? `🎯 Hedef Fiyat: <b>${rule.targetPrice.toLocaleString("tr-TR")} TL</b>`
    : `🎯 İndirim Eşiği: <b>%${rule.discountPercent} İndirim</b>`;

  const msg =
    "✅ <b>Ürün Takibe Alındı!</b>\n\n" +
    `📦 <b>Ürün:</b> ${htmlEscape(newItem.name)}\n` +
    `${ruleDesc}\n` +
    `🔗 <b>Link:</b> <a href="${htmlEscape(pending.url)}">Ürünü Gör</a>\n\n` +
    "🚀 Fiyat bu seviyeye indiğinde gruptan anında bildirim gönderilecektir!";

  await sendTelegram(env.BOT_TOKEN, chatId, msg, { replyToMessageId: messageId, parseMode: "HTML", disablePreview: true });
}

// ==========================================
// R3: DEAD MAN'S SWITCH KONTROLÜ
// ==========================================
async function checkDeadMansSwitch(env) {
  if (!env.GITHUB_REPO || !env.GITHUB_TOKEN || !env.BOT_TOKEN || !env.ALLOWED_CHAT_IDS) return;

  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/check_prices.yml/runs?per_page=1`;
  const res = await fetch(url, {
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "Cloudflare-Worker"
    }
  });

  if (!res.ok) return;
  const data = await res.json();
  const runs = data.workflow_runs || [];
  if (runs.length === 0) return;

  const lastRun = runs[0];
  const lastTime = new Date(lastRun.updated_at || lastRun.created_at).getTime();
  const diffMinutes = Math.round((Date.now() - lastTime) / (1000 * 60));

  if (diffMinutes > 75) {
    const alreadyAlerted = env.SESSIONS ? await env.SESSIONS.get("dms_alert_sent") : null;
    if (!alreadyAlerted) {
      const primaryChat = env.ALLOWED_CHAT_IDS.split(",")[0].trim();
      const warnMsg =
        "⚠️ <b>SİSTEM UYARISI: Fiyat Kontrolü Durmuş Olabilir!</b>\n\n" +
        `GitHub Actions en son <b>${diffMinutes} dakika önce</b> çalıştı (Beklenen: her 15 dk).\n` +
        `Durum: <code>${lastRun.status} (${lastRun.conclusion || "devam ediyor"})</code>\n\n` +
        "Lütfen GitHub Actions iş akışını kontrol edin.";

      await sendTelegram(env.BOT_TOKEN, primaryChat, warnMsg, { parseMode: "HTML" });
      if (env.SESSIONS) {
        await env.SESSIONS.put("dms_alert_sent", "1", { expirationTtl: 86400 });
      }
    }
  } else {
    // Düzelme durumu: daha önce uyarı atılmışsa ve şimdi son çalışma <= 75 dk ise düzelme mesajı at
    const alreadyAlerted = env.SESSIONS ? await env.SESSIONS.get("dms_alert_sent") : null;
    if (alreadyAlerted) {
      const primaryChat = env.ALLOWED_CHAT_IDS.split(",")[0].trim();
      const recMsg =
        "✅ <b>SİSTEM NORMALE DÖNDÜ:</b> Fiyat kontrol otomasyonu (GitHub Actions) yeniden çalışmaya başladı.\n" +
        `Son başarılı çalışma: <b>${diffMinutes} dakika önce</b>.`;
      await sendTelegram(env.BOT_TOKEN, primaryChat, recMsg, { parseMode: "HTML" });
      if (env.SESSIONS) {
        await env.SESSIONS.delete("dms_alert_sent");
      }
    }
  }
}

// ==========================================
// DOĞRULAMA VE PARSE YARDIMCILARI
// ==========================================

function htmlEscape(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const aLen = a.length;
  const bLen = b.length;
  let diff = aLen ^ bLen;
  for (let i = 0; i < Math.max(aLen, bLen); i++) {
    const charA = i < aLen ? a.charCodeAt(i) : 0;
    const charB = i < bLen ? b.charCodeAt(i) : 0;
    diff |= charA ^ charB;
  }
  return diff === 0;
}

function parseUserInputPrice(raw) {
  if (!raw || typeof raw !== "string") return null;
  const trimmed = raw.trim();

  // Strict regex: sadece sayı veya yüzde ifadesi
  const strictPattern = /^\s*(%?\s*\d[\d.,\s]*\s*%?)\s*$/;
  if (!strictPattern.test(trimmed)) {
    return null;
  }

  const isPercent = trimmed.includes("%");
  const cleaned = trimmed.replace(/%/g, "").trim();

  // Türkçe / Uluslararası sayı biçimlendirmesi
  let numVal = null;
  if (cleaned.includes(",") && cleaned.includes(".")) {
    if (cleaned.lastIndexOf(",") > cleaned.lastIndexOf(".")) {
      // 1.250,50 -> TR
      numVal = parseFloat(cleaned.replace(/\./g, "").replace(",", "."));
    } else {
      // 1,250.50 -> EN
      numVal = parseFloat(cleaned.replace(/,/g, ""));
    }
  } else if (cleaned.includes(",")) {
    const parts = cleaned.split(",");
    if (parts.length > 2 || parts[parts.length - 1].length === 3) {
      numVal = parseFloat(cleaned.replace(/,/g, ""));
    } else {
      numVal = parseFloat(cleaned.replace(",", "."));
    }
  } else if (cleaned.includes(".")) {
    const parts = cleaned.split(".");
    if (parts.length > 2 || parts[parts.length - 1].length === 3) {
      numVal = parseFloat(cleaned.replace(/\./g, ""));
    } else {
      numVal = parseFloat(cleaned);
    }
  } else {
    numVal = parseFloat(cleaned.replace(/\s+/g, ""));
  }

  if (isNaN(numVal)) {
    return { error: "invalid_format" };
  }

  if (isPercent) {
    if (numVal < 1 || numVal > 95) {
      return { error: "invalid_range" };
    }
    return { targetPrice: null, discountPercent: Math.round(numVal * 10) / 10, error: null };
  } else {
    if (numVal < 1 || numVal > 10_000_000) {
      return { error: "invalid_range" };
    }
    return { targetPrice: Math.round(numVal * 100) / 100, discountPercent: null, error: null };
  }
}

async function validateAndCleanUrl(rawUrl) {
  try {
    let target = rawUrl;
    // Mobil kısaltmalar (amzn.eu, ty.gl, app.hb.biz) çözümlenmesi
    if (/amzn\.eu|ty\.gl|app\.hb\.biz/i.test(target)) {
      try {
        const resp = await fetch(target, { redirect: "follow", headers: { "User-Agent": "Mozilla/5.0" } });
        if (resp && resp.url) target = resp.url;
      } catch (e) {
        // Çözümlenemezse orijinal ile devam
      }
    }

    const parsed = new URL(target);
    const host = parsed.hostname.toLowerCase();

    // Amazon kontrolleri
    if (host.includes("amazon.")) {
      if (!host.endsWith("amazon.com.tr")) {
        return { error: "unsupported_amazon" };
      }
      const asinMatch = parsed.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
      if (asinMatch) {
        return { url: `https://www.amazon.com.tr/dp/${asinMatch[1]}`, platform: "Amazon" };
      }
      return { url: `https://www.amazon.com.tr${parsed.pathname}`, platform: "Amazon" };
    }

    // Trendyol kontrolleri
    if (host.includes("trendyol.com")) {
      const cleanPath = parsed.pathname.replace(/\/en\//g, "/");
      return { url: `https://www.trendyol.com${cleanPath}`, platform: "Trendyol" };
    }

    // Hepsiburada kontrolleri
    if (host.includes("hepsiburada.com")) {
      return { url: `https://www.hepsiburada.com${parsed.pathname}`, platform: "Hepsiburada" };
    }

    return { error: "unsupported_domain" };
  } catch (e) {
    return { error: "invalid_url" };
  }
}

function chunkMessage(text, maxLen = 3800) {
  if (!text || text.length <= maxLen) return [text || ""];
  const chunks = [];
  const lines = text.split("\n");
  let current = "";
  for (const line of lines) {
    if ((current + "\n" + line).length > maxLen) {
      if (current) chunks.push(current.trim());
      current = line;
    } else {
      current = current ? current + "\n" + line : line;
    }
  }
  if (current) chunks.push(current.trim());
  return chunks;
}

// ==========================================
// TELEGRAM VE GITHUB API İSTEMCİLERİ
// ==========================================

async function sendTelegram(botToken, chatId, text, options = {}) {
  const url = `https://api.telegram.org/bot${botToken}/sendMessage`;
  const body = {
    chat_id: chatId,
    text: text,
    disable_web_page_preview: options.disablePreview ?? false
  };
  if (options.replyToMessageId) {
    body.reply_to_message_id = options.replyToMessageId;
  }
  if (options.parseMode) {
    body.parse_mode = options.parseMode;
  }

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!res.ok) {
      const errTxt = await res.text();
      console.error(`Telegram send hatası (${res.status}):`, errTxt);
      return null;
    }
    return await res.json();
  } catch (err) {
    console.error("Telegram network hatası:", err);
    return null;
  }
}

function decodeUtf8Base64(base64Str) {
  const binary = atob(base64Str.replace(/\n/g, ""));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new TextDecoder("utf-8").decode(bytes);
}

function encodeUtf8Base64(str) {
  const bytes = new TextEncoder().encode(str);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

async function getWatchlistWithSha(env) {
  if (!env.GITHUB_REPO || !env.GITHUB_TOKEN) return { items: [], sha: null };
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/watchlist.json`;
  try {
    const res = await fetch(url, {
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "User-Agent": "Cloudflare-Worker"
      }
    });
    if (!res.ok) return { items: [], sha: null };
    const data = await res.json();
    const content = decodeUtf8Base64(data.content);
    return { items: JSON.parse(content), sha: data.sha };
  } catch (e) {
    console.error("getWatchlistWithSha hatası:", e);
    return { items: [], sha: null };
  }
}

async function getWatchlist(env) {
  const data = await getWatchlistWithSha(env);
  return data.items || [];
}

async function getState(env) {
  if (!env.GITHUB_REPO || !env.GITHUB_TOKEN) return {};
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/state.json`;
  try {
    const res = await fetch(url, {
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "User-Agent": "Cloudflare-Worker"
      }
    });
    if (!res.ok) return {};
    const data = await res.json();
    const content = decodeUtf8Base64(data.content);
    return JSON.parse(content);
  } catch (e) {
    return {};
  }
}

async function saveWatchlistWithRetry(env, items, commitMsg, maxRetries = 3) {
  if (!env.GITHUB_REPO || !env.GITHUB_TOKEN) {
    return { success: false, error: "GITHUB_REPO veya GITHUB_TOKEN eksik" };
  }

  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/watchlist.json`;
  const contentBase64 = encodeUtf8Base64(JSON.stringify(items, null, 2));

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    const { sha } = await getWatchlistWithSha(env);
    const body = {
      message: commitMsg,
      content: contentBase64
    };
    if (sha) body.sha = sha;

    try {
      const res = await fetch(url, {
        method: "PUT",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "User-Agent": "Cloudflare-Worker",
          "Content-Type": "application/json"
        },
        body: JSON.stringify(body)
      });

      if (res.ok) {
        return { success: true };
      }

      if (res.status === 409 || res.status === 422) {
        // Eşzamanlı çakışma durumunda kısa bekleme ve taze SHA ile tekrar deneme
        console.warn(`saveWatchlist çakışması (${res.status}), tekrar deneniyor (${attempt}/${maxRetries})...`);
        await new Promise((r) => setTimeout(r, 400 * attempt));
        continue;
      }

      const errText = await res.text();
      return { success: false, status: res.status, error: errText };
    } catch (netErr) {
      console.error("saveWatchlist ağ hatası:", netErr);
      if (attempt === maxRetries) {
        return { success: false, error: String(netErr) };
      }
    }
  }

  return { success: false, status: 409, error: "Eşzamanlı yazma çakışması aşılamadı" };
}

// Node testleri için dışa aktar
export {
  timingSafeEqual,
  parseUserInputPrice,
  validateAndCleanUrl,
  chunkMessage,
  htmlEscape,
  checkDeadMansSwitch,
  saveWatchlistWithRetry,
  handleTelegramUpdate
};
