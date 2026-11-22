/**
 * Lo que la app guarda en el teléfono para seguir sin señal:
 *  - `queue`: entregas, no entregas y salidas hechas sin conexión, en el orden en que
 *    ocurrieron, hasta que se puedan mandar;
 *  - `cache`: la última copia de las rutas, para poder abrirlas sin conexión.
 *
 * Usa IndexedDB (aguanta las fotos, que no entran en localStorage). Si el navegador no
 * lo permite (modo privado en algunos equipos) queda en memoria: sirve mientras la app
 * siga abierta.
 */
const DB_NAME = "route_delivery";
const memory = { queue: new Map(), cache: new Map(), next: 1 };
let opening = null;

function open() {
    if (!opening) {
        opening = new Promise((resolve) => {
            let request;
            try {
                request = indexedDB.open(DB_NAME, 1);
            } catch {
                resolve(null);
                return;
            }
            request.onupgradeneeded = () => {
                request.result.createObjectStore("queue", { keyPath: "id", autoIncrement: true });
                request.result.createObjectStore("cache");
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => resolve(null);
            request.onblocked = () => resolve(null);
        });
    }
    return opening;
}

/** Ejecuta `work(store)` en una transacción y resuelve con el resultado del pedido. */
async function run(storeName, mode, work, fallback) {
    const db = await open();
    if (!db) {
        return fallback();
    }
    return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, mode);
        const request = work(tx.objectStore(storeName));
        tx.oncomplete = () => resolve(request ? request.result : undefined);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
    });
}

// Los objetos reactivos de OWL no se pueden guardar tal cual: se copian a datos planos.
const plain = (value) => JSON.parse(JSON.stringify(value));

export const offlineStore = {
    /** Agrega una acción pendiente y devuelve su id. */
    async queueAdd(item) {
        const data = plain(item);
        return run("queue", "readwrite", (store) => store.add(data), () => {
            const id = memory.next++;
            memory.queue.set(id, { ...data, id });
            return id;
        });
    },
    async queueAll() {
        const items = await run("queue", "readonly", (store) => store.getAll(), () => [...memory.queue.values()]);
        return (items || []).sort((a, b) => a.id - b.id);
    },
    async queuePut(item) {
        const data = plain(item);
        return run("queue", "readwrite", (store) => store.put(data), () => memory.queue.set(data.id, data));
    },
    async queueDelete(id) {
        return run("queue", "readwrite", (store) => store.delete(id), () => memory.queue.delete(id));
    },
    async cacheSet(key, value) {
        const data = plain(value);
        return run("cache", "readwrite", (store) => store.put(data, key), () => memory.cache.set(key, data));
    },
    async cacheGet(key) {
        return run("cache", "readonly", (store) => store.get(key), () => memory.cache.get(key));
    },
};
