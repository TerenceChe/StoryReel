import axios from "axios";

const baseURL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const apiClient = axios.create({
  baseURL,
  headers: {
    "Content-Type": "application/json",
  },
});

// --- Auth: configurable token provider ---

let _getToken: (() => Promise<string>) | null = null;

export function configureAuth(getToken: () => Promise<string>) {
  _getToken = getToken;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  if (v === null || typeof v !== "object") return false;
  if (Array.isArray(v)) return true;
  // Skip browser/runtime types we shouldn't enumerate.
  if (typeof FormData !== "undefined" && v instanceof FormData) return false;
  if (typeof Blob !== "undefined" && v instanceof Blob) return false;
  if (typeof ArrayBuffer !== "undefined" && v instanceof ArrayBuffer) return false;
  if (typeof File !== "undefined" && v instanceof File) return false;
  if (
    typeof URLSearchParams !== "undefined" &&
    v instanceof URLSearchParams
  )
    return false;
  return true;
}

apiClient.interceptors.request.use(async (config) => {
  if (_getToken) {
    try {
      const token = await _getToken();
      config.headers.Authorization = `Bearer ${token}`;
    } catch {
      // Token retrieval failed — request will go out without auth header
    }
  }
  if (isPlainObject(config.data)) {
    config.data = toSnakeCase(config.data);
  }
  return config;
});

function camelToSnake(str: string): string {
  return str.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
}

function convertKeys(
  obj: unknown,
  converter: (key: string) => string,
): unknown {
  if (Array.isArray(obj)) {
    return obj.map((item) => convertKeys(item, converter));
  }
  if (obj !== null && typeof obj === "object") {
    return Object.fromEntries(
      Object.entries(obj as Record<string, unknown>).map(([key, value]) => [
        converter(key),
        convertKeys(value, converter),
      ]),
    );
  }
  return obj;
}

export function toSnakeCase<T>(data: T): T {
  return convertKeys(data, camelToSnake) as T;
}

export function toCamelCase<T>(data: T): T {
  return convertKeys(data, snakeToCamel) as T;
}

apiClient.interceptors.response.use((response) => {
  if (isPlainObject(response.data)) {
    response.data = toCamelCase(response.data);
  }
  return response;
});

export default apiClient;

/** Get a fresh token for SSE URLs that need ?token= query param. */
export async function getApiToken(): Promise<string> {
  if (_getToken) {
    try {
      return await _getToken();
    } catch {
      return "";
    }
  }
  return "";
}
