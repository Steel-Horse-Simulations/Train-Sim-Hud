// TSW Hud offline data layer (IndexedDB).
//
// Mirrors journeys/segments/stops/train_classes locally so pages can read
// real data even when the PC isn't reachable, plus a pending_changes store
// for edits made while offline, queued until the next successful sync.
//
// IndexedDB itself has NO secure-context requirement (unlike Service
// Worker) - this works fine over plain HTTP, so it's useful even before
// HTTPS is set up, just without the "cold launch while offline" ability
// that the Service Worker adds on top.

const TSWOfflineDB = (() => {
  const DB_NAME = 'tsw_hud_offline';
  const DB_VERSION = 1;
  let dbPromise = null;

  function open() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains('journeys')) db.createObjectStore('journeys', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('train_classes')) db.createObjectStore('train_classes', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('pending_changes')) {
          db.createObjectStore('pending_changes', { keyPath: 'localId', autoIncrement: true });
        }
        if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta', { keyPath: 'key' });
      };
      req.onsuccess = (e) => resolve(e.target.result);
      req.onerror = () => reject(req.error);
    });
    return dbPromise;
  }

  function tx(storeName, mode) {
    return open().then((db) => db.transaction(storeName, mode).objectStore(storeName));
  }

  function reqToPromise(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function putAll(storeName, items) {
    const store = await tx(storeName, 'readwrite');
    for (const item of items) store.put(item);
    return new Promise((resolve, reject) => {
      store.transaction.oncomplete = () => resolve();
      store.transaction.onerror = () => reject(store.transaction.error);
    });
  }

  async function getAll(storeName) {
    const store = await tx(storeName, 'readonly');
    return reqToPromise(store.getAll());
  }

  async function get(storeName, key) {
    const store = await tx(storeName, 'readonly');
    return reqToPromise(store.get(key));
  }

  async function getMeta(key, fallback) {
    const val = await get('meta', key);
    return val ? val.value : fallback;
  }

  async function setMeta(key, value) {
    const store = await tx('meta', 'readwrite');
    store.put({ key, value });
  }

  // --- pending changes queue ---

  // --- shared timestamp helper: LOCAL time in the same "YYYY-MM-DDTHH:MM:SS"
  // shape Python's datetime.now().isoformat(timespec="seconds") produces on
  // the server. Deliberately NOT toISOString() (which is always UTC) - the
  // server's timestamps are naive local time, so a UTC-vs-local mismatch
  // would silently skew every last-write-wins comparison whenever the
  // device isn't in UTC (e.g. BST is UTC+1, so a tablet edit could appear
  // up to an hour "older" than it really was relative to the server).
  function localTimestamp() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T` +
           `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  async function queueChange(kind, id, fields) {
    // kind: 'journeys' | 'segments' | 'stops' | 'train_classes'
    const store = await tx('pending_changes', 'readwrite');
    const change = { kind, id, fields, updated_at: localTimestamp() };
    store.add(change);
    return new Promise((resolve, reject) => {
      store.transaction.oncomplete = () => resolve();
      store.transaction.onerror = () => reject(store.transaction.error);
    });
  }

  async function getPendingChanges() {
    return getAll('pending_changes');
  }

  async function clearPendingChanges(localIds) {
    const store = await tx('pending_changes', 'readwrite');
    for (const id of localIds) store.delete(id);
    return new Promise((resolve, reject) => {
      store.transaction.oncomplete = () => resolve();
      store.transaction.onerror = () => reject(store.transaction.error);
    });
  }

  // --- optimistic local apply (so edits show immediately, before the
  // queued change has actually synced to the server) ---

  async function applyOptimisticEdit(kind, id, fields) {
    if (kind === 'train_classes') {
      const record = await get('train_classes', id);
      if (record) await putAll('train_classes', [{ ...record, ...fields }]);
      return;
    }
    if (kind === 'journeys') {
      const record = await get('journeys', id);
      if (record) await putAll('journeys', [{ ...record, ...fields }]);
      return;
    }
    // stops/segments are nested inside their parent journey document, not
    // their own top-level store - find the journey containing this id.
    // Note: this scans every cached journey, which is fine for a
    // realistic per-route dataset but would be worth indexing separately
    // if this app ever caches the entire game's timetables on the tablet.
    if (kind === 'stops' || kind === 'segments') {
      const listKey = kind; // 'stops' or 'segments'
      const journeys = await getAll('journeys');
      for (const journey of journeys) {
        const list = journey[listKey];
        if (!Array.isArray(list)) continue;
        const idx = list.findIndex((item) => item.id === id);
        if (idx !== -1) {
          const updatedList = list.slice();
          updatedList[idx] = { ...updatedList[idx], ...fields };
          await putAll('journeys', [{ ...journey, [listKey]: updatedList }]);
          return;
        }
      }
    }
  }

  return {
    putAll, getAll, get, getMeta, setMeta,
    queueChange, getPendingChanges, clearPendingChanges, applyOptimisticEdit,
  };
})();
