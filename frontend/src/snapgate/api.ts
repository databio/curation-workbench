import {
  User,
  AuthError,
  RefreshResponse,
  ApiKeyListResponse,
  ApiKeyCreateRequest,
  ApiKeyCreateResponse,
  ProviderInfo,
  ScopesResponse,
  ConnectionInfo,
  ConnectionListResponse,
} from './types';
import { ACCESS_TOKEN_KEY } from './constants';
import { refreshAccessToken } from './refreshClient';
import { readAuthError } from './utils';

function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * fetch() against the snapgate backend with the bearer header; on a 401,
 * refresh once (single-flight, cross-tab) and retry with the new access token.
 */
export async function snapgateFetch(
  apiUrl: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const send = () =>
    fetch(`${apiUrl}${path}`, {
      ...init,
      credentials: 'include',
      headers: { ...authHeaders(), ...((init.headers as Record<string, string>) || {}) },
    });
  const response = await send();
  if (response.status !== 401) return response;
  try {
    const refreshed = await refreshAccessToken(apiUrl);
    if (!refreshed) return response;
  } catch {
    return response;
  }
  return send();
}

/** Parse a JSON response or throw an AuthError (readAuthError). */
export async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw await readAuthError(response);
  }
  const contentType = response.headers.get('content-type');
  if (!contentType || !contentType.includes('application/json')) {
    throw {
      code: 'invalid_response',
      message: 'Server returned non-JSON response',
      status: response.status,
    } as AuthError;
  }
  return response.json();
}

/** Throw an AuthError when !response.ok; otherwise return nothing (204 bodies). */
export async function expectOk(response: Response): Promise<void> {
  if (!response.ok) {
    throw await readAuthError(response);
  }
}

export class AuthAPI {
  private apiUrl: string;

  constructor(apiUrl: string) {
    this.apiUrl = apiUrl;
  }

  async getCurrentUser(): Promise<User | null> {
    try {
      const response = await fetch(`${this.apiUrl}/auth/me`, {
        credentials: 'include',
        headers: authHeaders(),
      });

      const data = await readJson<{ user: User | null }>(response);
      if (data === null || typeof data !== 'object' || !('user' in data)) {
        const seen = data && typeof data === 'object' ? Object.keys(data).slice(0, 8).join(', ') : typeof data;
        throw {
          code: 'invalid_me_shape',
          message:
            `GET ${this.apiUrl}/auth/me returned an unexpected shape. ` +
            `Snapgate expects {"user": <User|null>, ...}. ` +
            `Got keys: [${seen}]. ` +
            `Fix: have your backend's /auth/me wrap the user under a "user" key, ` +
            `or point snapgate at a different endpoint that does.`,
        } as AuthError;
      }
      return data.user;
    } catch (error) {
      // Don't throw for getCurrentUser - just return null
      console.debug('User not authenticated:', error);
      return null;
    }
  }

  async refreshTokens(refreshToken: string): Promise<RefreshResponse> {
    const response = await fetch(`${this.apiUrl}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    return readJson<RefreshResponse>(response);
  }

  async logout(refreshToken: string): Promise<void> {
    const response = await fetch(`${this.apiUrl}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        ...authHeaders(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    await readJson<{ message: string }>(response);
  }

  async demoLogin(role: 'admin' | 'user'): Promise<RefreshResponse> {
    const response = await fetch(`${this.apiUrl}/auth/demo-login/${role}`, {
      method: 'POST',
      credentials: 'include',
    });

    return readJson<RefreshResponse>(response);
  }

  async listApiKeys(): Promise<ApiKeyListResponse> {
    return readJson<ApiKeyListResponse>(await snapgateFetch(this.apiUrl, '/auth/api-keys'));
  }

  async createApiKey(data: ApiKeyCreateRequest): Promise<ApiKeyCreateResponse> {
    const response = await snapgateFetch(this.apiUrl, '/auth/api-keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return readJson<ApiKeyCreateResponse>(response);
  }

  async revokeApiKey(keyId: number): Promise<void> {
    await expectOk(await snapgateFetch(this.apiUrl, `/auth/api-keys/${keyId}`, { method: 'DELETE' }));
  }

  /** What the sign-in button should say (no auth needed). */
  async getProvider(): Promise<ProviderInfo> {
    const response = await fetch(`${this.apiUrl}/auth/provider`, { credentials: 'include' });
    return readJson<ProviderInfo>(response);
  }

  /** The app's scope vocabulary (no auth needed). */
  async getScopes(): Promise<ScopesResponse> {
    const response = await fetch(`${this.apiUrl}/auth/scopes`, { credentials: 'include' });
    return readJson<ScopesResponse>(response);
  }

  /** The signed-in person's connected AI apps. */
  async listConnections(): Promise<ConnectionListResponse> {
    const response = await snapgateFetch(this.apiUrl, '/auth/connections');
    return readJson<ConnectionListResponse>(response);
  }

  /** Change what a connection may do: the permission groups it holds (at least one). */
  async updateConnection(connectionId: number, groupIds: string[]): Promise<ConnectionInfo> {
    const response = await snapgateFetch(this.apiUrl, `/auth/connections/${connectionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ groups: groupIds }),
    });
    return readJson<ConnectionInfo>(response);
  }

  /** Revoke a connection and every key it holds. */
  async revokeConnection(connectionId: number): Promise<void> {
    const response = await snapgateFetch(this.apiUrl, `/auth/connections/${connectionId}`, {
      method: 'DELETE',
    });
    await expectOk(response);
  }

  async exchangeCode(code: string): Promise<RefreshResponse> {
    const response = await fetch(`${this.apiUrl}/auth/exchange`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ code }),
    });

    return readJson<RefreshResponse>(response);
  }
}
