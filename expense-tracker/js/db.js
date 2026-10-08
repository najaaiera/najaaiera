/* Thin promise wrapper around IndexedDB. Receipts are stored as Blobs. */
window.DB = (function () {
  'use strict';

  const NAME = 'expense-transfer-tracker';
  const VERSION = 1;
  const STORES = ['accounts', 'transactions', 'receipts', 'meta'];
  let dbPromise = null;

  function open() {
    if (!dbPromise) {
      dbPromise = new Promise((resolve, reject) => {
        if (!('indexedDB' in window)) {
          reject(new Error('IndexedDB is not available in this browser.'));
          return;
        }
        const req = indexedDB.open(NAME, VERSION);
        req.onupgradeneeded = () => {
          const db = req.result;
          for (const name of STORES) {
            if (!db.objectStoreNames.contains(name)) {
              db.createObjectStore(name, { keyPath: name === 'meta' ? 'key' : 'id' });
            }
          }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
        req.onblocked = () => reject(new Error('Database is blocked by another open tab.'));
      });
    }
    return dbPromise;
  }

  function request(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function completion(tx) {
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error('Transaction aborted'));
    });
  }

  async function getAll(store) {
    const db = await open();
    return request(db.transaction(store).objectStore(store).getAll());
  }

  async function get(store, key) {
    const db = await open();
    return request(db.transaction(store).objectStore(store).get(key));
  }

  /**
   * Apply several writes atomically, possibly across stores.
   * ops: [{ store, put: value } | { store, delete: key } | { store, clear: true }]
   */
  async function write(ops) {
    if (!ops.length) return;
    const db = await open();
    const stores = [...new Set(ops.map((o) => o.store))];
    const tx = db.transaction(stores, 'readwrite');
    for (const op of ops) {
      const s = tx.objectStore(op.store);
      if (op.clear) s.clear();
      else if ('delete' in op) s.delete(op.delete);
      else s.put(op.put);
    }
    return completion(tx);
  }

  return { open, getAll, get, write, STORES };
})();
