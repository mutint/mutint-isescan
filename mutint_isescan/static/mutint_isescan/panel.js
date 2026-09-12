/* The ISEScan panel: the run list under the options, polled while a run is under way.
 *
 * Runs inside core's fieldset on the Import data page. Two things here are the panel's and
 * not core's: disabling the fieldset's enable box when the tool is not installed -- the box
 * is core's markup, reached by walking up to the fieldset -- and the run table, which asks
 * the queue through /isescan/runs whether a queued row is waiting for a worker.
 */
(function () {
    "use strict";
    var panel = document.querySelector(".isescan-panel");
    if (!panel) { return; }
    var runsEl = panel.querySelector(".isescan-runs");
    var RUNS_URL = panel.getAttribute("data-runs-url");
    var POLL_MS = 5000;
    var pollTimer = null;

    var fieldset = panel.closest("fieldset");
    var enable = fieldset ? fieldset.querySelector(".annotator-enable") : null;
    if (panel.getAttribute("data-available") !== "1" && enable) {
        enable.checked = false;
        enable.disabled = true;
    }

    function esc(text) {
        var div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        return div.innerHTML;
    }

    function whenLocal(iso) {
        if (!iso) { return ""; }
        var when = new Date(iso);
        return isNaN(when.getTime()) ? "" : when.toLocaleString();
    }

    function statusCell(run) {
        if (run.status === "installed") { return '<span class="label label-success">Installed</span>'; }
        if (run.status === "unchanged") { return '<span class="label label-default">Nothing found</span>'; }
        if (run.status === "failed") { return '<span class="label label-danger">Failed</span>'; }
        if (run.status === "cancelled") { return '<span class="label label-default">Cancelled</span>'; }
        if (run.status === "running") { return '<span class="label label-info">Running</span>'; }
        var note = "";
        if (run.queue_status === "READY" || run.queue_status === "") {
            note = '<br><small style="color: #a94442;">waiting for a worker &mdash; ' +
                   'run <code>./mutint db_worker</code></small>';
        }
        return '<span class="label label-default">Queued</span>' + note;
    }

    function resultCell(run) {
        var bits = [];
        if (run.status === "installed") {
            bits.push(esc(run.removed) + " removed, " + esc(run.added) + " added");
        } else if (run.status === "unchanged") {
            bits.push("annotation left as it was");
        } else if (run.error) {
            bits.push('<span style="color: #a94442;">' + esc(run.error) + "</span>");
        }
        if (run.log_url) { bits.push('<a href="' + esc(run.log_url) + '">log</a>'); }
        if (run.finished && run.delete_url) {
            bits.push('<a href="#" class="isescan-delete" data-url="' + esc(run.delete_url) +
                      '">delete</a>');
        }
        return bits.join(" &middot; ") || "&mdash;";
    }

    function render(runs) {
        if (!runs.length) { runsEl.innerHTML = ""; return; }
        var rows = runs.map(function (run) {
            var opts = run.options || {};
            var how = (opts.replace_existing === false ? "added beside existing" : "replaced existing") +
                      (opts.remove_short_is ? ", complete only" : "");
            return "<tr><td>" + esc(whenLocal(run.created_at)) + "</td><td>" + statusCell(run) +
                   "</td><td><small>" + esc(how) + "</small></td><td>" + resultCell(run) + "</td></tr>";
        }).join("");
        runsEl.innerHTML = '<table class="table table-condensed" style="margin-bottom: 0.4em;">' +
            "<thead><tr><th>Started</th><th>Status</th><th>Options</th><th>Result</th></tr></thead>" +
            "<tbody>" + rows + "</tbody></table>";
        var unfinished = runs.some(function (run) { return !run.finished; });
        if (unfinished && !pollTimer) { startPolling(); }
        if (!unfinished) { stopPolling(); }
    }

    function refresh() {
        fetch(RUNS_URL, { credentials: "same-origin" }).then(function (resp) {
            return resp.ok ? resp.json() : { runs: [] };
        }).then(function (body) { render(body.runs || []); }).catch(function () {});
    }

    function startPolling() { stopPolling(); pollTimer = setInterval(refresh, POLL_MS); }
    function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

    runsEl.addEventListener("click", function (e) {
        var link = e.target.closest(".isescan-delete");
        if (!link) { return; }
        e.preventDefault();
        mutintPostJson(link.getAttribute("data-url"), {}).then(refresh).catch(function (err) {
            window.alert(err.message || String(err));
        });
    });

    // A launch from this page -- the import's own summary, or the Run annotators button --
    // lands in #import-result; a run appearing there is the cue to draw it here.
    var resultEl = document.getElementById("import-result");
    if (resultEl) {
        new MutationObserver(function () { refresh(); }).observe(resultEl, { childList: true });
    }

    var initial = document.getElementById("isescan-runs-data");
    render(initial ? JSON.parse(initial.textContent) : []);
}());
