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
  var ACTIONS_URL = "https://github.com/WenXiangWu/ai-news-radar/actions/workflows/update-news.yml";
  var state = { monitor: null, update: null, index: null };

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

  function emptyMonitor() {
    return {
      schema: "radar-monitor/v1",
      generated_at: "",
      run: {
        run_id: "",
        status: "pending",
        started_at: null,
        finished_at: null,
        duration_ms: 0,
        report_path: radarDataUrl("radar-run-report.json")
      },
      summary: {
        modules: 0,
        sources: 0,
        enabled_sources: 0,
        healthy_sources: 0,
        failed_sources: 0,
        baseline_verified: 0,
        baseline_pending: 0,
        updated: 0,
        translated: 0
      },
      providers: [],
      modules: [],
      sources: [],
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
      escapeHtml(message) + "</div>";
    $("runStatus").className = "status-chip warn";
    $("runStatus").textContent = "无可用快照";
    $("runSummary").innerHTML =
      '<div class="run-cell"><label>状态</label><strong>' +
      escapeHtml(message) + "</strong></div>";
    $("providers").innerHTML = '<p class="empty-state">暂无可验证的提供方运行数据。</p>';
    $("moduleTable").innerHTML =
      '<tr><td colspan="7">监控快照尚未发布，不能把缺失数据显示为 0。</td></tr>';
    $("sourceTable").innerHTML =
      '<tr><td colspan="8">监控快照尚未发布，不能判定数据源状态。</td></tr>';
    $("sourceEmpty").hidden = true;
    $("snapshotMeta").textContent = "尚未发布监控快照";
    showError(message);
  }

  function formatDate(value) {
    if (!value) return "未记录";
    var date = new Date(value);
    if (isNaN(date.getTime())) return escapeHtml(value);
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function formatDuration(value) {
    var ms = Number(value || 0);
    if (!ms) return "未记录";
    if (ms < 1000) return ms + " ms";
    return (ms / 1000).toFixed(ms >= 10000 ? 0 : 1) + " s";
  }

  function formatSchedule(schedule) {
    if (!schedule || typeof schedule !== "object") return "由注册表配置";
    var cron = schedule.cron || "";
    var timezone = schedule.timezone || "";
    return escapeHtml((cron || "按任务") + (timezone ? " · " + timezone : ""));
  }

  function statusClass(status) {
    var value = String(status || "").toLowerCase();
    if (["success", "ok", "healthy", "configured"].indexOf(value) >= 0) return "good";
    if (["pending", "degraded", "not_configured", "unknown"].indexOf(value) >= 0) return "warn";
    if (["failed", "partial", "error", "blocked"].indexOf(value) >= 0) return "bad";
    return "";
  }

  function statusLabel(status) {
    var labels = {
      success: "成功", ok: "正常", healthy: "可达", configured: "已配置",
      pending: "待执行", degraded: "降级", not_configured: "未配置",
      blocked: "阻塞",
      unknown: "未知", failed: "失败", partial: "部分成功",
      error: "错误", disabled: "已停用", not_due: "未到时间"
    };
    return labels[String(status || "")] || String(status || "未知");
  }

  function chip(status) {
    return '<span class="mini-chip ' + statusClass(status) + '">' +
      escapeHtml(statusLabel(status)) + "</span>";
  }

  function renderKpis(summary) {
    var items = [
      ["modules", "模块"],
      ["sources", "数据源"],
      ["healthy_sources", "可达"],
      ["failed_sources", "失败"],
      ["baseline_verified", "基线已验证"],
      ["updated", "今日更新"],
      ["translated", "今日翻译"]
    ];
    $("kpis").innerHTML = items.map(function (item) {
      return '<div class="kpi"><span class="kpi-value">' +
        escapeHtml(summary[item[0]] || 0) +
        '</span><span class="kpi-label">' + item[1] + "</span></div>";
    }).join("");
  }

  function renderRun(run) {
    var status = run && run.status || "unknown";
    $("runStatus").className = "status-chip " + statusClass(status);
    $("runStatus").textContent = statusLabel(status);
    if (!run) {
      $("runSummary").innerHTML = '<div class="run-cell"><label>状态</label><strong>尚未收到 Radar 报告</strong></div>';
      return;
    }
    $("runSummary").innerHTML = [
      ["运行 ID", run.run_id || "未记录"],
      ["触发请求", run.trigger_request_id || "定时执行"],
      ["开始", formatDate(run.started_at)],
      ["结束", formatDate(run.finished_at)],
      ["耗时", formatDuration(run.duration_ms)]
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
        '<p class="provider-meta">' + escapeHtml(provider.model || "默认模型") +
        " · 成功 " + escapeHtml(provider.success_count || 0) +
        " · 失败 " + escapeHtml(provider.failure_count || 0) +
        " · 回退 " + escapeHtml(provider.fallback_count || 0) + "</p>" +
        '<div class="provider-stat"><span>最近使用</span><strong>' +
        escapeHtml(formatDate(provider.last_used_at)) + "</strong></div>" +
        "</article>";
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
        "<td>" + escapeHtml(module.updated || 0) + "</td>" +
        "<td>" + escapeHtml(module.translated || 0) + "</td>" +
        "</tr>";
    }).join("") || '<tr><td colspan="7">没有注册模块。</td></tr>';
  }

  function sourceMatches(row) {
    var query = String($("sourceFilter").value || "").trim().toLowerCase();
    var status = $("sourceStatusFilter").value;
    if (status && String(row.status || "") !== status) return false;
    if (!query) return true;
    return [row.source_id, row.module_id, row.module_name, row.adapter, row.locator]
      .join(" ").toLowerCase().indexOf(query) >= 0;
  }

  function renderSources(sources) {
    var filtered = (sources || []).filter(sourceMatches);
    $("sourceEmpty").hidden = filtered.length !== 0;
    $("sourceTable").innerHTML = filtered.map(function (row) {
      var reachability = row.reachability || {};
      var baseline = row.baseline || {};
      return "<tr>" +
        '<td><div class="source-main"><strong>' + escapeHtml(row.module_name || row.module_id) +
        '</strong><span>' + escapeHtml(row.source_id) + '</span><span class="source-locator" title="' +
        escapeHtml(row.locator || "") + '">' + escapeHtml(row.locator || "未声明定位") + "</span></div></td>" +
        "<td>" + escapeHtml(row.adapter || "unknown") + "</td>" +
        "<td>" + formatSchedule(row.schedule) + "<br><span class=\"source-locator\">" +
        escapeHtml(row.next_run_at ? "下次 " + formatDate(row.next_run_at) : "未上报下次执行") + "</span></td>" +
        "<td>" + chip(row.status) + "</td>" +
        "<td>" + chip(reachability.status) + '<br><span class="source-locator">' +
        escapeHtml((reachability.latency_ms || 0) + " ms · " + (reachability.fetched || 0) + " fetched") + "</span></td>" +
        "<td>" + chip(baseline.status) + '<br><span class="source-locator">' +
        escapeHtml(baseline.cursor_run_id || "无游标") + "</span></td>" +
        '<td><span class="source-locator">' + escapeHtml(formatDate(row.last_run_at)) + "</span></td>" +
      '<td><a class="source-action" href="' + ACTIONS_URL +
      '" target="_blank" rel="noopener noreferrer">在 Actions 执行</a></td>' +
      "</tr>";
    }).join("");
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
        escapeHtml(module.updated || 0) + "</span><span>翻译 " +
        escapeHtml(module.translated || 0) + "</span></div>" +
        (links ? '<div class="translation-links">' + links + "</div>" : "") +
        "</article>";
    }).join("") || '<p class="empty-state">今天还没有模块更新报告。</p>';
  }

  function renderReports(index) {
    var reports = index && index.reports || [];
    $("reportList").innerHTML = reports.map(function (report) {
      return '<a class="report-item" href="' + escapeHtml(resolveRadarPath(report.path || "#")) + '">' +
        "<strong>" + escapeHtml(report.date) + "</strong>" +
        '<span>' + escapeHtml(statusLabel(report.status)) + " · 更新 " +
        escapeHtml((report.summary || {}).updated || 0) + "</span></a>";
    }).join("") || '<p class="empty-state">没有历史日报。</p>';
  }

  function showError(error) {
    $("loadError").hidden = false;
    $("loadError").textContent = "监控数据加载失败：" + (error && error.message || error);
  }

  function bind() {
    $("sourceFilter").addEventListener("input", function () {
      renderSources(state.monitor && state.monitor.sources);
    });
    $("sourceStatusFilter").addEventListener("change", function () {
      renderSources(state.monitor && state.monitor.sources);
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
        return;
      }
      state.monitor = values[0];
      state.update = values[1];
      state.index = values[2];
      var summary = state.monitor.summary || {};
      renderKpis(summary);
      renderRun(state.monitor.run);
      renderProviders(state.monitor.providers);
      renderModules(state.monitor.modules);
      renderSources(state.monitor.sources);
      renderDaily(state.update);
      renderReports(state.index);
      if (state.monitor.reports && state.monitor.reports.latest_update) {
        $("latestReportLink").href = resolveRadarPath(state.monitor.reports.latest_update);
      }
      $("snapshotMeta").textContent = "快照 " + formatDate(state.monitor.generated_at);
    }).catch(showError);
  }

  bind();
  load();
  window.setInterval(load, 60000);
})();
