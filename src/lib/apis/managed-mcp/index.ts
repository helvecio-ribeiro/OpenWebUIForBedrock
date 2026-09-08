import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ManagedMCPEnvironmentSetting = {
	default?: string | null;
	description?: string;
	secret?: boolean;
};

export type DiscoveredManagedMCPService = {
	id: string;
	name: string;
	description: string;
	version: string;
	package_path: string;
	package_digest: string;
	discovery_state: 'available' | 'registered' | 'conflict';
	enabled: boolean;
	runtime_state: string | null;
	environment: Record<string, ManagedMCPEnvironmentSetting>;
	security: {
		profile: 'confined' | 'system-admin';
		read_only: boolean;
		root_write: boolean;
		filesystem_roots: string[];
	};
};

export type ManagedMCPDiscovery = {
	roots: string[];
	services: DiscoveredManagedMCPService[];
	errors: { package_path: string; error: string }[];
};

const request = async <T>(token: string, path: string, init: RequestInit = {}): Promise<T> => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/managed-mcp${path}`, {
		...init,
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`,
			...(init.headers ?? {})
		}
	});
	if (!response.ok) {
		const body = await response.json().catch(() => ({}));
		throw new Error(body?.detail ?? `Managed MCP request failed (${response.status})`);
	}
	return response.status === 204 ? (undefined as T) : response.json();
};

export const discoverManagedMCPServices = (token: string) =>
	request<ManagedMCPDiscovery>(token, '/discover');

export const registerManagedMCPService = (token: string, service: DiscoveredManagedMCPService) => {
	const environment = Object.fromEntries(
		Object.entries(service.environment)
			.filter(
				([, setting]) =>
					!setting.secret && setting.default !== null && setting.default !== undefined
			)
			.map(([name, setting]) => [name, setting.default])
	);
	return request(token, '/', {
		method: 'POST',
		body: JSON.stringify({
			package_path: service.package_path,
			environment,
			secret_environment: {},
			access_grants: [],
			enabled: true
		})
	});
};

export const removeManagedMCPService = (token: string, serverId: string) =>
	request<void>(token, `/${encodeURIComponent(serverId)}`, { method: 'DELETE' });
