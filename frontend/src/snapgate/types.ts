export type UserStatus = 'pending' | 'approved' | 'denied' | 'suspended';

/** A site role, set only by the server's SNAPGATE_SUPERADMIN_EMAILS / SNAPGATE_ADMIN_EMAILS. */
export type SiteRole = 'superadmin' | 'admin';

export interface User {
  id: number;
  google_id: string;  // Stable identifier ("issuer#sub"; the name is historical)
  email: string;
  name: string;
  picture?: string;
  status: UserStatus;
  created_at: string;
  /** 'superadmin' / 'admin' for env-listed people, else null. */
  site_role: SiteRole | null;
}

/**
 * A remembered account stored in localStorage for quick re-login.
 */
export interface RememberedAccount {
  google_id: string;      // Stable identifier (from /auth/me)
  email: string;
  name: string;
  picture?: string;
  last_login: string;     // ISO timestamp
}

export interface AuthState {
  user: User | null;
  loading: boolean;
  error: AuthError | null;
}

export interface AuthError {
  code: string;
  message: string;
  status?: number;
  /** invalid_input: which input was wrong. */
  field?: string;
}

export interface RefreshResponse {
  message: string;
  access_token: string;
  refresh_token: string;
  user: User;
  access_expires_at: string;  // ISO timestamp
}

export interface ApiKeyInfo {
  id: number;
  name: string;
  description?: string | null;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at?: string | null;
  expires_at?: string | null;
  revoked: boolean;
}

export interface ApiKeyListResponse {
  api_keys: ApiKeyInfo[];
}

export interface ApiKeyCreateRequest {
  name: string;
  description?: string;
  expires_days?: number;
  /** Omit for the app's default_scopes. Must be declared names; never empty. */
  scopes?: string[];
}

export interface ApiKeyCreateResponse {
  message?: string;
  api_key: string;
  key_info: {
    id: number;
    name: string;
    description: string | null;
    scopes: string[];
    created_at: string;
    expires_at: string | null;
  };
  warning?: string;
}

/** One scope the app declares: a name and a plain-language description. */
export interface ScopeInfo {
  name: string;
  description: string;
}

/** One permission a person grants a connection (a consent checkbox), and its scopes. */
export interface ScopeGroupInfo {
  id: string;
  label: string;
  description: string | null;
  scopes: string[];
}

/** GET /auth/scopes: the app's public scope vocabulary. */
/** GET /auth/provider: what the sign-in button says, and where it goes. */
export interface ProviderInfo {
  label: string;
  issuer: string;
}

export interface ScopesResponse {
  scopes: ScopeInfo[];
  groups: ScopeGroupInfo[];
  default_scopes: string[];
}

/** A connected AI app (an OAuth connection). */
export interface ConnectionInfo {
  id: number;
  client_name: string;
  /** The website vouching for the app, or null for a registered app. */
  client_host: string | null;
  /**
   * The app's own command-line login (a native app): it can be revoked, not
   * changed, and holds no permission groups.
   */
  fixed?: boolean;
  scopes: ScopeInfo[];
  /** Ids of the permission groups the connection holds in full. */
  groups: string[];
  created_at: string;
  last_refreshed_at: string | null;
  expires_at: string;
}

export interface ConnectionListResponse {
  connections: ConnectionInfo[];
}

export interface AuthContextValue extends AuthState {
  /** The backend base URL the provider was given. */
  apiUrl: string;
  login: (loginHint?: string, returnTo?: string) => void;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  demoLogin?: (role: 'admin' | 'user') => Promise<void>;
  rememberedAccounts: RememberedAccount[];
  removeRememberedAccount: (googleId: string) => void;
  /** The sign-in button text's provider name (OIDC_LABEL); null until loaded. */
  providerLabel: string | null;
  /** True when the app mounted snapgate's user-admin module (SnapgateProvider userAdmin). */
  userAdmin: boolean;
}
