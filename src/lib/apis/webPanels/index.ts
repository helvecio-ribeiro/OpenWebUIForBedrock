import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';

export type WebPanel = {
	id: string;
	user_id: string;
	title: string;
	url: string;
	created_at: number;
	updated_at: number;
};

const request = async <T>(token: string, path: string, init: RequestInit = {}): Promise<T> => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/web-panels${path}`, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`,
			...init.headers
		}
	});
	if (!response.ok) {
		const body = await response.json().catch(() => null);
		throw new Error(body?.detail ?? `Web panel request failed (${response.status})`);
	}
	return response.json();
};

export const getWebPanels = (token: string) => request<WebPanel[]>(token, '/');
export const getWebPanel = (token: string, id: string) => request<WebPanel>(token, `/${id}`);
export const createWebPanel = (token: string, values: { title?: string; url?: string } = {}) =>
	request<WebPanel>(token, '/', { method: 'POST', body: JSON.stringify(values) });
export const updateWebPanel = (
	token: string,
	id: string,
	values: { title?: string; url?: string }
) => request<WebPanel>(token, `/${id}`, { method: 'PATCH', body: JSON.stringify(values) });
export const deleteWebPanel = (token: string, id: string) =>
	request<{ success: boolean }>(token, `/${id}`, { method: 'DELETE' });
export const deleteWebPanels = (token: string, ids: string[]) =>
	request<{ success: boolean; deleted: number }>(token, '/batch/delete', {
		method: 'POST',
		body: JSON.stringify({ ids })
	});
export const createWebPanelSession = (token: string, id: string) =>
	request<{ token: string; proxy_url: string }>(token, `/${id}/session`, { method: 'POST' }).then(
		(session) => ({
			...session,
			proxy_url:
				session.proxy_url && session.proxy_url.startsWith('/')
					? `${WEBUI_BASE_URL}${session.proxy_url}`
					: session.proxy_url
		})
	);
