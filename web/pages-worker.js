/* Classic dedicated worker. Initialize with {type: 'init', baseUrl}, then send {id, path}.
   The adapter receives the logical site path and returns (status, headers, body: bytes). */
const PYODIDE_URL = 'https://cdn.jsdelivr.net/pyodide/v314.0.8/full/';
const FFLATE_URL = 'https://cdn.jsdelivr.net/npm/fflate@0.8.2/umd/index.js';
const PROJECT = '/anna-pages';
const CACHE_BYTES = 128 * 1024 * 1024;
const CACHE_ENTRIES = 256;
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

function chunkReader() {
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
            bytes = self.fflate.gunzipSync(bytes);
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

function mountFiles(fs, manifest, dataURL) {
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
        throw new Error('Invalid Pages data manifest');
    }
    const files = Object.entries(manifest);
    if (files.some(([, file]) => file?.encoding === 'gzip')) importScripts(FFLATE_URL);
    const readChunk = chunkReader();
    for (const [path, file] of files) {
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

async function loadDependencies(pyodide, requirements) {
    const wheelDir = `${PROJECT}/wheels`;
    const wheels = pyodide.FS.analyzePath(wheelDir).exists
        ? pyodide.FS.readdir(wheelDir).filter(name => name.endsWith('.whl')).sort()
        : [];
    if (!wheels.length) await pyodide.loadPackage(['beautifulsoup4', 'html5lib']);
    if (!wheels.length && !requirements.length) return;

    await pyodide.loadPackage('micropip');
    const micropip = pyodide.pyimport('micropip');
    try {
        if (wheels.length) {
            // Ship the full dependency closure together when bundling wheels.
            const localWheels = wheels.map(name => `emfs:${wheelDir}/${name}`);
            await micropip.install.callKwargs(localWheels, {deps: false});
        }
        if (requirements.length) await micropip.install(requirements);
    } finally {
        micropip.destroy();
    }
}

async function initialize({baseUrl, runtimeUrl = 'pages-runtime.zip', manifestUrl = 'pages-data/index.json', requirements = []}) {
    if (typeof baseUrl !== 'string' || !baseUrl || !Array.isArray(requirements) ||
        requirements.some(requirement => typeof requirement !== 'string')) {
        throw new Error('Invalid Pages worker initialization');
    }
    const base = projectURL(baseUrl);
    const manifestURL = new URL(manifestUrl, base);
    importScripts(new URL('pyodide.js', PYODIDE_URL).href);
    const [pyodide, archive, manifest] = await Promise.all([
        self.loadPyodide({indexURL: PYODIDE_URL}),
        fetchAsset(new URL(runtimeUrl, base)).then(response => response.arrayBuffer()),
        fetchAsset(manifestURL).then(response => response.json()),
    ]);
    pyodide.unpackArchive(archive, 'zip', {extractDir: PROJECT});
    mountFiles(pyodide.FS, manifest, new URL('./', manifestURL));
    await loadDependencies(pyodide, requirements);
    return pyodide.runPython(`
        import sys
        sys.path.insert(0, ${JSON.stringify(PROJECT)})
        from anna.server.pages import handle
        handle
    `);
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
    if (!Object.hasOwn(data, 'id')) return;
    const {id, path} = data;
    try {
        if (!startup) throw new Error('Initialize the Pages worker before sending requests');
        if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) {
            send(id, 400, {'Content-Type': 'application/json; charset=utf-8'},
                new TextEncoder().encode('{"code":"invalid_request_path"}'));
            return;
        }
        const handle = await startup;
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
