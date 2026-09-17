<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		deleteWebPanel,
		deleteWebPanels,
		getWebPanels,
		updateWebPanel,
		type WebPanel
	} from '$lib/apis/webPanels';
	import GarbageBinIcon from '$lib/components/icons/GarbageBin.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import Link from '$lib/components/icons/Link.svelte';
	import Pencil from '$lib/components/icons/Pencil.svelte';
	import Trash from '$lib/components/icons/Trash.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import SidebarSection from '../Sidebar/Section.svelte';

	let panels: WebPanel[] = [];
	let loading = true;
	let selectionMode = false;
	let selectedIds: string[] = [];
	let pending = false;
	let openMenuPanelId: string | null = null;
	let panelPendingDeletion: WebPanel | null = null;
	let showBatchDeleteConfirm = false;

	$: activeId = $page.url.pathname.startsWith('/web-panels/') ? $page.params.id : null;

	const load = async () => {
		loading = true;
		panels = await getWebPanels(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return [];
		});
		loading = false;
	};

	const removeOne = async (id: string) => {
		await deleteWebPanel(localStorage.token, id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		panels = panels.filter((panel) => panel.id !== id);
		if (activeId === id) goto('/');
	};

	const requestDelete = (panel: WebPanel) => {
		openMenuPanelId = null;
		panelPendingDeletion = panel;
	};

	const removeSelected = async () => {
		if (!selectedIds.length || pending) return;
		pending = true;
		const ids = [...selectedIds];
		const result = await deleteWebPanels(localStorage.token, ids).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		pending = false;
		if (!result) return;
		panels = panels.filter((panel) => !ids.includes(panel.id));
		selectionMode = false;
		selectedIds = [];
		if (activeId && ids.includes(activeId)) goto('/');
	};

	const toggle = (id: string) => {
		selectedIds = selectedIds.includes(id)
			? selectedIds.filter((selected) => selected !== id)
			: [...selectedIds, id];
	};

	const rename = async (panel: WebPanel) => {
		const title = prompt('Rename browser tab', panel.title)?.trim();
		if (!title || title === panel.title) return;
		const updated = await updatePanel(panel.id, { title });
		if (updated) panels = panels.map((item) => (item.id === panel.id ? updated : item));
	};

	const updatePanel = async (id: string, values: { title?: string; url?: string }) => {
		return updateWebPanel(localStorage.token, id, values).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
	};

	const copyUrl = async (panel: WebPanel) => {
		if (!panel.url) {
			toast.error('This tab does not have a URL yet');
			return;
		}
		await navigator.clipboard.writeText(panel.url);
		toast.success('URL copied');
	};

	onMount(() => {
		load();
		window.addEventListener('web-panels-changed', load);
	});
	onDestroy(() => window.removeEventListener('web-panels-changed', load));
</script>

<ConfirmDialog
	show={panelPendingDeletion !== null}
	title="Delete browser tab?"
	onConfirm={async () => {
		const panel = panelPendingDeletion;
		panelPendingDeletion = null;
		if (panel) await removeOne(panel.id);
	}}
	on:cancel={() => (panelPendingDeletion = null)}
>
	<div class="flex-1 line-clamp-3 text-sm text-gray-500">
		This will delete <span class="font-normal"
			>{panelPendingDeletion?.title ?? 'this browser tab'}</span
		>.
	</div>
</ConfirmDialog>

<ConfirmDialog
	bind:show={showBatchDeleteConfirm}
	title="Delete selected browser tabs?"
	on:confirm={removeSelected}
>
	<div class="text-sm text-gray-500">
		This will permanently delete {selectedIds.length} selected browser
		{selectedIds.length === 1 ? 'tab' : 'tabs'}.
	</div>
</ConfirmDialog>

<SidebarSection id="sidebar-web-panels" name="Browser" className="mt-2" dragAndDrop={false}>
	<svelte:fragment slot="action">
		{#if selectionMode}
			<div class="flex items-center gap-1">
				<button
					class="flex size-7 items-center justify-center rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40"
					disabled={!selectedIds.length || pending}
					on:click|stopPropagation={() => (showBatchDeleteConfirm = true)}
					aria-label="Delete selected panels"
				>
					<GarbageBinIcon className="size-3.5" />
				</button>
				<button
					class="px-1 text-[11px] text-gray-500"
					on:click|stopPropagation={() => {
						selectionMode = false;
						selectedIds = [];
					}}>Cancel</button
				>
			</div>
		{:else if panels.length}
			<button
				class="px-1 text-[11px] text-gray-500"
				on:click|stopPropagation={() => (selectionMode = true)}>Select</button
			>
		{/if}
	</svelte:fragment>

	<div class="flex flex-col px-1 pt-1">
		{#if loading}
			<div class="px-2 py-1 text-xs text-gray-400">Loading...</div>
		{:else if !panels.length}
			<div class="px-2 py-1 text-xs text-gray-400">No tabs open</div>
		{:else}
			{#each panels as panel (panel.id)}
				<div
					class="group flex items-center gap-1 rounded-lg {activeId === panel.id
						? 'bg-gray-100 dark:bg-gray-900'
						: 'hover:bg-gray-100 dark:hover:bg-gray-900'}"
				>
					{#if selectionMode}
						<input
							class="ml-2 size-4 shrink-0"
							type="checkbox"
							checked={selectedIds.includes(panel.id)}
							on:change={() => toggle(panel.id)}
							aria-label={`Select ${panel.title}`}
						/>
					{/if}
					<button
						class="min-w-0 flex-1 truncate px-2 py-1.5 text-left text-sm"
						on:click={() => (selectionMode ? toggle(panel.id) : goto(`/web-panels/${panel.id}`))}
						title={panel.url || panel.title}
					>
						{panel.title}
					</button>
					{#if !selectionMode}
						<div
							class="mr-1 shrink-0 {openMenuPanelId === panel.id
								? 'block'
								: 'hidden group-hover:block'}"
						>
							<Dropdown
								align="start"
								show={openMenuPanelId === panel.id}
								onOpenChange={(show) => (openMenuPanelId = show ? panel.id : null)}
							>
								<button
									class="flex size-6 items-center justify-center rounded-md text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-800"
									aria-label={`Options for ${panel.title}`}
								>
									<EllipsisHorizontal className="size-4" />
								</button>
								<div slot="content">
									<DropdownMenu className="min-w-[180px]">
										<button class="panel-menu-item" on:click={() => copyUrl(panel)}
											><Link className="size-4" /> Copy URL</button
										>
										<button class="panel-menu-item" on:click={() => rename(panel)}
											><Pencil className="size-4" /> Rename</button
										>
										<button class="panel-menu-item" on:click={() => requestDelete(panel)}
											><Trash className="size-3.5" strokeWidth="1.5" /> Delete</button
										>
									</DropdownMenu>
								</div>
							</Dropdown>
						</div>
					{/if}
				</div>
			{/each}
		{/if}
	</div>
</SidebarSection>

<style>
	:global(.panel-menu-item) {
		display: flex;
		height: 2rem;
		width: 100%;
		align-items: center;
		gap: 0.5rem;
		border-radius: 0.75rem;
		padding: 0 0.5rem;
		font-size: 0.8125rem;
		white-space: nowrap;
	}
	:global(.panel-menu-item:hover) {
		background: rgb(249 250 251 / 0.7);
	}
	:global(.dark .panel-menu-item:hover) {
		background: rgb(31 41 55 / 0.7);
	}
	:global(.panel-menu-item:disabled) {
		opacity: 0.4;
	}
</style>
