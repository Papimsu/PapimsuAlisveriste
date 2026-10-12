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
      const message = update.message;

      if (!message || !message.text) {
        return new Response("OK", { status: 200 });
      }

      const chatId = String(message.chat.id);
      const messageId = message.message_id;
      const text = message.text.trim();

      // S2: Yetkili Chat ID'ler ortam değişkeninden (ALLOWED_CHAT_IDS) okunur
      const rawAllowed = env.ALLOWED_CHAT_IDS || "";
      const allowedChats = rawAllowed
        .split(",")
        .map((id) => id.trim())
        .filter(Boolean);

      if (allowedChats.length === 0 || !allowedChats.includes(chatId)) {
        console.warn(`Yetkisiz Chat ID engellendi: ${chatId}`);
        return new Response("OK", { status: 200 });
      }

      // /start veya /yardim
      if (text.startsWith("/start") || text.startsWith("/yardim")) {
        const welcome = 
          "🤖 Fiyat Radarı Botu Hazır!\n\n" +
          "Desteklenenler: Amazon, Trendyol, Hepsiburada\n\n" +
          "📌 Nasıl kullanılır?\n" +
          "Herhangi bir ürün linkini buraya atın! Bot reply yaparak kuralı soracaktır.\n\n" +
          "Komutlar:\n" +
          "📋 /liste - Takipteki ürünleri listeler\n" +
          "🗑️ /sil <no> - Ürünü takipten çıkarır";
        await sendTelegram(env.BOT_TOKEN, chatId, welcome, messageId, true);
        return new Response("OK", { status: 200 });
      }

      // /liste komutu (HEDEF VE GÜNCEL FİYAT BİRLİKTE GÖRÜNÜR)
      if (text.startsWith("/liste")) {
        const items = await getWatchlist(env);
        if (!items || items.length === 0) {
          await sendTelegram(env.BOT_TOKEN, chatId, "📋 Takip listenizde ürün bulunmuyor.", messageId, true);
          return new Response("OK", { status: 200 });
        }

        let resp = "📋 Takipteki Ürünler:\n\n";
        items.forEach((item, idx) => {
          let displayTitle = item.name || "Ürün";
          if (displayTitle.length > 55) {
            displayTitle = displayTitle.substring(0, 55) + "...";
          }

          let targetInfo = "";
          if (item.target_price) {
            targetInfo = `🎯 Hedef: ${Number(item.target_price).toLocaleString("tr-TR")} TL`;
          } else if (item.discount_threshold_percent) {
            targetInfo = `🎯 Hedef: %${item.discount_threshold_percent} İndirim`;
          }

          let currentInfo = "";
          if (item.last_checked_price && Number(item.last_checked_price) > 0) {
            currentInfo = ` | 💰 Güncel: ${Number(item.last_checked_price).toLocaleString("tr-TR")} TL`;
          } else {
            currentInfo = ` | 💰 Güncel: Taranıyor...`;
          }

          resp += `${idx + 1}. *${displayTitle}*\n`;
          resp += `${item.url}\n`;
          resp += `   ${targetInfo}${currentInfo}\n\n`;
        });
        
        await sendTelegram(env.BOT_TOKEN, chatId, resp, messageId, true);
        return new Response("OK", { status: 200 });
      }

      // /sil komutu
      if (text.startsWith("/sil")) {
        const parts = text.split(" ");
        const idx = parseInt(parts[1], 10) - 1;
        const fileData = await getWatchlistWithSha(env);
        if (fileData && fileData.items && idx >= 0 && idx < fileData.items.length) {
          const removed = fileData.items.splice(idx, 1)[0];
          await saveWatchlist(env, fileData.items, fileData.sha, "Remove item via Telegram");
          await sendTelegram(env.BOT_TOKEN, chatId, `🗑️ Ürün başarıyla silindi.`, messageId, true);
        } else {
          await sendTelegram(env.BOT_TOKEN, chatId, "❌ Geçersiz ürün numarası. Örnek: /sil 1", messageId, true);
        }
        return new Response("OK", { status: 200 });
      }

      // 1. ADIM: Soruya Yanıt (Fiyat veya % Cevabı Geldi)
      const isUrl = text.includes("http://") || text.includes("https://");
      const pendingRaw = await env.SESSIONS.get(`pending_${chatId}`);

      if (!isUrl && pendingRaw) {
        const pending = JSON.parse(pendingRaw);
        let targetPrice = null;
        let discountPercent = null;

        if (text.includes("%")) {
          const pct = text.replace(/[^\d]/g, "");
          if (pct) discountPercent = parseFloat(pct);
        } else {
          const digits = text.replace(/[^\d]/g, "");
          if (digits) {
            const entered = parseFloat(digits);
            targetPrice = entered * 1.05; // %5 buffer
          }
        }

        if (targetPrice === null && discountPercent === null) {
          await sendTelegram(env.BOT_TOKEN, chatId, "❓ Lütfen sadece rakam (örn: 18000) veya yüzde (örn: %30) yaz.", messageId, true);
          return new Response("OK", { status: 200 });
        }

        const fileData = await getWatchlistWithSha(env);
        const items = fileData.items || [];

        const newItem = {
          id: Math.random().toString(36).substring(2, 10),
          name: pending.title || "Ürün",
          url: pending.url,
          normal_price: 0,
          target_price: targetPrice ? Math.round(targetPrice / 1.05) : null,
          effective_target: targetPrice ? Math.round(targetPrice) : null,
          discount_threshold_percent: discountPercent,
          last_checked_price: null,
          last_alert_sent_price: null,
          active: true
        };

        items.push(newItem);
        await saveWatchlist(env, items, fileData.sha, "Add product via Telegram");
        await env.SESSIONS.delete(`pending_${chatId}`);

        const ruleDesc = targetPrice 
          ? `Hedef: ${Math.round(targetPrice / 1.05).toLocaleString("tr-TR")} TL` 
          : `Eşik: %${discountPercent} İndirim`;

        const msg = 
          "✅ Ürün Takibe Alındı!\n\n" +
          `🎯 Kural: ${ruleDesc}\n\n` +
          `📦 Link: ${pending.url}\n\n` +
          "Fiyat bu seviyeye indiğinde bu gruba 7/24 anında alarm çalacak! 🚀";

        await sendTelegram(env.BOT_TOKEN, chatId, msg, messageId, false);
        return new Response("OK", { status: 200 });
      }

      // 2. ADIM: Link Geldi (Amazon, Trendyol, Hepsiburada)
      const urlMatch = text.match(/https?:\/\/[^\s]*(?:amazon\.[^\s]+|amzn\.eu\/[^\s]+|trendyol\.com\/[^\s]+|ty\.gl\/[^\s]+|hepsiburada\.com\/[^\s]+|app\.hb\.biz\/[^\s]+)/i);
      if (urlMatch) {
        let rawUrl = urlMatch[0];
        let targetUrl = rawUrl;

        if (rawUrl.includes("amzn.eu") || rawUrl.includes("ty.gl") || rawUrl.includes("app.hb.biz")) {
          try {
            const redirectResp = await fetch(rawUrl, { redirect: "follow", headers: { "User-Agent": "Mozilla/5.0" } });
            targetUrl = redirectResp.url;
          } catch (e) {
            targetUrl = rawUrl;
          }
        }

        let cleanUrl = targetUrl.split("?")[0];
        const asinMatch = targetUrl.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
        if (asinMatch) {
          cleanUrl = `https://www.amazon.com.tr/dp/${asinMatch[1]}`;
        }

        // Paylaşılan mesajda ürün adı varsa al
        let capturedTitle = "Ürün";
        const lines = text.split("\n");
        for (const line of lines) {
          const tr = line.trim();
          if (tr && !tr.startsWith("http") && tr.length > 5) {
            capturedTitle = tr;
            break;
          }
        }

        await env.SESSIONS.put(`pending_${chatId}`, JSON.stringify({ url: cleanUrl, title: capturedTitle }), { expirationTtl: 600 });

        const askMsg = 
          "📦 Ürün Linki Alındı!\n\n" +
          `Link: ${cleanUrl}\n\n` +
          "Nasıl bir alarm kuralı koymak istersin?\n\n" +
          "🔹 Hedef Fiyat için: Sadece rakam yaz (Örn: 18000)\n" +
          "🔹 Yüzde İndirim için: Yüzde yaz (Örn: %30)\n\n" +
          "(Küsürat girmenize gerek yoktur)";

        await sendTelegram(env.BOT_TOKEN, chatId, askMsg, messageId, false);
        return new Response("OK", { status: 200 });
      }

      return new Response("OK", { status: 200 });
    } catch (err) {
      return new Response("Error: " + err.message, { status: 500 });
    }
  }
};

async function sendTelegram(botToken, chatId, text, replyToMessageId = null, disablePreview = false) {
  const url = `https://api.telegram.org/bot${botToken}/sendMessage`;
  const body = {
    chat_id: chatId,
    text: text,
    disable_web_page_preview: disablePreview
  };
  if (replyToMessageId) {
    body.reply_to_message_id = replyToMessageId;
  }
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
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
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/watchlist.json`;
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
}

async function getWatchlist(env) {
  const data = await getWatchlistWithSha(env);
  return data.items;
}

async function saveWatchlist(env, items, sha, commitMsg) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/watchlist.json`;
  const contentBase64 = encodeUtf8Base64(JSON.stringify(items, null, 2));
  const body = {
    message: commitMsg,
    content: contentBase64
  };
  if (sha) body.sha = sha;

  await fetch(url, {
    method: "PUT",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "Cloudflare-Worker",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  });
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

export { timingSafeEqual };
