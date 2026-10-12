import test from "node:test";
import assert from "node:assert/strict";
import {
  timingSafeEqual,
  parseUserInputPrice,
  validateAndCleanUrl,
  chunkMessage,
  htmlEscape
} from "../cloudflare_worker.js";

test("timingSafeEqual: string comparisons in constant time", () => {
  assert.equal(timingSafeEqual("my-secret-token", "my-secret-token"), true);
  assert.equal(timingSafeEqual("my-secret-token", "wrong-secret-token"), false);
  assert.equal(timingSafeEqual("short", "longer-secret"), false);
  assert.equal(timingSafeEqual("", ""), true);
  assert.equal(timingSafeEqual(null, "token"), false);
});

test("parseUserInputPrice: strict price inputs", () => {
  // Plain integers
  assert.deepEqual(parseUserInputPrice("18000"), {
    targetPrice: 18000,
    discountPercent: null,
    error: null
  });

  // Turkish thousands separator
  assert.deepEqual(parseUserInputPrice("18.000"), {
    targetPrice: 18000,
    discountPercent: null,
    error: null
  });

  // Turkish decimal (comma)
  assert.deepEqual(parseUserInputPrice("1.250,50"), {
    targetPrice: 1250.5,
    discountPercent: null,
    error: null
  });

  // Standard dot decimal
  assert.deepEqual(parseUserInputPrice("1250.50"), {
    targetPrice: 1250.5,
    discountPercent: null,
    error: null
  });

  // Percent formats
  assert.deepEqual(parseUserInputPrice("%30"), {
    targetPrice: null,
    discountPercent: 30,
    error: null
  });
  assert.deepEqual(parseUserInputPrice("30%"), {
    targetPrice: null,
    discountPercent: 30,
    error: null
  });
  assert.deepEqual(parseUserInputPrice("% 25.5"), {
    targetPrice: null,
    discountPercent: 25.5,
    error: null
  });
});

test("parseUserInputPrice: ignores regular group conversations", () => {
  assert.equal(parseUserInputPrice("tamam 50 dakika sonra gelirim"), null);
  assert.equal(parseUserInputPrice("5 kişi gidiyoruz"), null);
  assert.equal(parseUserInputPrice("merhaba"), null);
  assert.equal(parseUserInputPrice("fiyatı 18000 olur mu?"), null);
});

test("parseUserInputPrice: out of bounds range validation", () => {
  assert.deepEqual(parseUserInputPrice("0"), { error: "invalid_range" });
  assert.deepEqual(parseUserInputPrice("20000000"), { error: "invalid_range" });
  assert.deepEqual(parseUserInputPrice("%99"), { error: "invalid_range" });
  assert.deepEqual(parseUserInputPrice("%0"), { error: "invalid_range" });
});

test("validateAndCleanUrl: domain whitelist and URL normalization", async () => {
  // Amazon TR
  const amz = await validateAndCleanUrl("https://www.amazon.com.tr/dp/B0BK25SY9G?tag=abc-21");
  assert.equal(amz.url, "https://www.amazon.com.tr/dp/B0BK25SY9G");
  assert.equal(amz.platform, "Amazon");

  // Non-TR Amazon rejected
  const amzUs = await validateAndCleanUrl("https://www.amazon.com/dp/B0BK25SY9G");
  assert.equal(amzUs.error, "unsupported_amazon");
  const amzDe = await validateAndCleanUrl("https://www.amazon.de/dp/B0BK25SY9G");
  assert.equal(amzDe.error, "unsupported_amazon");

  // Trendyol
  const ty = await validateAndCleanUrl("https://www.trendyol.com/pioneer/ddj-flx4-p-471697117?boutiqueId=123");
  assert.equal(ty.url, "https://www.trendyol.com/pioneer/ddj-flx4-p-471697117");
  assert.equal(ty.platform, "Trendyol");

  // Trendyol English path clean
  const tyEn = await validateAndCleanUrl("https://www.trendyol.com/en/pioneer/ddj-flx4-p-471697117");
  assert.equal(tyEn.url, "https://www.trendyol.com/pioneer/ddj-flx4-p-471697117");

  // Hepsiburada
  const hb = await validateAndCleanUrl("https://www.hepsiburada.com/pioneer-dj-ddj-flx4-pm-HBC00003MXQS9?magaza=xyz");
  assert.equal(hb.url, "https://www.hepsiburada.com/pioneer-dj-ddj-flx4-pm-HBC00003MXQS9");
  assert.equal(hb.platform, "Hepsiburada");

  // Unsupported domain
  const ebay = await validateAndCleanUrl("https://www.ebay.com/itm/12345");
  assert.equal(ebay.error, "unsupported_domain");
});

test("chunkMessage: splits long messages safely", () => {
  const shortMsg = "Merhaba Dünya";
  assert.deepEqual(chunkMessage(shortMsg, 3800), [shortMsg]);

  const line = "A".repeat(100) + "\n";
  const longMsg = line.repeat(50); // 5000+ chars
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
