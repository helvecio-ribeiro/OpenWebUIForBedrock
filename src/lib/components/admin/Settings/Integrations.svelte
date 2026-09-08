<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, onMount, getContext, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const dispatch = createEventDispatcher();
	const i18n = getContext<Writable<i18nType>>('i18n');

	import { terminalServers, tools } from '$lib/stores';
	import { getTerminalServers } from '$lib/apis/terminal';
	import { getTools } from '$lib/apis/tools';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';
	import Cloud from '$lib/components/icons/Cloud.svelte';
	import Connection from '$lib/components/chat/Settings/Tools/Connection.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	import AddToolServerModal from '$lib/components/AddToolServerModal.svelte';
	import AddTerminalServerModal from '$lib/components/AddTerminalServerModal.svelte';
	import ExternalKnowledge from './ExternalKnowledge.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';
	import {
		discoverManagedMCPServices,
		registerManagedMCPService,
		removeManagedMCPService,
		type DiscoveredManagedMCPService,
		type ManagedMCPDiscovery
	} from '$lib/apis/managed-mcp';

	import {
		getToolServerConnections,
		setToolServerConnections,
		getTerminalServerConnections,
		setTerminalServerConnections
	} from '$lib/apis/configs';

	// svelte-ignore export_let_unused\n
	export let saveSettings: Function;

	type ToolServerConnection = any;
	type TerminalConnection = {
		id?: string;
		url?: string;
		name?: string;
		key?: string;
		enabled?: boolean;
		[key: string]: any;
	};

	let servers: ToolServerConnection[] | null = null;
	let showConnectionModal = false;
	let managedMCPDiscovery: ManagedMCPDiscovery | null = null;
	let managedMCPLoading = false;
	let registeringManagedMCP: string | null = null;
	let removingManagedMCP: string | null = null;
	let managedMCPPendingRemoval: DiscoveredManagedMCPService | null = null;
	let showManagedMCPRemoveConfirm = false;

	const discoverManagedMCP = async () => {
		managedMCPLoading = true;
		try {
			managedMCPDiscovery = await discoverManagedMCPServices(localStorage.token);
			// Chat keeps the tool catalogue in a global store. Discovery must
			// replace it so already-open chats see newly ready local services.
			tools.set(await getTools(localStorage.token));
		} catch (error) {
			toast.error(error instanceof Error ? error.message : $i18n.t('Service discovery failed'));
		} finally {
			managedMCPLoading = false;
		}
	};

	const registerManagedMCP = async (service: DiscoveredManagedMCPService) => {
		registeringManagedMCP = service.package_path;
		try {
			await registerManagedMCPService(localStorage.token, service);
			toast.success($i18n.t('Local MCP service registered'));
			await discoverManagedMCP();
		} catch (error) {
			toast.error(error instanceof Error ? error.message : $i18n.t('Failed to register service'));
			await discoverManagedMCP();
		} finally {
			registeringManagedMCP = null;
		}
	};

	const removeManagedMCP = async () => {
		if (!managedMCPPendingRemoval) return;
		removingManagedMCP = managedMCPPendingRemoval.id;
		try {
			await removeManagedMCPService(localStorage.token, managedMCPPendingRemoval.id);
			toast.success($i18n.t('Local MCP service removed'));
			await discoverManagedMCP();
		} catch (error) {
			toast.error(error instanceof Error ? error.message : $i18n.t('Failed to remove service'));
		} finally {
			removingManagedMCP = null;
			managedMCPPendingRemoval = null;
		}
	};

	// Terminal server admin connections
	let terminalConnections: TerminalConnection[] = [];
	let showAddTerminalModal = false;
	let editTerminalIdx: number | null = null;

	const addConnectionHandler = async (server: ToolServerConnection) => {
		servers = [...(servers ?? []), server];
		await updateHandler();
	};

	const updateHandler = async () => {
		const res = await setToolServerConnections(localStorage.token, {
			TOOL_SERVER_CONNECTIONS: servers
		}).catch((err) => {
			toast.error($i18n.t('Failed to save connections'));
			return null;
		});

		if (res) {
			toast.success($i18n.t('Connections saved successfully'));
		}
	};

	const saveTerminalServers = async () => {
		const res = await setTerminalServerConnections(localStorage.token, {
			TERMINAL_SERVER_CONNECTIONS: terminalConnections
		}).catch((err) => {
			toast.error($i18n.t('Failed to save terminal servers'));
			return null;
		});

		if (res) {
			toast.success($i18n.t('Terminal servers saved'));

			// Refresh the terminalServers store so changes are reflected immediately
			// Preserve user direct terminals, refresh system terminals from backend
			const existingDirectTerminals = (($terminalServers ?? []) as TerminalConnection[]).filter(
				(t) => !t.id
			);
			const systemTerminals = await getTerminalServers(localStorage.token);
			const systemEntries = systemTerminals.map((t) => ({
				id: t.id,
				url: `${WEBUI_API_BASE_URL}/terminals/${t.id}`,
				name: t.name,
				key: localStorage.token
			}));
			terminalServers.set([...existingDirectTerminals, ...systemEntries] as any);
		}
	};

	const addTerminalConnection = (server: TerminalConnection) => {
		terminalConnections = [
			...terminalConnections,
			{ ...server, id: server.id ?? crypto.randomUUID() }
		];
		saveTerminalServers();
	};

	const updateTerminalConnection = (idx: number, updated: TerminalConnection) => {
		terminalConnections = terminalConnections.map((c, i) =>
			i === idx ? { ...c, ...updated, id: updated.id ?? c.id } : c
		);
		saveTerminalServers();
	};

	const removeTerminalConnection = (idx: number) => {
		terminalConnections = terminalConnections.filter((_, i) => i !== idx);
		saveTerminalServers();
	};

	onMount(async () => {
		const res = await getToolServerConnections(localStorage.token);
		servers = res.TOOL_SERVER_CONNECTIONS as ToolServerConnection[];

		try {
			const terminalRes = await getTerminalServerConnections(localStorage.token);
			if (terminalRes?.TERMINAL_SERVER_CONNECTIONS) {
				terminalConnections = terminalRes.TERMINAL_SERVER_CONNECTIONS as TerminalConnection[];
			}
		} catch {
			// Not configured yet
		}
	});
