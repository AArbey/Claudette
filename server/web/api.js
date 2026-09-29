"use strict";

async function requestJson(path, { method = "GET", body, ui = false } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (ui) headers["X-Brain-UI"] = "1";
  const response = await fetch(path, {
    method,
    cache: method === "GET" ? "no-store" : undefined,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return {};
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

function getJson(path) {
  return requestJson(path);
}

function aiWrite(path, body = {}, method = "POST") {
  return requestJson(path, { method, body: method === "DELETE" ? undefined : body, ui: true });
}
