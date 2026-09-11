<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { fly } from 'svelte/transition';

	import { user, tools as _tools, skills as _skills, settings } from '$lib/stores';

	import { initiateOAuthRedirect } from '$lib/apis/configs';
	import { deleteOAuthSession } from '$lib/apis/auths';
	import { getMCPTools } from '$lib/apis/mcp';
	import { getSkills } from '$lib/apis/skills';
	import { updateUserSettings } from '$lib/apis/users';

	import { toast } from 'svelte-sonner';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';
	import Cube from '$lib/components/icons/Cube.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Photo from '$lib/components/icons/Photo.svelte';
	import Terminal from '$lib/components/icons/Terminal.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import LinkSlash from '$lib/components/icons/LinkSlash.svelte';

	const i18n = getContext('i18n');

	export let selectedMcpServerIds: string[] = [];
	export let selectedSkillIds: string[] = [];

	export let selectedModels: string[] = [];
	export let fileUploadCapableModels: string[] = [];

	export let showWebSearchButton = false;
	export let webSearchEnabled = false;
	export let showImageGenerationButton = false;
	export let imageGenerationEnabled = false;
	export let showCodeInterpreterButton = false;
	export let codeInterpreterEnabled = false;

	export let onClose: Function;
	export let onWebSearchToggle: Function = () => {};
	// svelte-ignore export_let_unused\n	export let closeOnOutsideClick = true;

	let show = false;
	let tab = '';

	let tools = null;
	let skills = null;

	const persistSelectedTools = async () => {
		const uiSettings = {
			...$settings,
			mcpServerIds: [...new Set(selectedMcpServerIds)]
		};
		settings.set(uiSettings);

		await updateUserSettings(localStorage.token, { ui: uiSettings }).catch((err) => {
			console.error('Failed to persist selected MCP servers', err);
			toast.error($i18n.t('Failed to save tool selection'));
		});
	};

	$: if (show) {
		init();
	}

	let fileUploadEnabled = true;
	$: fileUploadEnabled =
		fileUploadCapableModels.length === selectedModels.length &&
		($user?.role === 'admin' || $user?.permissions?.chat?.file_upload);

	const init = async () => {
		if ($_tools === null || $_tools.length === 0) {
			const catalog = await getMCPTools(localStorage.token);
			if (catalog.length > 0) await _tools.set(catalog);
		}

		if ($_tools) {
			tools = $_tools.reduce((a, tool) => {
				a[tool.id] = {
					name: tool.name,
					description: tool.meta.description,
					enabled: selectedMcpServerIds.includes(tool.id),
					...tool
				};
				return a;
			}, {});
		}

		selectedMcpServerIds = selectedMcpServerIds.filter((id) => Object.keys(tools).includes(id));

		if ($_skills === null) {
			await _skills.set(await getSkills(localStorage.token));
		}

		if ($_skills) {
			skills = $_skills
				.filter((skill) => skill.is_active)
				.reduce((a, skill) => {
					a[skill.id] = {
						name: skill.name,
						description: skill.description,
						enabled: selectedSkillIds.includes(skill.id),
						...skill
					};
					return a;
				}, {});
		}

		selectedSkillIds = selectedSkillIds.filter((id) => Object.keys(skills ?? {}).includes(id));
	};
</script>

