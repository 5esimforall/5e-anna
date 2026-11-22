"""Stage the pinned browser runtime and its licenses beside the website."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from urllib.request import urlopen

PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v314.0.8/full/'
CORE = {
    'pyodide.mjs': '1bb8cc14f9405aaa7cd4e00c12edd0a8539de9cc026618d6ee5a9663f367dc70',
    'pyodide.asm.mjs': '22514b8be6980e28a02c8b1d6387ce8dfe666fc4edf8d547cff5dce29ff4d972',
    'pyodide.asm.wasm': 'e14d49ceca9b4a0e274ea918f3a898de2d3880b6e5573a6cfe1a2267ecc70329',
    'pyodide-lock.json': '5dc2fc119108bc148c7457dc86e7675b5c87e1cafd420b9c34c1eaef7b36c010',
    'python_stdlib.zip': '2bd3394a145bfbbe5bbc9d9d6ab9136abbb986c6fef6c142b3a2962227aa5c28',
}
ADDITIONAL = {
    'pyodide-LICENSE': ('https://cdn.jsdelivr.net/gh/pyodide/pyodide@314.0.8/LICENSE',
                        '1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5'),
    'fflate.mjs': ('https://cdn.jsdelivr.net/npm/fflate@0.8.2/esm/browser.js',
                   '8cc1f687e0159e977addb6b85e274dbd11e622cf151f4fcb7b85d49622ea43e7'),
    'fflate-LICENSE': ('https://cdn.jsdelivr.net/npm/fflate@0.8.2/LICENSE',
                        '805f6cb28bb8b6d3a0badd83c93bccd9671fa01a3b4b92b7042b0743325ac243'),
}


def download(path, url, expected):
    with urlopen(url, timeout=45) as response:
        data = response.read()
    if sha256(data).hexdigest() != expected:
        raise ValueError(f'Checksum mismatch for browser runtime asset: {path.name}')
    path.write_bytes(data)


def stage_browser_runtime(target):
    target.mkdir()
    lock = target / 'pyodide-lock.json'
    download(lock, PYODIDE + lock.name, CORE[lock.name])
    packages = json.loads(lock.read_text(encoding='utf-8'))['packages']
    assets = {name: (PYODIDE + name, digest) for name, digest in CORE.items() if name != lock.name}
    assets.update(ADDITIONAL)
    pending = ['beautifulsoup4', 'html5lib']
    while pending:
        package = packages[pending.pop()]
        name = package['file_name']
        if name not in assets:
            assets[name] = (PYODIDE + name, package['sha256'])
            pending.extend(package['depends'])
    with ThreadPoolExecutor(max_workers=6) as pool:
        downloads = [pool.submit(download, target / name, url, digest) for name, (url, digest) in assets.items()]
        for task in downloads:
            task.result()
