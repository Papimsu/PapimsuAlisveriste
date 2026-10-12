import test from "node:test";
import assert from "node:assert/strict";
import worker, {
  timingSafeEqual,
  parseUserInputPrice,
  validateAndCleanUrl,
  chunkMessage,
  htmlEscape,
  checkDeadMansSwitch,
  saveWatchlistWithRetry,
  handleTelegramUpdate
} from "../cloudflare_worker.js";

// Mock KV Helper
function createMockKV(initialData = {}) {
  const store = new Map(Object.entries(initialData));
  let getCalls = 0;
  let putCalls = 0;
  let deleteCalls = 0;

  return {
    async get(key) {
      getCalls++;
      return store.has(key) ? store.get(key) : null;
    },
    async put(key, value, opts) {
      putCalls++;
      store.set(key, String(value));
    },
    async delete(key) {
      deleteCalls++;
      store.delete(key);
    },
    has(key) {
      return store.has(key);
    },
    getCalls: () => getCalls,
    putCalls: () => putCalls,
    deleteCalls: () => deleteCalls
  };
}

// ==========================================
// S1 TESTLERİ: Webhook Secret Token & Yetki
// ==========================================
test("S1: Worker fetch handler başlıksız ve yanlış başlıklı istekte 403 dönüyor, KV ve GitHub çağrılmıyor", async () => {
  const mockKV = createMockKV();
  let fetchCalled = false;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    fetchCalled = true;
    return new Response("OK", { status: 200 });
  };

  try {
    const env = {
      WEBHOOK_SECRET: "correct-secret-token-12345",
      ALLOWED_CHAT_IDS: "1001",
      SESSIONS: mockKV
    };

    // 1. Başlıksız İstek
    const reqNoHeader = new Request("https://worker.dev", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: { text: "test", chat: { id: 1001 } } })
    });
    const res1 = await worker.fetch(reqNoHeader, env);
    assert.equal(res1.status, 403);
    assert.equal(mockKV.getCalls(), 0);
    assert.equal(fetchCalled, false);

    // 2. Yanlış Başlıklı İstek
    const reqWrongHeader = new Request("https://worker.dev", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Bot-Api-Secret-Token": "wrong-secret-token"
      },
      body: JSON.stringify({ message: { text: "test", chat: { id: 1001 } } })
    });
    const res2 = await worker.fetch(reqWrongHeader, env);
    assert.equal(res2.status, 403);
    assert.equal(mockKV.getCalls(), 0);
    assert.equal(fetchCalled, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

// ==========================================
// C1 TESTLERİ: Günlük Konuşma Regex ve Başka Kullanıcı Koruması
// ==========================================
test("C1: Günlük konuşma mesajları kural olarak kaydedilmiyor", () => {
  assert.equal(parseUserInputPrice("Saat 8'de evdeyim"), null);
  assert.equal(parseUserInputPrice("2 tane alalım mı?"), null);
  assert.equal(parseUserInputPrice("15000 olursa alırım ama 2 ay bekleriz"), null);
  assert.equal(parseUserInputPrice("tamam 50 dakika sonra gelirim"), null);
  assert.equal(parseUserInputPrice("5 kişi gidiyoruz"), null);
});

test("C1: Başka kullanıcının fiyat cevabı kural olarak kabul edilmiyor (pending_${chatId}_${fromId})", async () => {
  const mockKV = createMockKV({
    "pending_chat100_user1": JSON.stringify({
      url: "https://www.amazon.com.tr/dp/B0BK25SY9G",
      title: "Pioneer DJ",
      promptMessageId: 10
    })
  });

  const sentMessages = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (url.includes("api.telegram.org")) {
      sentMessages.push(JSON.parse(opts.body));
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response("OK", { status: 200 });
  };

  try {
    const env = {
      BOT_TOKEN: "fake-token",
      ALLOWED_CHAT_IDS: "chat100",
      SESSIONS: mockKV
    };

    // User 2 "18000" yazıyor (User 1'in linkine cevap vermeye çalışıyor)
    const updateFromUser2 = {
      message: {
        message_id: 11,
        chat: { id: "chat100" },
        from: { id: "user2" },
        text: "18000"
      }
    };

    await handleTelegramUpdate(updateFromUser2, env);

    // User 1'in oturumu bozulmamalı ve silinmemelidir
    assert.equal(mockKV.has("pending_chat100_user1"), true);
    // User 2 adına kural oluşturulmamalı veya işlem yapılmamalıdır
    assert.equal(mockKV.has("pending_chat100_user2"), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

// ==========================================
// C4 TESTLERİ: GitHub API 200, 409 Retry, 401 Senaryoları
// ==========================================
test("C4: GitHub API 200, 409->200 retry ve 401 yetki hatası senaryoları", async () => {
  const originalFetch = globalThis.fetch;

  try {
    // 1. Senaryo: 200 OK (İlk denemede başarılı kayıt)
    globalThis.fetch = async (url, opts) => {
      if (opts.method === "PUT") {
        return new Response(JSON.stringify({ content: {} }), { status: 200 });
      }
      return new Response(JSON.stringify({ content: "W10=", sha: "sha1" }), { status: 200 });
    };
    const resSuccess = await saveWatchlistWithRetry(
      { GITHUB_REPO: "test/repo", GITHUB_TOKEN: "token" },
      [{ id: "1" }],
      "commit",
      3
    );
    assert.equal(resSuccess.success, true);

    // 2. Senaryo: 409 Conflict -> 200 OK (İlk denemede çakışma, 2. denemede taze SHA ile başarı)
    let putAttempts = 0;
    globalThis.fetch = async (url, opts) => {
      if (opts.method === "PUT") {
        putAttempts++;
        if (putAttempts === 1) {
          return new Response("Conflict", { status: 409 });
        }
        return new Response(JSON.stringify({ content: {} }), { status: 200 });
      }
      return new Response(JSON.stringify({ content: "W10=", sha: `sha${putAttempts}` }), { status: 200 });
    };
    const resRetry = await saveWatchlistWithRetry(
      { GITHUB_REPO: "test/repo", GITHUB_TOKEN: "token" },
      [{ id: "1" }],
      "commit",
      3
    );
    assert.equal(resRetry.success, true);
    assert.equal(putAttempts, 2);

    // 3. Senaryo: 401 Unauthorized (Yetkisiz token -> kullanıcıya hata döndürür)
    globalThis.fetch = async (url, opts) => {
      if (opts.method === "PUT") {
        return new Response("Bad credentials", { status: 401 });
      }
      return new Response(JSON.stringify({ content: "W10=", sha: "sha1" }), { status: 200 });
    };
    const res401 = await saveWatchlistWithRetry(
      { GITHUB_REPO: "test/repo", GITHUB_TOKEN: "bad-token" },
      [{ id: "1" }],
      "commit",
      3
    );
    assert.equal(res401.success, false);
    assert.equal(res401.status, 401);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

// ==========================================
// R3 TESTLERİ: Scheduled() Dead Man's Switch (75 dk, KV, Düzelme)
// ==========================================
test("R3: checkDeadMansSwitch 75 dk eşiği, KV bayrağı ve düzelme bildirimi", async () => {
  const originalFetch = globalThis.fetch;
  const mockKV = createMockKV();
  const telegramMessages = [];

  try {
    let mockLastRunTime = Date.now() - (90 * 60 * 1000); // 90 dakika önce (> 75 dk)

    globalThis.fetch = async (url, opts) => {
      if (url.includes("api.telegram.org")) {
        const payload = JSON.parse(opts.body);
        telegramMessages.push(payload.text);
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      if (url.includes("actions/workflows")) {
        return new Response(JSON.stringify({
          workflow_runs: [{
            status: "completed",
            conclusion: "success",
            updated_at: new Date(mockLastRunTime).toISOString()
          }]
        }), { status: 200 });
      }
      return new Response("OK", { status: 200 });
    };

    const env = {
      GITHUB_REPO: "test/repo",
      GITHUB_TOKEN: "token",
      BOT_TOKEN: "bot-token",
      ALLOWED_CHAT_IDS: "1001",
      SESSIONS: mockKV
    };

    // 1. Aşama: 90 dk önce çalıştı -> Uyarı atar ve KV bayrağı koyar
    await checkDeadMansSwitch(env);
    assert.equal(telegramMessages.length, 1);
    assert.match(telegramMessages[0], /Fiyat Kontrolü Durmuş Olabilir/);
    assert.equal(mockKV.has("dms_alert_sent"), true);

    // 2. Aşama: Hala 90 dk önce ama KV bayrağı var -> Tekrar uyarı atmaz
    await checkDeadMansSwitch(env);
    assert.equal(telegramMessages.length, 1);

    // 3. Aşama: Actions normale döndü (10 dk önce çalıştı) -> Düzelme mesajı atar ve bayrağı siler
    mockLastRunTime = Date.now() - (10 * 60 * 1000);
    await checkDeadMansSwitch(env);
    assert.equal(telegramMessages.length, 2);
    assert.match(telegramMessages[1], /SİSTEM NORMALE DÖNDÜ/);
    assert.equal(mockKV.has("dms_alert_sent"), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

// ==========================================
// R5 TESTLERİ: Worker Exception Durumunda Bile HTTP 200
// ==========================================
test("R5: Worker iç hatada (exception) bile Telegram webhook'una HTTP 200 döner", async () => {
  const env = {
    WEBHOOK_SECRET: "my-secret",
    ALLOWED_CHAT_IDS: "1001"
  };

  // Bozuk / geçersiz JSON body gönderiliyor
  const req = new Request("https://worker.dev", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Bot-Api-Secret-Token": "my-secret"
    },
    body: "BU_BIR_JSON_DEGIL"
  });

  const res = await worker.fetch(req, env);
  assert.equal(res.status, 200);
  const text = await res.text();
  assert.equal(text, "OK");
});

// ==========================================
// A7 TESTLERİ: Aynı URL İkinci Kez Eklenemez
// ==========================================
test("A7: Aynı URL ikinci kez eklenemez (Deduplication)", async () => {
  const originalFetch = globalThis.fetch;
  const mockKV = createMockKV();
  const telegramMessages = [];

  const existingUrl = "https://www.amazon.com.tr/dp/B0BK25SY9G";
  const existingWatchlist = [
    {
      id: "d8wk9m6x",
      url: existingUrl,
      name: "Pioneer DJ DDJ-FLX4"
    }
  ];

  try {
    globalThis.fetch = async (url, opts) => {
      if (url.includes("api.telegram.org")) {
        const payload = JSON.parse(opts.body);
        telegramMessages.push(payload.text);
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      if (url.includes("contents/watchlist.json")) {
        const encoded = btoa(JSON.stringify(existingWatchlist));
        return new Response(JSON.stringify({ content: encoded, sha: "sha-abc" }), { status: 200 });
      }
      return new Response("OK", { status: 200 });
    };

    const env = {
      BOT_TOKEN: "bot-token",
      ALLOWED_CHAT_IDS: "1001",
      GITHUB_REPO: "test/repo",
      GITHUB_TOKEN: "token",
      SESSIONS: mockKV
    };

    // Aynı ürün linkini içeren mesaj gönderiliyor
    const update = {
      message: {
        message_id: 50,
        chat: { id: "1001" },
        from: { id: "user1" },
        text: `https://www.amazon.com.tr/dp/B0BK25SY9G?tag=affiliate`
      }
    };

    await handleTelegramUpdate(update, env);

    // Kullanıcıya zaten kayıtlı olduğu bildirilmeli
    assert.equal(telegramMessages.length, 1);
    assert.match(telegramMessages[0], /zaten takip listenizde kayıtlı/);
    // Yeni pending oturumu açılmamalıdır
    assert.equal(mockKV.has("pending_1001_user1"), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

// ==========================================
// PARSE, URL VE MESAJ YARDIMCILARI TESTLERİ
// ==========================================
test("timingSafeEqual: string comparisons in constant time", () => {
  assert.equal(timingSafeEqual("my-secret-token", "my-secret-token"), true);
  assert.equal(timingSafeEqual("my-secret-token", "wrong-secret-token"), false);
  assert.equal(timingSafeEqual("short", "longer-secret"), false);
  assert.equal(timingSafeEqual("", ""), true);
  assert.equal(timingSafeEqual(null, "token"), false);
});

test("parseUserInputPrice: strict price inputs", () => {
  assert.deepEqual(parseUserInputPrice("18000"), { targetPrice: 18000, discountPercent: null, error: null });
  assert.deepEqual(parseUserInputPrice("18.000"), { targetPrice: 18000, discountPercent: null, error: null });
  assert.deepEqual(parseUserInputPrice("1.250,50"), { targetPrice: 1250.5, discountPercent: null, error: null });
  assert.deepEqual(parseUserInputPrice("1250.50"), { targetPrice: 1250.5, discountPercent: null, error: null });
  assert.deepEqual(parseUserInputPrice("%30"), { targetPrice: null, discountPercent: 30, error: null });
  assert.deepEqual(parseUserInputPrice("30%"), { targetPrice: null, discountPercent: 30, error: null });
  assert.deepEqual(parseUserInputPrice("% 25.5"), { targetPrice: null, discountPercent: 25.5, error: null });
});

test("parseUserInputPrice: out of bounds range validation", () => {
  assert.deepEqual(parseUserInputPrice("0"), { error: "invalid_range" });
  assert.deepEqual(parseUserInputPrice("20000000"), { error: "invalid_range" });
  assert.deepEqual(parseUserInputPrice("%99"), { error: "invalid_range" });
  assert.deepEqual(parseUserInputPrice("%0"), { error: "invalid_range" });
});

test("validateAndCleanUrl: domain whitelist and URL normalization", async () => {
  const amz = await validateAndCleanUrl("https://www.amazon.com.tr/dp/B0BK25SY9G?tag=abc-21");
  assert.equal(amz.url, "https://www.amazon.com.tr/dp/B0BK25SY9G");
  assert.equal(amz.platform, "Amazon");

  const amzUs = await validateAndCleanUrl("https://www.amazon.com/dp/B0BK25SY9G");
  assert.equal(amzUs.error, "unsupported_amazon");

  const ty = await validateAndCleanUrl("https://www.trendyol.com/pioneer/ddj-flx4-p-471697117?boutiqueId=123");
  assert.equal(ty.url, "https://www.trendyol.com/pioneer/ddj-flx4-p-471697117");

  const hb = await validateAndCleanUrl("https://www.hepsiburada.com/pioneer-dj-ddj-flx4-pm-HBC00003MXQS9?magaza=xyz");
  assert.equal(hb.url, "https://www.hepsiburada.com/pioneer-dj-ddj-flx4-pm-HBC00003MXQS9");

  const ebay = await validateAndCleanUrl("https://www.ebay.com/itm/12345");
  assert.equal(ebay.error, "unsupported_domain");
});

test("chunkMessage: splits long messages safely", () => {
  const shortMsg = "Merhaba Dünya";
  assert.deepEqual(chunkMessage(shortMsg, 3800), [shortMsg]);

  const line = "A".repeat(100) + "\n";
  const longMsg = line.repeat(50);
  const chunks = chunkMessage(longMsg, 3800);
  assert.ok(chunks.length > 1);
  for (const c of chunks) {
    assert.ok(c.length <= 3800);
  }
});

test("htmlEscape: escapes HTML characters properly", () => {
  assert.equal(
    htmlEscape('<div class="test">A & B > C</div>'),
    "&lt;div class=&quot;test&quot;&gt;A &amp; B &gt; C&lt;/div&gt;"
  );
  assert.equal(htmlEscape(null), "");
});
