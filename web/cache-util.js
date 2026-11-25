/* Shared validation, abort-aware promise waiting, and cached JSON loading for static cache modules. */
export const LIBS_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*(?:,[a-z0-9]+(?:-[a-z0-9]+)*)*$/;

export function jsonObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function parseCacheRequest(path) {
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) return null;
  let url;
  try {
    url = new URL(path, 'https://anna.invalid');
  } catch {
    return null;
  }
  const {pathname, searchParams} = url;
  const counts = new Map();
  for (const key of searchParams.keys()) counts.set(key, (counts.get(key) || 0) + 1);
  for (const n of counts.values()) if (n !== 1) return null;
  return {pathname, searchParams, counts};
}

export function waitWithAbort(promise, signal) {
  if (!signal) return promise;
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(signal.reason);
    signal.addEventListener('abort', onAbort, {once: true});
    promise.then(
      value => { signal.removeEventListener('abort', onAbort); resolve(value); },
      error => { signal.removeEventListener('abort', onAbort); reject(error); },
    );
  });
}

async function fetchJsonArtifact(filename, baseUrl) {
  try {
    const response = await fetch(new URL(filename, baseUrl), {mode: 'same-origin'});
    if (response.ok) return await response.json();
  } catch {
    // An unreadable artifact leaves these requests on the host's normal transport.
  }
}

export function getCachedArtifact(artifactsMap, filename, baseUrl, signal) {
  signal?.throwIfAborted();
  if (!artifactsMap.has(filename)) artifactsMap.set(filename, fetchJsonArtifact(filename, baseUrl));
  return waitWithAbort(artifactsMap.get(filename), signal);
}