</script>

<AddToolServerModal bind:show={showConnectionModal} onSubmit={addConnectionHandler} />

<ConfirmDialog
	bind:show={showManagedMCPRemoveConfirm}
	title={$i18n.t('Remove local MCP service?')}
	message={$i18n.t(
		'This stops the managed service and removes its registration. The package files remain on disk and can be discovered again later.'
	)}
	confirmLabel={$i18n.t('Remove')}
	on:confirm={removeManagedMCP}
/>

<AddTerminalServerModal
	bind:show={showAddTerminalModal}
	edit={editTerminalIdx !== null}
	connection={editTerminalIdx !== null ? terminalConnections[editTerminalIdx] : null}
	onSubmit={(c: TerminalConnection) => {
		if (editTerminalIdx !== null) {
			updateTerminalConnection(editTerminalIdx, c);
			editTerminalIdx = null;
		} else {
			addTerminalConnection(c);
		}
	}}
	onDelete={() => {
		if (editTerminalIdx !== null) {
			removeTerminalConnection(editTerminalIdx);
			editTerminalIdx = null;
		}
	}}
/>

<form
	class="flex h-full flex-col justify-between text-sm"
	on:submit|preventDefault={() => {
		updateHandler();
	}}
>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$i18n.t('Integrations')}</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if servers !== null}
			<AdminSettingSection title={$i18n.t('Local MCP Services')} first>
				<div>
					<div class="mb-2 flex items-center justify-between gap-3">
						<div class="text-xs text-gray-600 dark:text-gray-400">
							{$i18n.t('Services installed on this Open WebUI server')}
						</div>
						<button
							class="shrink-0 rounded-full border border-gray-200 px-3 py-1 text-xs font-medium hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-800"
							type="button"
							disabled={managedMCPLoading}
							on:click={discoverManagedMCP}
						>
							{managedMCPLoading ? $i18n.t('Discovering…') : $i18n.t('Discover Services')}
						</button>
					</div>

					{#if managedMCPDiscovery}
						<div class="flex flex-col gap-2">
							{#each managedMCPDiscovery.services as service (service.package_path)}
								<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-800">
									<div class="flex items-start justify-between gap-3">
										<div class="min-w-0">
											<div class="flex items-center gap-2">
												<span class="truncate text-xs font-medium text-gray-800 dark:text-gray-200"
													>{service.name}</span
												>
												<span class="text-[0.625rem] text-gray-400">v{service.version}</span>
											</div>
											{#if service.description}
												<div class="mt-0.5 text-[0.6875rem] text-gray-500 dark:text-gray-400">
													{service.description}
												</div>
											{/if}
											<div
												class="mt-1 truncate font-mono text-[0.625rem] text-gray-400"
												title={service.package_path}
											>
												{service.package_path}
											</div>
										</div>

										{#if service.discovery_state === 'available' && service.security.profile === 'confined'}
											<button
												class="shrink-0 rounded-full bg-black px-3 py-1 text-xs text-white disabled:opacity-50 dark:bg-white dark:text-black"
												type="button"
												disabled={registeringManagedMCP !== null}
												on:click={() => registerManagedMCP(service)}
											>
												{registeringManagedMCP === service.package_path
													? $i18n.t('Adding…')
													: $i18n.t('Add')}
											</button>
										{:else if service.discovery_state === 'available'}
											<span class="shrink-0 text-[0.6875rem] text-amber-600 dark:text-amber-400">
												{$i18n.t('Privileged setup required')}
											</span>
										{:else if service.discovery_state === 'registered'}
											<div class="flex shrink-0 items-center gap-2">
												<span class="text-[0.6875rem] text-green-600 dark:text-green-400">
													{$i18n.t('Registered')} · {service.runtime_state ?? $i18n.t('stopped')}
												</span>
												<button
													class="rounded-full border border-red-200 px-3 py-1 text-xs text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950/30"
													type="button"
													disabled={removingManagedMCP !== null}
													on:click={() => {
														managedMCPPendingRemoval = service;
														showManagedMCPRemoveConfirm = true;
													}}
												>
													{removingManagedMCP === service.id
														? $i18n.t('Removing…')
														: $i18n.t('Remove')}
												</button>
											</div>
										{:else}
											<span class="shrink-0 text-[0.6875rem] text-red-600 dark:text-red-400">
												{$i18n.t('ID conflict')}
											</span>
										{/if}
									</div>
								</div>
							{/each}
						</div>

						{#if managedMCPDiscovery.services.length === 0}
							<div class="text-[0.6875rem] text-gray-400">
								{$i18n.t('No local MCP services were found in the configured package roots.')}
							</div>
						{/if}

						{#if managedMCPDiscovery.errors.length > 0}
							<div
								class="mt-2 rounded-lg bg-red-50 p-2 text-[0.6875rem] text-red-700 dark:bg-red-950/30 dark:text-red-300"
							>
								<div class="font-medium">{$i18n.t('Some packages could not be loaded:')}</div>
								{#each managedMCPDiscovery.errors as error}
									<div class="mt-1 break-all">{error.package_path}: {error.error}</div>
								{/each}
							</div>
						{/if}
					{/if}

					<div class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{$i18n.t('Discovery searches the package roots configured on the local MCP runtime.')}
					</div>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Tools')}>
				<div>
					<div class="mb-2 flex items-center justify-between">
						<div class="text-xs text-gray-600 dark:text-gray-400">
							{$i18n.t('External Tool Servers')}
						</div>

						<Tooltip content={$i18n.t(`Add Connection`)}>
							<button
								class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-black/5 hover:text-gray-900 dark:text-gray-600 dark:hover:bg-white/5 dark:hover:text-white"
								on:click={() => {
									showConnectionModal = true;
								}}
								type="button"
							>
								<Plus />
							</button>
						</Tooltip>
					</div>

					<div class="flex flex-col gap-1">
						{#each servers ?? [] as server, idx}
							<Connection
								bind:connection={server}
								onSubmit={() => {
									updateHandler();
								}}
								onDelete={() => {
									servers = (servers ?? []).filter((_, i) => i !== idx);
									updateHandler();
								}}
							/>
						{/each}
					</div>

					{#if (servers ?? []).length === 0}
						<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{$i18n.t('No tool server connections configured.')}
						</div>
					{/if}

					<div class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{$i18n.t('Connect to your own OpenAPI compatible external tool servers.')}
					</div>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Terminal')}>
				<div>
					<div class="mb-2 flex items-center justify-between">
						<div class="text-xs text-gray-600 dark:text-gray-400">{$i18n.t('Open Terminal')}</div>

						<Tooltip content={$i18n.t('Add Connection')}>
							<button
								class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-black/5 hover:text-gray-900 dark:text-gray-600 dark:hover:bg-white/5 dark:hover:text-white"
								on:click={() => {
									editTerminalIdx = null;
									showAddTerminalModal = true;
								}}
								type="button"
							>
								<Plus />
							</button>
						</Tooltip>
					</div>

					<div class="flex flex-col gap-1.5">
						{#each terminalConnections as connection, idx}
							<div class="flex w-full gap-2 items-center">
								<Tooltip className="w-full relative" content={''} placement="top-start">
									<div class="flex w-full">
										<div
											class="flex-1 relative flex gap-1.5 items-center {connection?.enabled ===
											false
												? 'opacity-50'
												: ''}"
										>
											<Tooltip content={$i18n.t('Terminal')}>
												<Cloud className="size-4" strokeWidth="1.5" />
											</Tooltip>

											<div
												class="outline-hidden w-full bg-transparent text-xs text-gray-700 dark:text-gray-300"
											>
												{connection.name || connection.url || $i18n.t('New Terminal')}
											</div>
										</div>
									</div>
								</Tooltip>

								<div class="flex gap-1 items-center">
									<Tooltip content={$i18n.t('Configure')}>
										<button
											class="self-center p-1 bg-transparent hover:bg-black/5 dark:hover:bg-white/5 rounded-lg transition"
											on:click={() => {
												editTerminalIdx = idx;
												showAddTerminalModal = true;
											}}
											type="button"
										>
											<Cog6 />
										</button>
									</Tooltip>

									<Tooltip
										content={connection?.enabled !== false
											? $i18n.t('Enabled')
											: $i18n.t('Disabled')}
									>
										<Switch
											state={connection?.enabled !== false}
											on:change={() => {
												terminalConnections = terminalConnections.map((c, i) =>
													i === idx ? { ...c, enabled: !(c?.enabled !== false) } : c
												);
												saveTerminalServers();
											}}
										/>
									</Tooltip>
								</div>
							</div>
						{/each}
					</div>

					{#if terminalConnections.length === 0}
						<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{$i18n.t('No terminal connections configured.')}
						</div>
					{/if}

					<div class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{$i18n.t(
							'Connect to Open Terminal instances. Admins and users granted access can use file browsing and terminal tools through these servers.'
						)}
					</div>
					<a
						class="mt-0.5 block text-[0.6875rem] text-gray-500 underline hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300"
						href="https://github.com/open-webui/open-terminal"
						target="_blank">{$i18n.t('Learn more about Open Terminal')} ↗</a
					>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Knowledge')}>
				<ExternalKnowledge />
			</AdminSettingSection>
		{:else}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-6 text-sm font-normal">
		<button
			class="px-3.5 py-1.5 text-sm font-normal bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
