// TSW Hud sync client - orchestrates pull (server -> IndexedDB) and push
// (queued offline edits -> server), and exposes a simple status object for
// pages to build the sync banner UI against (see design_previews/preview_
// tablet_icons_sync_ui.html for the approved design this feeds).

const TSWSync = (() => {
  const PULL_TIMEOUT_MS = 4000;
  const AUTO_SYNC_INTERVAL_MS = 30 * 1000;

  let status = { online: false, lastSyncedAt: null, pendingCount: 0, syncing: false };
  const listeners = [];

  function notify() {
    listeners.forEach((fn) => fn({ ...status }));
  }

  function onStatusChange(fn) {
    listeners.push(fn);
    fn({ ...status }); // fire immediately with current state
  }

  async function fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  async function refreshPendingCount() {
    const pending = await TSWOfflineDB.getPendingChanges();
    status.pendingCount = pending.length;
  }

  async function pull() {
    const since = await TSWOfflineDB.getMeta('last_synced_at', '1970-01-01T00:00:00');
    let afterJourneyId = 0;
    let afterTrainClassId = 0;
    let journeysMore = true;
    let trainClassesMore = true;
    let serverTime = since;

    // Loop pages until both journeys and train_classes are fully caught
    // up - each individual request is bounded/fast regardless of total
    // dataset size, so this reliably completes even on a large first-ever
    // sync, rather than one giant slow all-or-nothing request.
    while (journeysMore || trainClassesMore) {
      const params = new URLSearchParams({
        since, after_journey_id: afterJourneyId, after_train_class_id: afterTrainClassId,
      });
      const res = await fetchWithTimeout(`/api/sync/changes?${params}`, {}, PULL_TIMEOUT_MS);
      if (!res.ok) throw new Error('pull failed: ' + res.status);
      const data = await res.json();

      if (data.journeys && data.journeys.length) await TSWOfflineDB.putAll('journeys', data.journeys);
      if (data.train_classes && data.train_classes.length) await TSWOfflineDB.putAll('train_classes', data.train_classes);

      journeysMore = data.journeys_has_more;
      trainClassesMore = data.train_classes_has_more;
      afterJourneyId = data.last_journey_id;
      afterTrainClassId = data.last_train_class_id;
      serverTime = data.server_time;
    }

    await TSWOfflineDB.setMeta('last_synced_at', serverTime);
    return { server_time: serverTime };
  }

  async function push() {
    const pending = await TSWOfflineDB.getPendingChanges();
    if (!pending.length) return;

    const body = { journeys: [], segments: [], stops: [], train_classes: [] };
    for (const change of pending) {
      body[change.kind].push({ id: change.id, updated_at: change.updated_at, fields: change.fields });
    }

    const res = await fetchWithTimeout('/api/sync/push', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }, PULL_TIMEOUT_MS);
    if (!res.ok) throw new Error('push failed: ' + res.status);

    // Whether applied or rejected (stale), the change is "resolved" from
    // this device's point of view - either it went through, or the
    // server's newer copy wins and the next pull brings back the truth.
    // Either way, drop it from the local pending queue.
    const localIdsToClear = pending.map((c) => c.localId);
    await TSWOfflineDB.clearPendingChanges(localIdsToClear);
  }

  let rerunRequested = false;

  async function sync() {
    if (status.syncing) {
      // Another sync is already running. Don't start a second one
      // concurrently, but don't just drop this request either - anything
      // queued after the in-flight sync already took its snapshot of
      // pending changes would otherwise be stranded until the next
      // AUTO_SYNC_INTERVAL_MS timer. Flag it so the in-flight sync runs
      // itself again once it finishes.
      rerunRequested = true;
      return status;
    }
    status.syncing = true;
    notify();
    try {
      await push();  // push first, so a subsequent pull can bring back the server's resolved state
      await pull();
      status.online = true;
      status.lastSyncedAt = await TSWOfflineDB.getMeta('last_synced_at', null);
    } catch (e) {
      status.online = false;
    } finally {
      await refreshPendingCount();
      status.syncing = false;
      notify();
    }
    if (rerunRequested) {
      rerunRequested = false;
      return sync(); // catch up on whatever arrived mid-flight, right away rather than waiting for the timer
    }
    return status;
  }

  async function queueEdit(kind, id, fields) {
    await TSWOfflineDB.applyOptimisticEdit(kind, id, fields);
    await TSWOfflineDB.queueChange(kind, id, fields);
    await refreshPendingCount();
    notify();
    // Try an immediate sync if we might be online - harmless no-op if not.
    sync();
  }

  function getStatus() {
    return { ...status };
  }

  async function init() {
    await refreshPendingCount();
    status.lastSyncedAt = await TSWOfflineDB.getMeta('last_synced_at', null);
    notify();
    sync();
    setInterval(sync, AUTO_SYNC_INTERVAL_MS);
  }

  if (typeof indexedDB !== 'undefined') {
    init();
  }

  return { sync, queueEdit, getStatus, onStatusChange };
})();
