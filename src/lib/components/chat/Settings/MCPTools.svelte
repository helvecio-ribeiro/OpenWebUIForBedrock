<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { getMCPTools } from '$lib/apis/mcp';
	import { updateUserSettings } from '$lib/apis/users';
	import { settings, tools } from '$lib/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n = getContext('i18n');
	let loading = true;
	let saving = false;
	let catalog: any[] = [];
	let selectedIds: string[] = [];

	const load = async () => {
		loading = true;
		try {
			catalog = await getMCPTools(localStorage.token);
			tools.set(catalog);
			selectedIds = ($settings?.mcpServerIds ?? []).filter((id: string) =>
				catalog.some((server) => server.id === id)
			);
		} catch (error) {
			toast.error(error instanceof Error ? error.message : $i18n.t('Failed to load tools'));
		} finally {
			loading = false;
		}
	};

	const toggle = async (serverId: string) => {
		selectedIds = selectedIds.includes(serverId)
			? selectedIds.filter((id) => id !== serverId)
			: [...selectedIds, serverId];
		const uiSettings = { ...$settings, mcpServerIds: selectedIds };
		settings.set(uiSettings);
		saving = true;
		try {
			await updateUserSettings(localStorage.token, { ui: uiSettings });
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : $i18n.t('Failed to save tool selection')
			);
			await load();
		} finally {
			saving = false;
		}
	};

	onMount(load);
</script>

<div class="flex h-full flex-col">
	<div class="mb-4">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$i18n.t('Tools')}</h2>
		<p class="mt-1 text-xs text-gray-500">
			{$i18n.t('Choose the MCP services that are available in your chats.')}
		</p>
	</div>

	{#if loading}
		<div class="flex justify-center py-8"><Spinner /></div>
	{:else if catalog.length === 0}
		<div class="rounded-xl border border-gray-100 p-4 text-sm text-gray-500 dark:border-gray-800">
			{$i18n.t('No MCP services are available. Ask an administrator to install one.')}
		</div>
	{:else}
		<div class="flex flex-col gap-2">
			{#each catalog as server (server.id)}
				<button
					type="button"
					class="flex w-full items-center justify-between gap-4 rounded-xl border border-gray-100 px-4 py-3 text-left hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
					disabled={saving}
					on:click={() => toggle(server.id)}
				>
					<div class="min-w-0">
						<div class="truncate text-sm font-medium">{server.name}</div>
						<div class="mt-0.5 text-xs text-gray-500">{server.meta?.description ?? ''}</div>
					</div>
					<Switch state={selectedIds.includes(server.id)} />
				</button>
			{/each}
		</div>
	{/if}
</div>
