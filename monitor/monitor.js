(function () {
  "use strict";

  function radarDataUrl(path) {
    return new URL("../data/" + String(path).replace(/^\/+/, ""), window.location.href).toString();
  }

  function resolveRadarPath(path) {
    var value = String(path || "");
    if (!value || value === "#") return value || "#";
    if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(value)) return value;
    if (value.charAt(0) === "/") {
      return new URL(".." + value, window.location.href).toString();
    }
    return new URL(value, window.location.href).toString();
  }

  var SNAPSHOT_URL = radarDataUrl("radar-monitor.json");
  var UPDATE_URL = radarDataUrl("radar-update-report.json");
  var INDEX_URL = radarDataUrl("radar-reports/index.json");
  var TABS = ["today", "registry", "coverage", "reports", "config"];
  var state = {
    monitor: null,
    update: null,
    index: null,
    coverageFilter: "all"
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
      var selected = button.getAttribute("data-tab") === active;
      button.setAttribute("aria-selected", selected ? "true" : "false");
    });
  }

  function emptyMonitor() {
    return {
      schema: "radar-monitor/v2",
      generated_at: "",
      run: {
        run_id: "",
        status: "pending",
        started_at: null,
        finished_at: null,
        duration_ms: 0,
        report_path: radarDataUrl("radar-run-report.json")
      },
      summary: {},
      providers: [],
      modules: [],
      sources: [],
      workflows: [],
      coverage: { modules: [] },
      reports: {
        latest_update: radarDataUrl("radar-update-report.json"),
        latest_validation: radarDataUrl("source-validation.json"),
        history_index: radarDataUrl("radar-reports/index.json")
      }
    };
  }

  function renderUnavailableMonitor() {
    var message = "Radar 尚未发布可验证的监控快照。本轮执行可能仍在运行、失败或尚未完成发布。";
    $("kpis").innerHTML =
      '<div class="empty-state monitor-unavailable">数据不可判定：' +
      escapeHtml(message) + " 不能把缺失数据显示为 0。</div>";
    $("runStatus").className = "status-chip warn";
    $("runStatus").textContent = "无可用快照";
    $("runSummary").innerHTML =
      '<div class="run-cell"><label>状态</label><strong>' +
      escapeHtml(message) + "</strong></div>";
    $("providers").innerHTML = '<p class="empty-state">暂无可验证的提供方运行数据。</p>';
    $("moduleTable").innerHTML =
      '<tr><td colspan="5">监控快照尚未发布，不能把缺失数据显示为 0。</td></tr>';
    $("sourceTable").innerHTML =
      '<tr><td colspan="8">监控快照尚未发布，不能判定数据源状态。</td></tr>';
    $("sourceEmpty").hidden = true;
    $("workflowCards").innerHTML = '<tr><td colspan="5">监控快照尚未发布，不能判定 workflow。</td></tr>';
    $("todayMessages").innerHTML = "";
    $("coverageModules").innerHTML = '<p class="empty-state">监控快照尚未发布，不能判定 Way 覆盖。</p>';
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
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    }).format(date);
  }

  function formatSchedule(schedule) {
    if (!schedule || typeof schedule !== "object") return "由注册表配置";
    var cron = schedule.cron || "";
    var timezone = schedule.timezone || "";
    return escapeHtml((cron || "按任务") + (timezone ? " · " + timezone : ""));
  }

  function statusClass(status) {
    var value = String(status || "").toLowerCase();
    if (["success", "ok", "healthy", "configured", "present", "updating"].indexOf(value) >= 0) return "good";
    if (["pending", "degraded", "not_configured", "unknown", "registered", "absent"].indexOf(value) >= 0) return "warn";
    if (["failed", "partial", "error", "blocked", "excluded"].indexOf(value) >= 0) return "bad";
    return "";
  }

  function statusLabel(status) {
    var labels = {
      success: "成功", ok: "正常", healthy: "可达", configured: "已配置",
      pending: "待执行", degraded: "降级", not_configured: "未配置",
      blocked: "阻塞", unknown: "未知", failed: "失败", partial: "部分成功",
      error: "错误", disabled: "已停用", not_due: "未到时间",
      present: "有基线", absent: "无基线", verified: "已验证",
      updating: "更新中", registered: "已登记", excluded: "不更新",
      unregistered: "未登记", incremental: "增量", baseline_only: "仅基线"
    };
    return labels[String(status || "")] || String(status || "未知");
  }

  function chip(status) {
    return '<span class="mini-chip ' + statusClass(status) + '">' +
      escapeHtml(statusLabel(status)) + "</span>";
  }

  function pushText(push) {
    var value = push || {};
    if (!value.attempted) return "未推送";
    return value.ok ? "推送成功" : "推送失败";
  }

  function fillSelect(select, values, placeholder) {
    var current = select.value;
    var options = ['<option value="">' + escapeHtml(placeholder) + "</option>"];
    values.forEach(function (value) {
      options.push('<option value="' + escapeHtml(value) + '">' + escapeHtml(value) + "</option>");
    });
    select.innerHTML = options.join("");
    if (values.indexOf(current) >= 0) select.value = current;
  }

  function renderKpis(monitor) {
    var workflows = monitor.workflows || [];
    var failed = 0;
    var unpublished = 0;
    workflows.forEach(function (workflow) {
      var sources = workflow.sources || [];
      var push = workflow.push || {};
      sources.forEach(function (source) {
        if (String(source.status || "") === "failed") failed += 1;
        if (!push.attempted) unpublished += 1;
      });
      if (!sources.length && workflow.conclusion === "failure") failed += 1;
    });
    var summary = monitor.summary || {};
    var items = [
      [workflows.length, "Workflow"],
      [failed, "今日失败"],
      [summary.sources == null ? "未上报" : summary.sources, "已登记源"],
      [unpublished, "未推送"]
    ];
    $("kpis").innerHTML = items.map(function (item) {
      return '<div class="kpi"><span class="kpi-value">' + escapeHtml(item[0]) +
        '</span><span class="kpi-label">' + item[1] + "</span></div>";
    }).join("");
  }

  function renderRun(run) {
    var status = run && run.status || "unknown";
    $("runStatus").className = "status-chip " + statusClass(status);
    $("runStatus").textContent = statusLabel(status);
    $("runSummary").hidden = false;
    if (!run) {
      $("runSummary").innerHTML = '<div class="run-cell"><label>状态</label><strong>尚未收到 Radar 报告</strong></div>';
      return;
    }
    $("runSummary").innerHTML = [
      ["运行 ID", run.run_id || "未记录"],
      ["开始", formatShanghaiMinute(run.started_at)],
      ["结束", formatShanghaiMinute(run.finished_at)]
    ].map(function (item) {
      return '<div class="run-cell"><label>' + item[0] + "</label><strong>" +
        escapeHtml(item[1]) + "</strong></div>";
    }).join("");
  }

  function renderProviders(providers) {
    $("providers").innerHTML = (providers || []).map(function (provider) {
      var status = provider.status || (provider.configured ? "configured" : "not_configured");
      return '<article class="provider">' +
        '<div class="provider-head"><strong>' + escapeHtml(provider.name || provider.id) +
        "</strong>" + chip(status) + "</div>" +
        '<p class="provider-meta">' + escapeHtml(provider.model || "未上报模型") +
        " · 成功 " + escapeHtml(reported(provider.success_count)) +
        " · 失败 " + escapeHtml(reported(provider.failure_count)) +
        " · 回退 " + escapeHtml(reported(provider.fallback_count)) + "</p>" +
        '<div class="provider-stat"><span>最近使用</span><strong>' +
        escapeHtml(provider.last_used_at ? formatDate(provider.last_used_at) : "未上报") +
        "</strong></div></article>";
    }).join("") || '<p class="empty-state">没有提供方运行数据。</p>';
  }

  function renderModules(modules) {
    $("moduleTable").innerHTML = (modules || []).map(function (module) {
      return "<tr>" +
        '<td><div class="module-name"><strong>' +
        escapeHtml(module.name || module.module_id) + "</strong><span>" +
        escapeHtml(module.module_id) + "</span></div></td>" +
        "<td>" + escapeHtml(module.kind || "source") + "</td>" +
        "<td>" + chip(module.status) + "</td>" +
        "<td>" + escapeHtml((module.task_ids || []).length) + "</td>" +
        "<td>" + escapeHtml((module.source_ids || []).length) + "</td>" +
        "</tr>";
    }).join("") || '<tr><td colspan="5">没有注册模块。</td></tr>';
  }

  function baselinePresence(baseline) {
    var value = baseline || {};
    if (value.presence) return value.presence;
    return value.status === "verified" ? "present" : "absent";
  }

  function sourceMatches(row) {
    var query = String($("sourceFilter").value || "").trim().toLowerCase();
    var status = $("sourceStatusFilter").value;
    var adapter = $("sourceAdapterFilter").value;
    var baseline = $("sourceBaselineFilter").value;
    if (status && String(row.status || "") !== status) return false;
    if (adapter && String(row.adapter || "") !== adapter) return false;
    if (baseline && baselinePresence(row.baseline) !== baseline) return false;
    if (!query) return true;
    return [row.source_id, row.module_id, row.module_name, row.adapter, row.locator]
      .join(" ").toLowerCase().indexOf(query) >= 0;
  }

  function renderSources(sources) {
    var rows = sources || [];
    var adapters = [];
    rows.forEach(function (row) {
      if (row.adapter && adapters.indexOf(row.adapter) < 0) adapters.push(row.adapter);
    });
    adapters.sort();
    fillSelect($("sourceAdapterFilter"), adapters, "全部适配器");
    var filtered = rows.filter(sourceMatches);
    $("sourceEmpty").hidden = filtered.length !== 0;
    $("sourceTable").innerHTML = filtered.map(function (row) {
      var reachability = row.reachability || {};
      var baseline = row.baseline || {};
      var presence = baselinePresence(baseline);
      var baselineTime = presence === "not_applicable" ? "不适用" : formatShanghaiMinute(baseline.at);
      return "<tr>" +
        '<td><div class="source-main"><strong>' + escapeHtml(row.module_name || row.module_id) +
        '</strong><span>' + escapeHtml(row.source_id) + "</span></div></td>" +
        "<td>" + escapeHtml(row.adapter || "unknown") + "</td>" +
        "<td>" + formatSchedule(row.schedule) + "</td>" +
        "<td>" + chip(row.status) + "</td>" +
        "<td>" + chip(reachability.status) + "</td>" +
        "<td>" + chip(presence) + "</td>" +
        "<td>" + escapeHtml(baselineTime) + "</td>" +
        "<td>" + escapeHtml(formatDate(row.last_run_at)) + "</td>" +
        "</tr>";
    }).join("") || '<tr><td colspan="8">没有符合条件的数据源。</td></tr>';
  }

  function workflowName(workflow) {
    return workflow.name || workflow.workflow_id || "";
  }

  function todaySourceRows(workflows) {
    var rows = [];
    (workflows || []).forEach(function (workflow) {
      (workflow.sources || []).forEach(function (source) {
        rows.push({ workflow: workflow, source: source });
      });
    });
    return rows;
  }

  function todayMatches(row) {
    var workflowFilter = $("todayWorkflowFilter").value;
    var status = $("todayStatusFilter").value;
    var query = String($("todayQuery").value || "").trim().toLowerCase();
    if (workflowFilter && workflowName(row.workflow) !== workflowFilter && row.workflow.workflow_id !== workflowFilter) {
      return false;
    }
    if (status && String(row.source.status || "") !== status) return false;
    if (!query) return true;
    return [row.source.source_id, row.source.run_kind, row.source.error, workflowName(row.workflow)]
      .join(" ").toLowerCase().indexOf(query) >= 0;
  }

  function renderWorkflows(workflows) {
    var rows = workflows || [];
    fillSelect($("todayWorkflowFilter"), rows.map(workflowName).filter(Boolean), "全部 workflow");
    $("workflowCards").innerHTML = rows.map(function (workflow) {
      var push = workflow.push || {};
      var link = workflow.html_url
        ? '<a href="' + escapeHtml(workflow.html_url) + '">运行记录</a>'
        : "未上报";
      return "<tr><td><strong>" + escapeHtml(workflowName(workflow)) +
        '</strong><div class="source-locator">' + escapeHtml(workflow.schedule || "") + "</div></td>" +
        "<td>" + escapeHtml(formatShanghaiMinute(workflow.started_at)) + " – " +
        escapeHtml(formatShanghaiMinute(workflow.finished_at)) + "</td>" +
        "<td>" + chip(workflow.conclusion) + "</td>" +
        "<td>" + escapeHtml(pushText(push)) + "</td>" +
        "<td>" + link + "</td></tr>";
    }).join("") || '<tr><td colspan="5">还没有 workflow 状态。</td></tr>';
    var messages = todaySourceRows(rows).filter(todayMatches);
    $("todayEmpty").hidden = messages.length !== 0;
    $("todayMessages").innerHTML = messages.map(function (row) {
      var source = row.source;
      var baseline = source.baseline === "not_applicable"
        ? "不适用"
        : (source.baseline_at ? "有基线 · " + formatShanghaiMinute(source.baseline_at) : (source.baseline || "未上报"));
      return "<tr><td>" + escapeHtml(workflowName(row.workflow)) + "</td><td>" +
        escapeHtml(source.source_id) + "</td><td>" + chip(source.status) + "</td><td>" +
        escapeHtml(statusLabel(source.run_kind)) + "</td><td>" + escapeHtml(baseline) +
        "</td><td>" + escapeHtml(source.error || "") + "</td></tr>";
    }).join("");
  }

  function baselineLabel(item) {
    if (!item || item.baseline === "not_applicable") return "不适用";
    if (item.baseline === "present") {
      return item.baseline_at ? "有基线 · " + formatShanghaiMinute(item.baseline_at) : "有基线 · 无基线时间";
    }
    return "无基线";
  }

  function renderCoverage(coverage) {
    var filter = state.coverageFilter || "all";
    var moduleFilter = $("coverageModuleFilter").value;
    var query = String($("coverageQuery").value || "").trim().toLowerCase();
    var modules = (coverage && coverage.modules) || [];
    fillSelect($("coverageModuleFilter"), modules.map(function (module) {
      return module.name || module.module_id;
    }), "全部模块");
    if (moduleFilter) $("coverageModuleFilter").value = moduleFilter;
    Array.prototype.forEach.call(document.querySelectorAll("#coverageFilters [data-coverage]"), function (button) {
      button.setAttribute("aria-pressed", button.getAttribute("data-coverage") === filter ? "true" : "false");
    });
    var visible = modules.filter(function (module) {
      if (!moduleFilter) return true;
      return (module.name || module.module_id) === moduleFilter;
    });
    $("coverageModules").innerHTML = visible.map(function (module) {
      var items = (module.items || []).filter(function (item) {
        if (filter !== "all" && item.coverage !== filter) return false;
        if (!query) return true;
        return [item.name, item.item_id, item.reason_zh, item.workflow_id]
          .join(" ").toLowerCase().indexOf(query) >= 0;
      });
      var rows = items.map(function (item) {
        return "<tr><td>" + escapeHtml(item.name || item.item_id) + "</td><td>" +
          chip(item.coverage) + "</td><td>" + escapeHtml(item.reason_zh || "") + "</td><td>" +
          escapeHtml(baselineLabel(item)) + "</td><td>" + escapeHtml(item.workflow_id || "") + "</td></tr>";
      }).join("") || '<tr><td colspan="5">没有符合筛选的条目。</td></tr>';
      return '<article class="coverage-module"><h3>' + escapeHtml(module.name || module.module_id) +
        '</h3><div class="table-wrap"><table class="coverage-table"><thead><tr><th>条目</th><th>结论</th><th>原因</th><th>基线</th><th>Workflow</th></tr></thead><tbody>' +
        rows + "</tbody></table></div></article>";
    }).join("") || '<p class="empty-state">没有 Way 覆盖清单。</p>';
  }

  function renderDaily(update) {
    var modules = update && update.modules || [];
    $("dailyModules").innerHTML = modules.map(function (module) {
      var links = (module.translation_links || []).map(function (link) {
        return '<a href="' + escapeHtml(resolveRadarPath(link.url || link.path || "#")) + '">' +
          escapeHtml(link.content_id || "翻译文件") + "</a>";
      }).join("");
      return '<article class="module-log-item"><div class="module-log-head"><strong>' +
        escapeHtml(module.name || module.module_id) + "</strong>" +
        chip(module.status) + "</div><div class=\"module-log-meta\"><span>" +
        escapeHtml(module.module_id) + "</span><span>更新 " +
        escapeHtml(reported(module.updated)) + "</span><span>翻译 " +
        escapeHtml(reported(module.translated)) + "</span></div>" +
        (links ? '<div class="translation-links">' + links + "</div>" : "") +
        "</article>";
    }).join("") || '<p class="empty-state">今天还没有模块更新报告。</p>';
  }

  function renderReports(index) {
    var reports = (index && index.reports) || [];
    var dateQuery = String($("reportDate").value || "").trim().toLowerCase();
    var status = $("reportStatus").value;
    var filtered = reports.filter(function (report) {
      if (status && String(report.status || "") !== status) return false;
      if (!dateQuery) return true;
      return String(report.date || "").toLowerCase().indexOf(dateQuery) >= 0;
    });
    $("reportList").innerHTML = filtered.map(function (report) {
      var updated = report.summary && report.summary.updated != null ? report.summary.updated : "未上报";
      return '<a class="report-item" href="' + escapeHtml(resolveRadarPath(report.path || "#")) + '">' +
        "<strong>" + escapeHtml(report.date) + "</strong>" +
        "<span>" + chip(report.status) + " · 更新 " + escapeHtml(updated) + "</span></a>";
    }).join("") || '<p class="empty-state">没有历史日报。</p>';
  }

  function showError(error) {
    $("loadError").hidden = false;
    $("loadError").textContent = "监控数据加载失败：" + (error && error.message || error);
  }

  function rerender() {
    if (!state.monitor) return;
    renderSources(state.monitor.sources);
    renderWorkflows(state.monitor.workflows);
    renderCoverage(state.monitor.coverage);
    renderReports(state.index);
  }

  function bind() {
    ["sourceFilter", "todayQuery", "coverageQuery", "reportDate"].forEach(function (id) {
      $(id).addEventListener("input", rerender);
    });
    ["sourceStatusFilter", "sourceAdapterFilter", "sourceBaselineFilter", "todayWorkflowFilter", "todayStatusFilter", "coverageModuleFilter", "reportStatus"].forEach(function (id) {
      $(id).addEventListener("change", rerender);
    });
    $("coverageFilters").addEventListener("click", function (event) {
      var button = event.target.closest("button[data-coverage]");
      if (!button) return;
      state.coverageFilter = button.getAttribute("data-coverage");
      renderCoverage(state.monitor && state.monitor.coverage);
    });
    $("monitorTabs").addEventListener("click", function (event) {
      var button = event.target.closest("button[data-tab]");
      if (!button) return;
      var tab = button.getAttribute("data-tab");
      if (location.hash !== "#" + tab) location.hash = tab;
      showTab(tab);
    });
    window.addEventListener("hashchange", function () {
      showTab(currentTab());
    });
  }

  function load() {
    return Promise.all([
      fetchJson(SNAPSHOT_URL).catch(function (error) {
        if (String(error && error.message || "").indexOf("404") >= 0) {
          return { __unavailable: true };
        }
        throw error;
      }),
      fetchJson(UPDATE_URL).catch(function () { return null; }),
      fetchJson(INDEX_URL).catch(function () { return null; })
    ]).then(function (values) {
      if (values[0] && values[0].__unavailable) {
        state.monitor = null;
        state.update = values[1];
        state.index = values[2];
        renderUnavailableMonitor();
        renderDaily(state.update);
        renderReports(state.index);
        renderWorkflows([]);
        renderCoverage({ modules: [] });
        return;
      }
      state.monitor = values[0];
      state.update = values[1];
      state.index = values[2];
      renderKpis(state.monitor);
      renderRun(state.monitor.run);
      renderProviders(state.monitor.providers);
      renderModules(state.monitor.modules);
      renderSources(state.monitor.sources);
      renderWorkflows(state.monitor.workflows);
      renderCoverage(state.monitor.coverage);
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
