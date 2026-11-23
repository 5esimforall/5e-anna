/* Dedicated module worker. Initialize with {type: 'init', baseUrl}, then send {id, path}.
   The adapter receives the logical site path and returns (status, headers, body: bytes). */
const PROJECT = '/anna-pages';
const CACHE_BYTES = 128 * 1024 * 1024;
const CACHE_ENTRIES = 2048;
// Emscripten's WASI errno values for the pinned Pyodide build.
const EIO = 29;
const EROFS = 69;

let startup;

function projectURL(baseUrl) {
    const base = new URL(baseUrl, self.location.href);
    if (!base.pathname.endsWith('/')) base.pathname += '/';
    base.search = '';
    base.hash = '';
    return base;
}

async function fetchAsset(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${url}`);
    return response;
}

function chunkReader(gunzip) {
    const cache = new Map();
    let cachedBytes = 0;
    return (url, length, encoding) => {
        const cached = cache.get(url);
        if (cached) {
            cache.delete(url);
            cache.set(url, cached);
            return cached;
        }
        // SQLite's filesystem API requires synchronous reads. Each URL serves one complete shard.
        const xhr = new XMLHttpRequest();
        xhr.open('GET', url, false);
        xhr.responseType = 'arraybuffer';
        xhr.send();
        if (xhr.status !== 200 || !xhr.response) throw new Error(`HTTP ${xhr.status} ${url}`);
        let bytes = new Uint8Array(xhr.response);
        if (encoding === 'gzip') {
            const trailer = new DataView(bytes.buffer);
            if (bytes.length < 18 || trailer.getUint32(bytes.length - 4, true) !== length) {
                throw new Error(`Invalid gzip length for Pages shard ${url}`);
            }
            bytes = gunzip(bytes);
        }
        if (bytes.length !== length) throw new Error(`Invalid Pages shard ${url} (expected ${length} bytes)`);
        if (length <= CACHE_BYTES) {
            while (cachedBytes + length > CACHE_BYTES || cache.size >= CACHE_ENTRIES) {
                const oldest = cache.keys().next().value;
                cachedBytes -= cache.get(oldest).byteLength;
                cache.delete(oldest);
            }
            cache.set(url, bytes);
            cachedBytes += length;
        }
        return bytes;
    };
}

function mountFiles(fs, manifest, dataURL, gunzip) {
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
        throw new Error('Invalid Pages data manifest');
    }
    const readChunk = chunkReader(gunzip);
    for (const [path, file] of Object.entries(manifest)) {
        const parts = path.split('/');
        const {size, chunkSize, encoding} = file ?? {};
        if (parts.some(part => !part || part === '.' || part === '..' || /[\\\0]/.test(part)) ||
            !Number.isSafeInteger(size) || size < 0 || !Number.isSafeInteger(chunkSize) || chunkSize < 1 ||
            (encoding !== undefined && encoding !== 'gzip')) {
            throw new Error(`Invalid Pages data file ${path}`);
        }
        const directory = [PROJECT, ...parts.slice(0, -1)].join('/');
        const localPath = `${PROJECT}/${path}`;
        const remotePath = new URL(parts.map(encodeURIComponent).join('/') + '/', dataURL);
        fs.mkdirTree(directory);
        if (fs.analyzePath(localPath).exists) throw new Error(`Duplicate Pages data file ${path}`);
        fs.create(localPath, 0o444);
        const {node} = fs.lookupPath(localPath);
        // Keep MEMFS metadata and seeking while reserving no WASM storage for the file contents.
        node.usedBytes = size;
        node.node_ops = {
            getattr: node.node_ops.getattr,
            setattr() { throw new fs.ErrnoError(EROFS); },
        };
        node.stream_ops = {
            llseek: node.stream_ops.llseek,
            read(_stream, buffer, offset, length, position) {
                const end = Math.min(position + length, size);
                const start = position;
                try {
                    while (position < end) {
                        const index = Math.floor(position / chunkSize);
                        const chunkStart = index * chunkSize;
                        const url = new URL(`${index}.bin`, remotePath).href;
                        const bytes = readChunk(url, Math.min(chunkSize, size - chunkStart), encoding);
                        const inChunk = position - chunkStart;
                        const count = Math.min(end - position, bytes.length - inChunk);
                        buffer.set(bytes.subarray(inChunk, inChunk + count), offset);
                        position += count;
                        offset += count;
                    }
                } catch (error) {
                    console.error(error);
                    throw new fs.ErrnoError(EIO);
                }
                return position - start;
            },
        };
    }
}

async function initialize({baseUrl}) {
    if (typeof baseUrl !== 'string' || !baseUrl) throw new Error('Invalid Pages worker initialization');
    const base = projectURL(baseUrl);
    const engineURL = new URL('pages-engine/', base);
    const manifestURL = new URL('pages-data/index.json', base);
    const [pyodide, {gunzipSync}, archive, manifest] = await Promise.all([
        import(new URL('pyodide.mjs', engineURL).href).then(({loadPyodide}) => loadPyodide({indexURL: engineURL.href})),
        import(new URL('fflate.mjs', engineURL).href),
        fetchAsset(new URL('pages-runtime.zip', base)).then(response => response.arrayBuffer()),
        fetchAsset(manifestURL).then(response => response.json()),
    ]);
    pyodide.unpackArchive(archive, 'zip', {extractDir: PROJECT});
    mountFiles(pyodide.FS, manifest, new URL('./', manifestURL), gunzipSync);
    pyodide.runPython(`
        import sys
        sys.path.insert(0, ${JSON.stringify(PROJECT)})
        from anna.server.pages import handle, warm
    `);
    return {
        handle: pyodide.globals.get('handle'),
        warm: pyodide.globals.get('warm'),
    };
}

function send(id, status, headers, body) {
    self.postMessage({id, status, headers, body}, [body.buffer]);
}

self.addEventListener('message', async ({data}) => {
    if (!data || typeof data !== 'object') return;
    if (data.type === 'init') {
        if (startup) return;
        startup = initialize(data);
        try {
            await startup;
            self.postMessage({type: 'ready'});
        } catch (error) {
            console.error(error);
            self.postMessage({type: 'error', error: String(error.message ?? error)});
        }
        return;
    }
    if (data.type === 'warm') {
        if (!startup) return;
        try {
            const {warm} = await startup;
            warm();
        } catch (error) {
            console.error(error);
        }
        return;
    }
    if (!Object.hasOwn(data, 'id')) return;
    const {id, path} = data;
    try {
        if (!startup) throw new Error('Initialize the Pages worker before sending requests');
        if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) {
            send(id, 400, {'Content-Type': 'application/json; charset=utf-8'},
                new TextEncoder().encode('{"code":"invalid_request_path"}'));
            return;
        }
        const {handle} = await startup;
        const result = handle(path);
        try {
            const response = result.toJs({dict_converter: Object.fromEntries, create_pyproxies: false});
            const [status, headers, body] = response;
            if (!Number.isInteger(status) || status < 200 || status > 599 || !(body instanceof Uint8Array)) {
                throw new Error('Invalid response from anna.server.pages.handle');
            }
            send(id, status, headers, body);
        } finally {
            result.destroy();
        }
    } catch (error) {
        console.error(error);
        send(id, 500, {'Content-Type': 'application/json; charset=utf-8'},
            new TextEncoder().encode('{"code":"pages_runtime_error"}'));
    }
});
