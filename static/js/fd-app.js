(function () {
  var SESSION_KEY = "fd.lab.session.v1";
  var LOGS_KEY = "fd.lab.logs.v1";

  var DEFAULTS = {
    lga: "Zaria",
    soil: "Loamy",
    soilOverridden: false,
    water: "Rain only",
    equipment: "Hand tools",
    s1: ["maize"],
    s2: ["maize"],
    priorities: ["save_water", "add_nitrogen"],
    sell: "Local market",
    season_type: "Typical",
    pick: "a",
    lang: "en",
    farmerName: "",
    advisorName: "CBA Field Advisor (KADA)",
    savedAt: ""
  };

  var HAUSA_UI = {
    "nav.advisor": "Mai Ba Da Shawara Na Kaduna",
    "nav.ready": "A Shirye (Ba Tare Da Intanet Ba)",
    "nav.offline": "Yanayin Rashin Intanet",
    "nav.farm": "1 Gona",
    "nav.priorities": "2 Bukatu",
    "nav.season": "3 Damina",
    "nav.compare": "4 Kwatanta",
    "nav.decide": "5 Zabi",
    "nav.share": "Aika",
    "nav.map": "Taswirar LGA 23",
    "nav.learn": "Kimiyya",
    "nav.logs": "Tarihin Ziyara",
    "nav.screens": "Dukkan Shafi",
    "footer.judgment": "Wannan kayan aiki yana taimaka wa hukuncin manomi da kwarewar gida. Tuntubi malamin gonarku."
  };

  var ENGLISH_UI = {
    "nav.advisor": "Kaduna Rotation Advisor",
    "nav.ready": "Ready (Offline-First)",
    "nav.offline": "Offline Cached Mode",
    "nav.farm": "1 Farm",
    "nav.priorities": "2 Priorities",
    "nav.season": "3 Season",
    "nav.compare": "4 Compare",
    "nav.decide": "5 Decide",
    "nav.share": "Share",
    "nav.map": "LGA Trend Map",
    "nav.learn": "Learn Science",
    "nav.logs": "Field Logs",
    "nav.screens": "All Screens",
    "footer.judgment": "This tool supports farmer judgment and local expertise. Check with your extension officer."
  };

  function safeParse(raw, fallback) {
    try {
      return raw ? JSON.parse(raw) : fallback;
    } catch (_err) {
      return fallback;
    }
  }

  function load() {
    var stored = safeParse(localStorage.getItem(SESSION_KEY), {});
    return Object.assign({}, DEFAULTS, stored);
  }

  function save(patch) {
    var merged = Object.assign(load(), patch || {}, { savedAt: new Date().toISOString() });
    try {
      localStorage.setItem(SESSION_KEY, JSON.stringify(merged));
    } catch (_err) {
      /* private browsing fallback */
    }
    return merged;
  }

  function hydrateFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var patch = {};
    if (params.get("lga")) patch.lga = params.get("lga");
    if (params.get("soil")) patch.soil = params.get("soil");
    if (params.get("water")) patch.water = params.get("water");
    if (params.get("equipment")) patch.equipment = params.get("equipment");
    if (params.get("s1")) patch.s1 = params.get("s1").split(",").filter(Boolean);
    if (params.get("s2")) patch.s2 = params.get("s2").split(",").filter(Boolean);
    if (params.has("priorities")) {
      patch.priorities = params.get("priorities").split(",").filter(Boolean);
    }
    if (params.get("sell")) patch.sell = params.get("sell");
    if (params.get("season_type")) patch.season_type = params.get("season_type");
    if (params.get("pick")) patch.pick = params.get("pick");
    if (params.get("lang")) patch.lang = params.get("lang");
    if (Object.keys(patch).length > 0) {
      return save(patch);
    }
    return load();
  }

  function toQuery(extra) {
    var s = Object.assign(load(), extra || {});
    var q = new URLSearchParams();
    q.set("lga", s.lga || "Zaria");
    if (s.soil) q.set("soil", s.soil);
    q.set("water", s.water || "Rain only");
    q.set("equipment", s.equipment || "Hand tools");
    q.set("s1", (s.s1 && s.s1.length ? s.s1 : ["maize"]).join(","));
    q.set("s2", (s.s2 && s.s2.length ? s.s2 : []).join(","));
    q.set("priorities", (s.priorities || []).join(","));
    q.set("sell", s.sell || "Local market");
    q.set("season_type", s.season_type || "Typical");
    if (s.pick) q.set("pick", s.pick);
    if (s.lang && s.lang !== "en") q.set("lang", s.lang);
    return q.toString();
  }

  function go(path, extra) {
    var sep = path.indexOf("?") === -1 ? "?" : "&";
    window.location.href = path + sep + toQuery(extra);
  }

  function logs() {
    return safeParse(localStorage.getItem(LOGS_KEY), []);
  }

  function recordConsultation(entry) {
    var s = load();
    var item = Object.assign(
      {
        id: "FD-" + Date.now(),
        farmerLabel: s.farmerName || "Walk-in Smallholder",
        lga: s.lga || "Zaria",
        soil: s.soil || "Loamy",
        water: s.water || "Rain only",
        currentSeq: (s.s2 && s.s2.length ? s.s2.join("+") + " -> " : "") + (s.s1 || ["maize"]).join("+"),
        chosenPlan: "Option A",
        confidence: "Medium",
        status: "Saved Locally",
        createdAt: new Date().toISOString()
      },
      entry || {}
    );
    var current = logs();
    current.unshift(item);
    try {
      localStorage.setItem(LOGS_KEY, JSON.stringify(current.slice(0, 100)));
    } catch (_err) {
      /* ignore quota */
    }
    return item;
  }

  function clearLogs() {
    try {
      localStorage.removeItem(LOGS_KEY);
    } catch (_err) {
      /* ignore */
    }
  }

  function applyLanguage() {
    var state = load();
    var lang = state.lang === "ha" ? "ha" : "en";
    var dict = lang === "ha" ? HAUSA_UI : ENGLISH_UI;
    document.documentElement.lang = lang;
    document.querySelectorAll(".fd-lang-btn").forEach(function (btn) {
      var active = btn.getAttribute("data-lang") === lang;
      btn.classList.toggle("is-on", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      if (dict[key]) {
        el.textContent = dict[key];
      }
    });
    document.querySelectorAll("[data-lang-ha]").forEach(function (el) {
      if (!el.hasAttribute("data-lang-en")) {
        el.setAttribute("data-lang-en", el.textContent);
      }
      el.textContent = lang === "ha" ? el.getAttribute("data-lang-ha") : el.getAttribute("data-lang-en");
    });
    var banner = document.getElementById("fdHausaBanner");
    if (banner) banner.hidden = lang !== "ha";
    document.dispatchEvent(new CustomEvent("fd:langchange", { detail: { lang: lang } }));
  }

  function updateNetworkPill() {
    var pill = document.getElementById("fdStatusPill");
    var label = document.getElementById("fdStatusText");
    if (!pill || !label) return;
    var online = navigator.onLine !== false;
    pill.classList.toggle("is-offline", !online);
    var lang = load().lang === "ha" ? "ha" : "en";
    var dict = lang === "ha" ? HAUSA_UI : ENGLISH_UI;
    label.textContent = online ? dict["nav.ready"] : dict["nav.offline"];
  }

  function openTerm(termKey, lga) {
    var modal = document.getElementById("fdTermModal");
    var body = document.getElementById("fdTermModalBody");
    if (!modal || !body) return;
    var targetLga = lga || load().lga || "Zaria";
    modal.hidden = false;
    body.innerHTML = '<p style="padding:12px 0;color:#5a665d;">Loading explanation...</p>';
    fetch("/partials/explain/" + encodeURIComponent(termKey) + "?lga=" + encodeURIComponent(targetLga))
      .then(function (r) {
        if (!r.ok) throw new Error("Failed");
        return r.text();
      })
      .then(function (html) {
        body.innerHTML = html;
      })
      .catch(function () {
        body.innerHTML = '<p>Offline term note: check the Learn Science glossary for full definitions.</p>';
      });
  }

  function closeTerm() {
    var modal = document.getElementById("fdTermModal");
    if (modal) modal.hidden = true;
  }

  document.addEventListener("click", function (ev) {
    var langBtn = ev.target.closest(".fd-lang-btn");
    if (langBtn) {
      save({ lang: langBtn.getAttribute("data-lang") });
      applyLanguage();
      updateNetworkPill();
      return;
    }
    var termTrigger = ev.target.closest("[data-explain-term]");
    if (termTrigger) {
      ev.preventDefault();
      openTerm(termTrigger.getAttribute("data-explain-term"), termTrigger.getAttribute("data-lga"));
      return;
    }
    if (ev.target.id === "fdTermModal" || ev.target.closest("[data-close-term]")) {
      closeTerm();
    }
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeTerm();
  });

  window.addEventListener("online", updateNetworkPill);
  window.addEventListener("offline", updateNetworkPill);

  document.addEventListener("DOMContentLoaded", function () {
    hydrateFromUrl();
    applyLanguage();
    updateNetworkPill();
  });

  window.FDStore = {
    load: load,
    save: save,
    toQuery: toQuery,
    go: go,
    logs: logs,
    recordConsultation: recordConsultation,
    clearLogs: clearLogs,
    openTerm: openTerm,
    closeTerm: closeTerm
  };
})();
