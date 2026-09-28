/**
 * Authenticated fetch wrapper.
 * API Gateway Cognito authorizer requires the ID token (not access token).
 * Falls back to access token if ID token is unavailable.
 */
export async function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const token =
    sessionStorage.getItem('idToken') ||
    localStorage.getItem('idToken') ||
    sessionStorage.getItem('authToken') ||
    localStorage.getItem('authToken');

  const headers = new Headers(init?.headers);
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  return fetch(input, { ...init, headers });
}
