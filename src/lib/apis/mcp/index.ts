import { WEBUI_API_BASE_URL } from '$lib/constants';

export type MCPServerCatalogEntry = {
	id: string;
	name: string;
	description: string;
	source: 'managed' | 'remote';
	transport: 'streamable_http';
	status: string;
	enabled: boolean;
	selectable: boolean;
	authenticated: boolean | null;
	created_at: number;
	updated_at: number;
};

const authenticatedGet = (url: string, token: string) =>
	fetch(url, {
		headers: { Accept: 'application/json', authorization: `Bearer ${token}` }
	});

export const getMCPServers = async (token = ''): Promise<MCPServerCatalogEntry[]> => {
	const response = await authenticatedGet(`${WEBUI_API_BASE_URL}/mcp/servers`, token);
	const contentType = response.headers.get('content-type') ?? '';
	if (!contentType.includes('application/json')) {
		const body = await response.text();
		if (
			body.trimStart().toLowerCase().startsWith('<!doctype') ||
			body.trimStart().toLowerCase().startsWith('<html')
		) {
			throw new Error('MCP catalog request reached the HTML frontend instead of the API backend');
		}
		throw new Error(
			`MCP catalog returned ${response.status} ${contentType || 'without a content type'} instead of JSON${
				body.trimStart().startsWith('<!doctype') || body.trimStart().startsWith('<html')
					? '; the request reached an HTML frontend rather than the API backend'
					: ''
			}`
		);
	}

	const payload = await response.json();
	if (!response.ok) {
		throw new Error(payload.detail ?? `Unable to load MCP servers (${response.status})`);
	}
	if (!Array.isArray(payload)) {
		throw new Error('MCP catalog returned an invalid response');
	}
	return payload;
};

// Adapt catalog records to the existing shared UI store shape. IDs remain raw MCP server IDs.
export const getMCPTools = async (token = '') =>
	(await getMCPServers(token))
		.filter((server) => server.selectable)
		.map((server) => ({
			id: server.id,
			user_id: server.id,
			name: server.name,
			meta: { description: server.description },
			authenticated: server.authenticated,
			source: server.source,
			status: server.status,
			created_at: server.created_at,
			updated_at: server.updated_at
		}));