<Dropdown
	bind:show
	onOpenChange={(state) => {
		if (state === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('Integrations')} placement="top">
		<slot />
	</Tooltip>
	<div slot="content">
		<DropdownMenu
			className="min-w-70 max-w-70 max-h-72 overflow-y-auto overflow-x-hidden scrollbar-thin"
		>
			{#if tab === ''}
				<div in:fly={{ x: -20, duration: 150 }}>
					{#if tools}
						{#if Object.keys(tools).length > 0}
							<button
								class="flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[13px] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
								on:click={() => {
									tab = 'tools';
								}}
							>
								<Wrench />

								<div class="flex items-center w-full justify-between">
									<div class=" line-clamp-1">
										{$i18n.t('Tools')}
										<span class="ml-0.5 text-gray-500">{Object.keys(tools).length}</span>
									</div>

									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
							</button>
						{/if}

						{#if skills && Object.keys(skills).length > 0}
							<button
								class="flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[13px] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
								on:click={() => {
									tab = 'skills';
								}}
							>
								<Cube className="size-3.5" strokeWidth="1.75" />

								<div class="flex items-center w-full justify-between">
									<div class=" line-clamp-1">
										{$i18n.t('Skills')}
										<span class="ml-0.5 text-gray-500">{Object.keys(skills).length}</span>
									</div>

									<div class="text-gray-500">
										<ChevronRight />
									</div>
								</div>
							</button>
						{/if}
					{:else}
						<div class="py-4">
							<Spinner />
						</div>
					{/if}

					{#if showWebSearchButton}
						<Tooltip content={$i18n.t('Search the internet')} placement="top-start">
							<button
								class="flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[13px] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
								aria-pressed={webSearchEnabled}
								aria-label={webSearchEnabled
									? $i18n.t('Disable Web Search')
									: $i18n.t('Enable Web Search')}
								on:click={() => {
									webSearchEnabled = !webSearchEnabled;
									onWebSearchToggle(webSearchEnabled);
								}}
							>
								<div class="flex-1 truncate">
									<div class="flex flex-1 gap-2 items-center">
										<div class="shrink-0">
											<GlobeAlt />
										</div>

										<div class=" truncate">{$i18n.t('Web Search')}</div>
									</div>
								</div>

								<div class=" shrink-0">
									<Switch
										state={webSearchEnabled}
										on:change={async (e) => {
											const state = e.detail;
											await tick();
										}}
									/>
								</div>
							</button>
						</Tooltip>
					{/if}

					{#if showImageGenerationButton}
						<Tooltip content={$i18n.t('Generate an image')} placement="top-start">
							<button
								class="flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[13px] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
								aria-pressed={imageGenerationEnabled}
								aria-label={imageGenerationEnabled
									? $i18n.t('Disable Image Generation')
									: $i18n.t('Enable Image Generation')}
								on:click={() => {
									imageGenerationEnabled = !imageGenerationEnabled;
								}}
							>
								<div class="flex-1 truncate">
									<div class="flex flex-1 gap-2 items-center">
										<div class="shrink-0">
											<Photo className="size-3.5" strokeWidth="1.5" />
										</div>

										<div class=" truncate">{$i18n.t('Image')}</div>
									</div>
								</div>

								<div class=" shrink-0">
									<Switch
										state={imageGenerationEnabled}
										on:change={async (e) => {
											const state = e.detail;
											await tick();
										}}
									/>
								</div>
							</button>
						</Tooltip>
					{/if}

					{#if showCodeInterpreterButton}
						<Tooltip content={$i18n.t('Execute code for analysis')} placement="top-start">
							<button
								class="flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[13px] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
								aria-pressed={codeInterpreterEnabled}
								aria-label={codeInterpreterEnabled
									? $i18n.t('Disable Code Interpreter')
									: $i18n.t('Enable Code Interpreter')}
								on:click={() => {
									codeInterpreterEnabled = !codeInterpreterEnabled;
								}}
							>
								<div class="flex-1 truncate">
									<div class="flex flex-1 gap-2 items-center">
										<div class="shrink-0">
											<Terminal className="size-3.5" strokeWidth="1.75" />
										</div>

										<div class=" truncate">{$i18n.t('Code Interpreter')}</div>
									</div>
								</div>

								<div class=" shrink-0">
									<Switch
										state={codeInterpreterEnabled}
										on:change={async (e) => {
											const state = e.detail;
											await tick();
										}}
									/>
								</div>
							</button>
						</Tooltip>
					{/if}
				</div>
			{:else if tab === 'tools' && tools}
				<div in:fly={{ x: 20, duration: 150 }}>
					<button
						class="flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[13px] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Tools')}
								<span class="ml-0.5 text-gray-500">{Object.keys(tools).length}</span>
							</div>
						</div>
					</button>

					{#each Object.keys(tools) as toolId}
						<button
							class="relative flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[13px] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
							on:click={async (e) => {
								if (!(tools[toolId]?.authenticated ?? true)) {
									e.preventDefault();

									initiateOAuthRedirect({
										id: toolId,
										serverId: toolId,
										authType: 'mcp'
									});
								} else {
									tools[toolId].enabled = !tools[toolId].enabled;

									const state = tools[toolId].enabled;
									await tick();

									if (state) {
										selectedMcpServerIds = [...selectedMcpServerIds, toolId];
									} else {
										selectedMcpServerIds = selectedMcpServerIds.filter((id) => id !== toolId);
									}
									await persistSelectedTools();
								}
							}}
						>
							{#if !(tools[toolId]?.authenticated ?? true)}
								<!-- make it slighly darker and not clickable -->
								<div class="absolute inset-0 opacity-50 rounded-xl cursor-pointer z-10"></div>
							{/if}
							<div class="flex-1 truncate">
								<div class="flex flex-1 gap-2 items-center">
									<Tooltip content={tools[toolId]?.name ?? ''} placement="top">
										<div class="shrink-0">
											<Wrench />
										</div>
									</Tooltip>
									<Tooltip content={tools[toolId]?.description ?? ''} placement="top-start">
										<div class=" truncate">{tools[toolId].name}</div>
									</Tooltip>
								</div>
							</div>

							{#if tools[toolId]?.authenticated ?? true}
								<div class="shrink-0">
									<Tooltip content={$i18n.t('Disconnect OAuth')}>
										<button
											class="self-center w-fit text-sm text-gray-600 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition rounded-full"
											type="button"
											on:click={async (e) => {
												e.stopPropagation();
												e.preventDefault();

												const provider = `mcp:${toolId}`;

												try {
													await deleteOAuthSession(localStorage.token, provider);
													toast.success($i18n.t('OAuth session disconnected'));

													// Refresh tools to update authenticated state
													_tools.set(await getMCPTools(localStorage.token));
													selectedMcpServerIds = selectedMcpServerIds.filter((id) => id !== toolId);
													await persistSelectedTools();
													await init();
												} catch (err) {
													toast.error(err ?? $i18n.t('Failed to disconnect'));
												}
											}}
										>
											<LinkSlash className="size-3.5" />
										</button>
									</Tooltip>
								</div>
							{/if}

							<div class=" shrink-0">
								<Switch state={tools[toolId].enabled} />
							</div>
						</button>
					{/each}
				</div>
			{:else if tab === 'skills' && skills}
				<div in:fly={{ x: 20, duration: 150 }}>
					<button
						class="flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[13px] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
						on:click={() => {
							tab = '';
						}}
					>
						<ChevronLeft />

						<div class="flex items-center w-full justify-between">
							<div>
								{$i18n.t('Skills')}
								<span class="ml-0.5 text-gray-500">{Object.keys(skills).length}</span>
							</div>
						</div>
					</button>

					{#each Object.keys(skills) as skillId}
						<button
							class="relative flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[13px] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40"
							on:click={async () => {
								skills[skillId].enabled = !skills[skillId].enabled;

								const state = skills[skillId].enabled;
								await tick();

								if (state) {
									selectedSkillIds = [...selectedSkillIds, skillId];
								} else {
									selectedSkillIds = selectedSkillIds.filter((id) => id !== skillId);
								}
							}}
						>
							<div class="flex-1 truncate">
								<div class="flex flex-1 gap-2 items-center">
									<Tooltip content={skills[skillId]?.name ?? ''} placement="top">
										<div class="shrink-0">
											<Cube className="size-3.5" strokeWidth="1.75" />
										</div>
									</Tooltip>
									<Tooltip content={skills[skillId]?.description ?? ''} placement="top-start">
										<div class=" truncate">{skills[skillId].name}</div>
									</Tooltip>
								</div>
							</div>

							<div class=" shrink-0">
								<Switch state={skills[skillId].enabled} />
							</div>
						</button>
					{/each}
				</div>
			{/if}
		</DropdownMenu>
	</div>
</Dropdown>
