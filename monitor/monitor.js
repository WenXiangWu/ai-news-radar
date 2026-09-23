(function () {
  "use strict";

  function radarDataUrl(path) {
    return new URL("../data/" + String(path).replace(/^\/+/, ""), window.location.href).toString();
  }

  function resolveRadarPath(path) {
    var value = String(path || "");
    if (!value || value === "#") return value || "#";
    if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(value)) return value;
    if (value.charAt(0) === "/") return new URL(".." + value, window.location.href).toString();
    return new URL(value, window.location.href).toString();
  }

  var RUNTIME_URL = radarDataUrl("radar-runtime.json");
  var SNAPSHOT_URL = radarDataUrl("radar-monitor.json");
  var UPDATE_URL = radarDataUrl("radar-update-report.json");
  var INDEX_URL = radarDataUrl("radar-reports/index.json");
  var TABS = ["today", "registry", "coverage", "reports", "config"];
  var CATALOG = [
    { id: "update-news", name: "新闻采集", duty: "抓前沿新闻，写入待推送。不推站点。", when: "每小时第 17 分", cron: "17 * * * *" },
    { id: "update-radar", name: "资料抓取", duty: "官方资料、框架文档、wiki。15 组并行，每组最多 6 个源。", when: "03:17、09:17、15:17、21:17", cron: "17 1,7,13,19 * * *" },
    { id: "update-textbooks", name: "教材镜像", duty: "按登记表镜像 10 本教材。不翻译，不提交正文。", when: "每天 08:17", cron: "17 0 * * *" },
    { id: "update-publish", name: "站点推送", duty: "把待推送内容推上 Way。唯一会推 Gitee 的任务。", when: "每小时第 47 分", cron: "47 * * * *" }
  ];
  var state = {
    monitor: null,
    update: null,
    index: null,
    coverageFilter: "attention",
    registryMode: "summary",
    todayMode: "failed",
    focusWorkflow: "",
    focusName: "",
    runtime: null
  };

  function $(id) { return document.getElementById(id); }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function fetchJson(url) {
    return fetch(url, { cache: "no-store" }).then(function (response) {
      if (!response.ok) throw new Error(response.status + " " + url);
      return response.json();
    });
  }

  function reported(value) {
    if (value == null || value === "") return "未上报";
    return String(value);
  }

  function currentTab() {
    var hash = String(location.hash || "").replace("#", "");
    return TABS.indexOf(hash) >= 0 ? hash : "today";
  }

  function showTab(tab) {
    var active = TABS.indexOf(tab) >= 0 ? tab : "today";
    Array.prototype.forEach.call(document.querySelectorAll("[data-tab-panel]"), function (panel) {
      panel.hidden = panel.getAttribute("data-tab-panel") !== active;
    });
    Array.prototype.forEach.call(document.querySelectorAll("#monitorTabs [data-tab]"), function (button) {
      button.setAttribute("aria-selected", button.getAttribute("data-tab") === active ? "true" : "false");
    });
  }

  function renderUnavailableMonitor() {
    var message = "Radar 尚未发布可验证的监控快照。本轮执行可能仍在运行、失败或尚未完成发布。";
    $("kpis").innerHTML = '<div class="empty-state monitor-unavailable">数据不可判定：' + escapeHtml(message) + " 不能把缺失数据显示为 0。</div>";
    $("runStatus").textContent = "无可用快照";
    $("workflowCards").innerHTML = '<p class="empty-state">监控快照尚未发布，不能判定 workflow。</p>';
    $("moduleBoard").innerHTML = '<p class="empty-state">监控快照尚未发布，不能判定各模块执行情况。</p>';
    $("todayMessages").innerHTML = "";
    $("todayEmpty").hidden = false;
    $("coverageModules").innerHTML = '<p class="empty-state">监控快照尚未发布，不能判定 Way 覆盖。</p>';
    $("moduleTable").innerHTML = '<p class="empty-state">监控快照尚未发布，不能把缺失数据显示为 0。</p>';
    $("sourceTable").innerHTML = "";
    $("sourceEmpty").hidden = false;
    $("providers").innerHTML = '<p class="empty-state">暂无可验证的提供方运行数据。</p>';
    $("snapshotMeta").textContent = "尚未发布监控快照";
    showError(message);
  }

  function formatDate(value) {
    if (!value) return "未记录";
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
  }

  function formatShanghaiMinute(value) {
    if (!value) return "无基线时间";
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23"
    }).format(date);
  }

  function statusClass(status) {
    var value = String(status || "").toLowerCase();
    if (["success", "ok", "healthy", "configured", "present", "updating"].indexOf(value) >= 0) return "good";
    if (["pending", "degraded", "not_configured", "unknown", "registered", "absent", "warn"].indexOf(value) >= 0) return "warn";
    if (["failed", "partial", "error", "blocked", "excluded"].indexOf(value) >= 0) return "bad";
    return "";
  }

  function statusLabel(status) {
    var labels = {
      success: "成功", ok: "正常", healthy: "可达", configured: "已配置",
      pending: "待执行", degraded: "降级", not_configured: "未配置",
      failed: "失败", partial: "部分成功", error: "错误", disabled: "已停用",
      present: "有基线", absent: "无基线", verified: "已验证",
      updating: "更新中", registered: "已登记", excluded: "不更新", unregistered: "未登记",
      incremental: "增量", baseline_only: "仅基线"
    };
    return labels[String(status || "")] || String(status || "未知");
  }

  function chip(status, text) {
    return '<span class="mini-chip ' + statusClass(status) + '">' + escapeHtml(text || statusLabel(status)) + "</span>";
  }

  function workflowsById(monitor) {
    var map = {};
    ((monitor && monitor.workflows) || []).forEach(function (workflow) {
      map[workflow.workflow_id] = workflow;
    });
    return map;
  }

  function moduleNameForSource(monitor, sourceId) {
    var modules = (monitor.coverage && monitor.coverage.modules) || [];
    for (var i = 0; i < modules.length; i += 1) {
      var items = modules[i].items || [];
      for (var j = 0; j < items.length; j += 1) {
        if (String(items[j].radar_source_id || "") === String(sourceId || "")) return modules[i].name || modules[i].module_id;
      }
    }
    return "";
  }

  function failedCount(monitor) {
    var count = 0;
    ((monitor && monitor.workflows) || []).forEach(function (workflow) {
      (workflow.sources || []).forEach(function (source) {
        if (source.status === "failed") count += 1;
      });
      if (!(workflow.sources || []).length && workflow.conclusion === "failure") count += 1;
    });
    return count;
  }

  function renderKpis(monitor) {
    var failed = failedCount(monitor);
    var publish = workflowsById(monitor)["update-publish"];
    var push = publish && publish.push || {};
    var pushed = !!(push.attempted && push.ok);
    var title = "尚无执行记录";
    var detail = "四条 workflow 都还没有写下状态。";
    var tone = "warn";
    if (failed) {
      title = "今日有失败";
      detail = "先看模块大盘里的红色数字，再进今日明细。";
      tone = "bad";
    } else if (pushed) {
      title = "今日已同步到站点";
      detail = "站点推送已经成功。";
      tone = "good";
    } else if ((monitor.workflows || []).length) {
      title = "抓取已完成，站点未推送";
      detail = "抓取结果在待推送目录。站点推送尚未成功。";
      tone = "warn";
    }
    $("kpis").className = "status-banner " + tone;
    $("kpis").innerHTML = '<div><strong>' + escapeHtml(title) + '</strong><p>' + escapeHtml(detail) + '</p></div>' +
      '<div class="status-side"><div>失败<b>' + failed + '</b></div><div>未推送<b>' + (pushed ? "否" : "是") + "</b></div></div>";
    $("runStatus").textContent = title;
  }

  function formatSlots(slots) {
    if (!slots || !slots.length) return "未配置时间";
    if (slots.length === 1 && String(slots[0]).charAt(0) === "*") {
      return "每小时第 " + Number(String(slots[0]).split(":")[1]) + " 分";
    }
    if (slots.length === 1) return "每天 " + slots[0];
    return slots.join("、");
  }

  function runtimeWorkflow(id) {
    var workflows = state.runtime && state.runtime.workflows;
    return workflows && workflows[id] || null;
  }

  function renderWorkflows(monitor) {
    var known = workflowsById(monitor || {});
    $("workflowCards").innerHTML = CATALOG.map(function (item) {
      var configured = runtimeWorkflow(item.id);
      var when = configured && configured.slots_shanghai ? formatSlots(configured.slots_shanghai) : item.when;
      var live = known[item.id];
      var last = "尚未执行";
      var tone = "warn";
      if (configured && configured.enabled === false) {
        last = "已停用";
      } else if (live) {
        last = statusLabel(live.conclusion) + " · " + formatShanghaiMinute(live.finished_at || live.started_at);
        tone = live.conclusion === "success" ? "good" : (live.conclusion === "failure" ? "bad" : "warn");
        if (item.id === "update-publish" && !(live.push && live.push.ok)) {
          last = (live.push && live.push.attempted) ? "推送失败" : "尚未推送";
          tone = (live.push && live.push.attempted) ? "bad" : "warn";
        }
      }
      return '<button type="button" class="catalog-card' + (state.focusWorkflow === item.id ? " selected" : "") +
        '" data-workflow="' + escapeHtml(item.id) + '"><b>' + escapeHtml(item.name) +
        '</b><p>' + escapeHtml(item.duty) + '</p><div class="when">' + escapeHtml(when) +
        '<span>' + escapeHtml(item.cron) + "</span></div>" + chip(tone, last) + "</button>";
    }).join("");
  }

  function boardCards(monitor) {
    var statuses = {};
    (monitor.workflows || []).forEach(function (workflow) {
      (workflow.sources || []).forEach(function (source) {
        if (source.source_id) statuses[String(source.source_id)] = String(source.status || "");
      });
    });
    var cards = ((monitor.coverage && monitor.coverage.modules) || []).map(function (module) {
      var failed = 0;
      var success = 0;
      var idle = 0;
      (module.items || []).forEach(function (item) {
        if (item.coverage === "excluded" || item.coverage === "unregistered") return;
        var status = statuses[String(item.radar_source_id || "")];
        if (status === "failed") failed += 1;
        else if (status === "success") success += 1;
        else idle += 1;
      });
      return {
        name: module.name || module.module_id,
        failed: failed,
        success: success,
        idle: idle,
        tone: failed ? "bad" : (success ? "good" : "idle"),
        label: !failed && !success && !idle ? "不更新" : ""
      };
    });
    var publish = workflowsById(monitor)["update-publish"];
    var push = publish && publish.push || {};
    var pushTone = "warn";
    var pushLabel = "未推送";
    if (push.attempted && push.ok) {
      pushTone = "good";
      pushLabel = "已推送";
    } else if (push.attempted) {
      pushTone = "bad";
      pushLabel = "推送失败";
    }
    cards.push({ name: "站点推送", failed: pushTone === "bad" ? 1 : 0, success: 0, idle: 0, tone: pushTone, label: pushLabel });
    cards.sort(function (left, right) { return right.failed - left.failed; });
    return cards;
  }

  function renderBoard(monitor) {
    $("moduleBoard").innerHTML = boardCards(monitor || {}).map(function (card) {
      var num = card.label || (card.failed ? String(card.failed) : (card.success ? String(card.success) : "未执行"));
      var detail = card.name === "站点推送" ? "唯一推送任务" : (card.label ? "静态目录" : (card.failed ? "失败" : (card.success ? "成功" : (card.idle ? card.idle + " 个源" : "静态目录"))));
      return '<button type="button" class="board-card ' + card.tone + (state.focusName === card.name ? " selected" : "") +
        '" data-board-module="' + escapeHtml(card.name) + '"><strong>' + escapeHtml(card.name) +
        '</strong><span class="board-count">' + escapeHtml(num) + '</span><span class="board-detail">' +
        escapeHtml(detail) + "</span></button>";
    }).join("") || '<p class="empty-state">没有可展示的模块执行情况。</p>';
  }

  function openBoardModule(name) {
    state.focusName = state.focusName === name ? "" : name;
    state.focusWorkflow = "";
    state.todayMode = "all";
    press("#todayFilters [data-today]", "all");
    if (location.hash !== "#today") location.hash = "today";
    showTab("today");
    renderBoard(state.monitor);
    renderWorkflows(state.monitor);
    renderToday(state.monitor);
    if ($("todayMessages").scrollIntoView) $("todayMessages").scrollIntoView({ block: "start" });
  }

  function sourceLines(workflow, monitor) {
    return (workflow.sources || []).filter(function (source) {
      if (!state.focusName || state.focusName === "站点推送") return state.focusName !== "站点推送";
      return moduleNameForSource(monitor, source.source_id) === state.focusName;
    });
  }

  function renderToday(monitor) {
    var known = workflowsById(monitor || {});
    var groups = CATALOG.map(function (item) {
      var live = known[item.id] || { sources: [] };
      var list = item.id === "update-publish" ? [] : sourceLines(live, monitor);
      if (state.focusWorkflow && state.focusWorkflow !== item.id) return null;
      if (state.focusName && state.focusName !== "站点推送" && item.id !== "update-publish" && !list.length) return null;
      if (state.focusName === "站点推送" && item.id !== "update-publish") return null;
      return { item: item, live: live, list: list, failed: list.filter(function (source) { return source.status === "failed"; }) };
    }).filter(Boolean);
    var failedTotal = groups.reduce(function (sum, group) { return sum + group.failed.length; }, 0);
    if (state.todayMode === "failed" && !failedTotal) {
      $("todayMessages").innerHTML = "";
      $("todayEmpty").hidden = false;
      return;
    }
    $("todayEmpty").hidden = true;
    $("todayMessages").innerHTML = groups.map(function (group) {
      var shown = state.todayMode === "failed" ? group.failed : group.list;
      var open = state.todayMode === "all" || group.failed.length;
      var success = group.list.filter(function (source) { return source.status === "success"; }).length;
      var body = shown.map(function (source) {
        return '<div class="line"><span>' + escapeHtml(source.source_id) + "</span><em>" +
          escapeHtml(statusLabel(source.run_kind)) + " · " + escapeHtml(statusLabel(source.status)) +
          (source.error ? " · " + escapeHtml(source.error) : "") + "</em></div>";
      }).join("") || '<div class="line"><em>' + (group.live.conclusion ? "本组没有失败" : "尚未执行") + "</em></div>";
      return '<details class="group"' + (open ? " open" : "") + "><summary><span><b>" + escapeHtml(group.item.name) +
        '</b><span class="sub">' + escapeHtml(group.item.when) + "</span></span><span class=\"counts\">" +
        chip(group.failed.length ? "failed" : "success", "失败 " + group.failed.length) +
        chip("success", "成功 " + success) + "</span></summary>" + body + "</details>";
    }).join("");
  }

  function coverageLines(module) {
    var mode = state.coverageFilter;
    return (module.items || []).filter(function (item) {
      if (mode === "attention") return item.coverage === "unregistered" || item.coverage === "excluded";
      if (mode === "registered") return false;
      return item.coverage === mode;
    });
  }

  function renderCoverage(coverage) {
    var modules = (coverage && coverage.modules) || [];
    $("coverageModules").innerHTML = modules.map(function (module) {
      var counts = { unregistered: 0, excluded: 0, registered: 0, updating: 0 };
      (module.items || []).forEach(function (item) {
        if (counts[item.coverage] != null) counts[item.coverage] += 1;
      });
      var lines = coverageLines(module);
      if (state.coverageFilter === "registered") {
        lines = counts.registered ? [{ name: counts.registered + " 条已登记", coverage: "registered", reason_zh: "多数是抓取已完成、尚未推上站点" }] : [];
      }
      var open = state.coverageFilter === "attention" ? (counts.unregistered + counts.excluded) > 0 : lines.length > 0;
      var body = lines.map(function (item) {
        var baseline = item.baseline === "not_applicable" ? "不适用" : (item.baseline === "present" ? "有基线" : "无基线");
        return '<div class="line"><span>' + escapeHtml(item.name || item.item_id) + " · " + escapeHtml(statusLabel(item.coverage)) +
          '</span><em>' + escapeHtml(item.reason_zh || "") + " · " + escapeHtml(baseline) + "</em></div>";
      }).join("") || '<div class="line"><em>这一档没有需要处理的条目。</em></div>';
      return '<details class="group"' + (open ? " open" : "") + "><summary><span><b>" + escapeHtml(module.name || module.module_id) +
        '</b></span><span class="counts">' + chip("", "未登记 " + counts.unregistered) + chip("", "不更新 " + counts.excluded) +
        chip("registered", "已登记 " + counts.registered) + "</span></summary>" + body + "</details>";
    }).join("") || '<p class="empty-state">没有 Way 覆盖清单。</p>';
  }

  function registryBucket(id, moduleName) {
    if (id.indexOf("docs.") === 0) return "框架文档";
    if (id.indexOf("wiki.") === 0) return "Wiki";
    if (moduleName === "官方资料" || id.indexOf("source.knowledge") === 0) return "官方资料";
    if (moduleName === "前沿追踪") return "前沿新闻";
    if (moduleName === "课程中心") return "教材";
    return "其他";
  }

  function renderRegistry(monitor) {
    var seen = {};
    var groups = {};
    function add(id, name, baseline) {
      if (!id || seen[id]) return;
      seen[id] = true;
      var bucket = registryBucket(id, name.module || "");
      groups[bucket] = groups[bucket] || [];
      groups[bucket].push({ id: id, label: name.label || id, baseline: baseline });
    }
    ((monitor.coverage && monitor.coverage.modules) || []).forEach(function (module) {
      (module.items || []).forEach(function (item) {
        if (!item.radar_source_id) return;
        if (item.coverage !== "registered" && item.coverage !== "updating") return;
        add(String(item.radar_source_id), { module: module.name, label: item.name || item.item_id }, item.baseline);
      });
    });
    (monitor.sources || []).forEach(function (row) {
      add(String(row.source_id || ""), { module: row.module_name, label: row.module_name || row.source_id }, (row.baseline || {}).presence || ((row.baseline || {}).status === "verified" ? "present" : "absent"));
    });
    var order = ["官方资料", "框架文档", "Wiki", "前沿新闻", "教材", "其他"];
    $("moduleTable").innerHTML = order.filter(function (name) { return groups[name] && groups[name].length; }).map(function (name) {
      var rows = groups[name];
      var present = rows.filter(function (row) { return row.baseline === "present"; }).length;
      var body = state.registryMode === "sources" ? rows.map(function (row) {
        return '<div class="line"><span>' + escapeHtml(row.label) + '</span><em>' +
          escapeHtml(row.baseline === "present" ? "有基线" : (row.baseline === "not_applicable" ? "不适用" : "无基线")) + "</em></div>";
      }).join("") : "";
      return '<details class="group"' + (state.registryMode === "sources" ? " open" : "") + "><summary><span><b>" +
        escapeHtml(name) + '</b></span><span class="counts">' + chip("success", rows.length + " 个源") +
        chip("success", "基线 " + present) + "</span></summary>" + body + "</details>";
    }).join("");
    $("sourceTable").innerHTML = "";
    $("sourceEmpty").hidden = true;
  }

  function renderReports(index) {
    var reports = ((index && index.reports) || []).slice().sort(function (left, right) {
      return String(right.date || "").localeCompare(String(left.date || ""));
    });
    $("reportList").innerHTML = reports.map(function (report) {
      var updated = report.summary && report.summary.updated != null ? report.summary.updated : "未上报";
      return '<article class="group"><div class="group-head day"><time>' + escapeHtml(report.date) +
        "</time><span>更新 " + escapeHtml(updated) + "</span>" + chip(report.status) + "</div></article>";
    }).join("") || '<p class="empty-state">没有历史日报。</p>';
  }

  function providerPurpose(provider) {
    var id = String(provider.id || provider.name || "").toLowerCase();
    if (id.indexOf("deepseek") >= 0) return "翻译失败时的回退模型";
    if (id.indexOf("google") >= 0) return "正文翻译的默认通道";
    return "翻译提供方";
  }

  function renderRuntime(runtime) {
    var schedule = $("scheduleConfig");
    var deepseek = $("deepseekConfig");
    if (!schedule || !deepseek) return;
    if (!runtime) {
      schedule.innerHTML = '<p class="empty-state">还没有运行配置。不能把缺失的执行时间显示为当前时刻。</p>';
      deepseek.innerHTML = '<p class="empty-state">DeepSeek 开关未上报。</p>';
      return;
    }
    schedule.innerHTML = CATALOG.map(function (item) {
      var configured = (runtime.workflows || {})[item.id] || {};
      var slots = configured.slots_shanghai || [];
      return '<article class="group"><div class="group-head"><span><b>' + escapeHtml(item.name) +
        '</b><span class="sub">' + escapeHtml(item.cron) + " · 只能选这个唤醒网格上的时间</span></span>" +
        chip(configured.enabled === false ? "disabled" : "success", configured.enabled === false ? "已停用" : formatSlots(slots)) +
        "</div></article>";
    }).join("");
    var enabled = runtime.deepseek && runtime.deepseek.enabled === true;
    deepseek.innerHTML = '<article class="group"><div class="group-head"><span><b>' +
      (enabled ? "允许使用" : "不使用") + '</b><span class="sub">关闭时新闻翻译走 Google，标题增强和人物评分也不调用 DeepSeek。密钥仍只放在 GitHub Secrets。</span></span>' +
      chip(enabled ? "configured" : "not_configured", enabled ? "已打开" : "已关闭") + "</div></article>";
  }

  function renderProviders(providers) {
    $("providers").innerHTML = (providers || []).map(function (provider) {
      var status = provider.status || (provider.configured ? "configured" : "not_configured");
      return '<article class="provider"><div class="provider-head"><strong>' + escapeHtml(provider.name || provider.id) +
        "</strong>" + chip(status) + "</div><p class=\"provider-meta\">" + escapeHtml(providerPurpose(provider)) +
        "<br>" + escapeHtml(provider.model || "未上报模型") + " · 成功 " + escapeHtml(reported(provider.success_count)) +
        " · 失败 " + escapeHtml(reported(provider.failure_count)) + " · 最近使用 " +
        escapeHtml(provider.last_used_at ? formatDate(provider.last_used_at) : "未上报") + "</p></article>";
    }).join("") || '<p class="empty-state">没有提供方运行数据。</p>';
  }

  function renderDaily(update) {
    var modules = (update && update.modules) || [];
    $("dailyModules").innerHTML = modules.length ? modules.slice(0, 6).map(function (module) {
      return '<article class="module-log-item"><div class="module-log-head"><strong>' +
        escapeHtml(module.name || module.module_id) + "</strong>" + chip(module.status) +
        "</div></article>";
    }).join("") : '<p class="empty-state">今天还没有模块更新报告。</p>';
  }

  function press(selector, value) {
    Array.prototype.forEach.call(document.querySelectorAll(selector), function (button) {
      var key = button.getAttribute("data-today") || button.getAttribute("data-coverage") || button.getAttribute("data-registry");
      button.setAttribute("aria-pressed", key === value ? "true" : "false");
    });
  }

  function rerender() {
    if (!state.monitor) return;
    renderWorkflows(state.monitor);
    renderBoard(state.monitor);
    renderToday(state.monitor);
    renderCoverage(state.monitor.coverage);
    renderRegistry(state.monitor);
    renderReports(state.index);
  }

  function showError(error) {
    $("loadError").hidden = false;
    $("loadError").textContent = "监控数据加载失败：" + (error && error.message || error);
  }

  function bind() {
    $("workflowCards").addEventListener("click", function (event) {
      var button = event.target.closest("[data-workflow]");
      if (!button) return;
      var id = button.getAttribute("data-workflow");
      state.focusWorkflow = state.focusWorkflow === id ? "" : id;
      state.focusName = "";
      state.todayMode = "all";
      press("#todayFilters [data-today]", "all");
      if (location.hash !== "#today") location.hash = "today";
      showTab("today");
      rerender();
    });
    $("moduleBoard").addEventListener("click", function (event) {
      var button = event.target.closest("[data-board-module]");
      if (!button) return;
      openBoardModule(button.getAttribute("data-board-module"));
    });
    $("todayFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-today]");
      if (!button) return;
      state.todayMode = button.getAttribute("data-today");
      press("#todayFilters [data-today]", state.todayMode);
      renderToday(state.monitor);
    });
    $("coverageFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-coverage]");
      if (!button) return;
      state.coverageFilter = button.getAttribute("data-coverage");
      press("#coverageFilters [data-coverage]", state.coverageFilter);
      renderCoverage(state.monitor && state.monitor.coverage);
    });
    $("registryFilters").addEventListener("click", function (event) {
      var button = event.target.closest("[data-registry]");
      if (!button) return;
      state.registryMode = button.getAttribute("data-registry");
      press("#registryFilters [data-registry]", state.registryMode);
      renderRegistry(state.monitor);
    });
    $("monitorTabs").addEventListener("click", function (event) {
      var button = event.target.closest("button[data-tab]");
      if (!button) return;
      var tab = button.getAttribute("data-tab");
      if (location.hash !== "#" + tab) location.hash = tab;
      showTab(tab);
    });
    window.addEventListener("hashchange", function () { showTab(currentTab()); });
  }

  function load() {
    return Promise.all([
      fetchJson(SNAPSHOT_URL).catch(function (error) {
        if (String(error && error.message || "").indexOf("404") >= 0) return { __unavailable: true };
        throw error;
      }),
      fetchJson(UPDATE_URL).catch(function () { return null; }),
      fetchJson(INDEX_URL).catch(function () { return null; }),
      fetchJson(RUNTIME_URL).catch(function () { return null; })
    ]).then(function (values) {
      state.runtime = values[3];
      renderRuntime(state.runtime);
      if (values[0] && values[0].__unavailable) {
        state.monitor = null;
        state.update = values[1];
        state.index = values[2];
        renderUnavailableMonitor();
        renderDaily(state.update);
        renderReports(state.index);
        return;
      }
      state.monitor = values[0];
      state.update = values[1];
      state.index = values[2];
      renderKpis(state.monitor);
      renderWorkflows(state.monitor);
      renderBoard(state.monitor);
      renderToday(state.monitor);
      renderCoverage(state.monitor.coverage);
      renderRegistry(state.monitor);
      renderProviders(state.monitor.providers);
      renderDaily(state.update);
      renderReports(state.index);
      if (state.monitor.reports && state.monitor.reports.latest_update) {
        $("latestReportLink").href = resolveRadarPath(state.monitor.reports.latest_update);
      }
      $("snapshotMeta").textContent = "快照 " + formatDate(state.monitor.generated_at);
      $("loadError").hidden = true;
    }).catch(showError);
  }

  showTab(currentTab());
  bind();
  load();
  window.setInterval(load, 60000);
})();
