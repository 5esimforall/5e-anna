/* Cached GET responses for the default Anna homepage on either host. */
const jsonObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const routes = {
  '/api/meta': body => jsonObject(body.fields) && jsonObject(body.types) && Array.isArray(body.collections),
  '/api/facets': ({schema, groups, scope_total}) => jsonObject(schema) &&
    ['columns', 'flags', 'sorts'].every(key => Array.isArray(schema[key])) &&
    Array.isArray(groups) && groups.every(group => Array.isArray(group?.options)) && Number.isInteger(scope_total),
  '/api/entries?offset=0&limit=150': body => Array.isArray(body.rows) && Number.isInteger(body.total),
  '/api/library': body => Array.isArray(body.collections) && body.collections.every(jsonObject)
};
const artifacts = new Map();

async function load(filename) {
  try {
    const response = await fetch(new URL(filename, import.meta.url), {mode:'same-origin'});
    if (response.ok) return await response.json();
  } catch {
    // An unreadable artifact leaves these requests on the host's normal transport.
  }
}

function waitForArtifact(artifact, signal) {
  if (!signal) return artifact;
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener('abort', abort, {once:true});
    artifact.then(data => {
      signal.removeEventListener('abort', abort);
      resolve(data);
    });
  });
}

export async function request(path, filename, signal) {
  signal?.throwIfAborted();
  if (!/^home\.[a-f0-9]{16}\.json$/.test(filename) || !Object.hasOwn(routes, path)) return;
  // Share the fetch while letting each caller cancel its own wait.
  if (!artifacts.has(filename)) artifacts.set(filename, load(filename));
  const data = await waitForArtifact(artifacts.get(filename), signal);
  signal?.throwIfAborted();
  if (!jsonObject(data) || !Object.hasOwn(data, path)) return;
  const body = data[path];
  if (jsonObject(body) && routes[path](body)) return Response.json(body, {headers:{'X-Anna-Home-Cache':'1'}});
}
