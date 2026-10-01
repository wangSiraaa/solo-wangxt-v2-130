const BASE = import.meta.env.VITE_API_BASE || '';

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => response.statusText);
    throw new Error(typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail.detail || detail));
  }
  return response.json();
}

export const api = {
  projects: () => request('/api/projects'),
  createProject: (code, name) => request('/api/projects', { method: 'POST', body: JSON.stringify({ code, name }) }),
  topology: (id) => request(`/api/projects/${id}/topology`),
  jobs: (id) => request(`/api/projects/${id}/jobs`),
  submitJob: (id, expectedDraftVersion, algorithm = 'auto') =>
    request(`/api/projects/${id}/jobs`, {
      method: 'POST',
      body: JSON.stringify({ expected_draft_version: expectedDraftVersion, algorithm }),
    }),
  advance: (jobId) => request(`/jobs/${jobId}/advance`, { method: 'POST' }),
  result: (jobId) => request(`/jobs/${jobId}/result`),
  importCsv: (id, file) => {
    const form = new FormData();
    form.append('file', file);
    return request(`/api/projects/${id}/import`, { method: 'POST', body: form });
  },
  weights: (id) => request(`/api/projects/${id}/weights`),
  updateWeights: (id, body) => request(`/api/projects/${id}/weights`, { method: 'PUT', body: JSON.stringify(body) }),
  publish: (id, version) =>
    request(`/api/projects/${id}/publish`, { method: 'POST', body: JSON.stringify({ expected_draft_version: version }) }),
};
