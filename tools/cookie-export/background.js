// One-click export of this browser's Google cookies to a Netscape cookies.txt.
//
// Everything runs locally in the browser. Cookies are read with the extension's
// `cookies` permission (so HttpOnly session cookies like SID/HSID are included,
// which a page script cannot see) and written to a file you choose. Nothing is
// sent anywhere. The file is the full sign-in state of the account; treat it
// like a password and delete it when the tests are done.
//
// Cookies are collected per URL (`getAll({url})`), not per domain. That is the
// set the browser would actually send to each site, so the base auth cookies
// (SID, HSID, APISID and the __Secure- variants) are all included. A plain
// `getAll({domain})` can leave some of them out, which produces a half-logged-in
// session that Google challenges for write operations.

const URLS = [
  "https://docs.google.com/",
  "https://classroom.google.com/",
  "https://drive.google.com/",
  "https://accounts.google.com/",
  "https://www.google.com/",
];

function netscapeLines(cookies) {
  const lines = [
    "# Netscape HTTP Cookie File",
    "# Exported by GoogleClassroomAPI cookie export for GoogleClassroomAPI live tests.",
    "# This is the account's full sign-in state. Treat it like a password and delete it after use.",
  ];
  for (const c of cookies) {
    const hostOnly = !!c.hostOnly;
    const includeSubdomains = hostOnly ? "FALSE" : "TRUE";
    let domain = c.domain || "";
    domain = hostOnly ? domain.replace(/^\./, "") : (domain.startsWith(".") ? domain : "." + domain);
    const secure = c.secure ? "TRUE" : "FALSE";
    const expiry = c.session ? 0 : Math.floor(c.expirationDate || 0);
    lines.push([domain, includeSubdomains, c.path || "/", secure, expiry, c.name, c.value].join("\t"));
  }
  return lines.join("\n") + "\n";
}

async function collect() {
  const seen = new Map();
  for (const url of URLS) {
    let cookies = [];
    try { cookies = await chrome.cookies.getAll({ url }); } catch (e) { /* skip this url */ }
    for (const c of cookies) {
      seen.set(`${c.name}\t${c.domain}\t${c.path}`, c);  // dedupe across urls
    }
  }
  return [...seen.values()];
}

function toDataUrl(text) {
  // Service workers have no URL.createObjectURL, so hand the download a data URL.
  const utf8 = encodeURIComponent(text).replace(/%([0-9A-F]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));
  return "data:text/plain;charset=utf-8;base64," + btoa(utf8);
}

async function badge(text, color) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color });
    await chrome.action.setBadgeText({ text });
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 4000);
  } catch (e) { /* badge is only a nicety */ }
}

chrome.action.onClicked.addListener(async () => {
  try {
    const cookies = await collect();
    const names = new Set(cookies.map((c) => c.name));
    const required = ["SID", "HSID", "SAPISID", "__Secure-1PSID"];
    const missing = required.filter((n) => !names.has(n));
    if (!cookies.length) { await badge("0", "#b00020"); return; }

    const url = toDataUrl(netscapeLines(cookies));
    await chrome.downloads.download({
      url,
      filename: "gca-cookies.txt",
      saveAs: true,            // you choose where it lands — keep it out of the repo
      conflictAction: "overwrite",
    });
    // Green = complete session. Amber = some base auth cookies were not found,
    // which usually means you are not fully signed in to that account in this profile.
    if (missing.length) {
      console.warn("[GoogleClassroomAPI cookie export] missing base cookies:", missing.join(", "));
      await badge("!", "#f4b400");
    } else {
      await badge(String(cookies.length), "#0f9d58");
    }
  } catch (e) {
    console.error("[GoogleClassroomAPI cookie export]", e);
    await badge("err", "#b00020");
  }
});
