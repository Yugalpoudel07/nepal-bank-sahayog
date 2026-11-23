// api.js
// Thin client for the FastAPI backend. The base URL comes from VITE_API_URL.

export const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`${API_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch {
    throw new ApiError('network', 0);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(body.detail || res.statusText, res.status);
  return body;
}

export const getFiscalYears = () => request('/fiscal-years');
export const getDocuments = () => request('/documents');

export const calculateLoanProvision = (payload) =>
  request('/calculate/loan-provision', { method: 'POST', body: JSON.stringify(payload) });

export const checkCashLimit = (payload) =>
  request('/calculate/cash-limit', { method: 'POST', body: JSON.stringify(payload) });

/**
 * Stream a chat answer over Server-Sent Events (POST, so EventSource cannot be used).
 * handlers: { onMeta(meta), onToken(text), onDone(result), onError(message) }
 */
export async function streamChat(payload, handlers, signal) {
  let res;
  try {
    res = await fetch(`${API_URL}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (err) {
    if (err.name === 'AbortError') return;
    throw new ApiError('network', 0);
  }
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(body.detail || res.statusText, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const dispatch = (block) => {
    let event = 'message';
    const data = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return;
    const parsed = JSON.parse(data.join('\n'));
    if (event === 'meta') handlers.onMeta?.(parsed);
    else if (event === 'token') handlers.onToken?.(parsed.text);
    else if (event === 'done') handlers.onDone?.(parsed);
    else if (event === 'error') handlers.onError?.(parsed.detail);
  };

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        dispatch(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 2);
      }
    }
    if (buffer.trim()) dispatch(buffer);
  } catch (err) {
    if (err.name !== 'AbortError') throw err;
  }
}
